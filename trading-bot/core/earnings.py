"""Earnings-date lookup for EP scanners.

Tries yfinance first, falls back to Finnhub when an API key is available.

Why a fallback layer: yfinance scrapes Yahoo Finance HTML, and Yahoo periodically
ships layout changes that surface as bare `KeyError(['Earnings Date'])` from
`pandas.DataFrame.dropna`. The 2026-05-13 ep_news_scan failure was caused by
exactly this — one such error wiped the entire scan. Even with the per-ticker
exception handler in `scan_ep_news`, a wholesale yfinance break still produces
zero candidates; a second source narrows the blast radius.

API contract:
- Returns a list[date] of recent earnings dates (most recent first).
- Raises on *both* sources failing (preserves the per-ticker raise-and-skip
  semantics that scanners depend on).
- No silent fallbacks — when one source errors and the other isn't configured,
  we still raise. The decision to tolerate a single ticker failure lives in
  the scanner caller, not here.

The module is read-only and never touches Alpaca, IBKR, or the trading DB.
"""

from __future__ import annotations

import concurrent.futures
import logging
from datetime import date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

PER_SOURCE_TIMEOUT_SEC = 5.0


class EarningsLookupError(RuntimeError):
    """Raised when every configured source fails to return earnings data."""


def _fetch_yfinance(ticker: str, limit: int) -> list[date]:
    """Recent earnings dates via yfinance. Raises on any error.

    yfinance's `get_earnings_dates` does a synchronous HTML scrape that we
    cannot pass a timeout to directly. Wrap in a thread with a wall-clock
    cap so a hanging Yahoo endpoint cannot stall the scanner.
    """
    import yfinance as yf

    def _read() -> list[date]:
        df = yf.Ticker(ticker).get_earnings_dates(limit=limit)
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        out: list[date] = []
        for dt in df.index:
            d = dt.date() if hasattr(dt, "date") else dt
            out.append(d)
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_read)
        return future.result(timeout=PER_SOURCE_TIMEOUT_SEC)


def _fetch_finnhub(ticker: str, api_key: str, window_days: int) -> list[date]:
    """Recent earnings dates via Finnhub. Raises on any error.

    Finnhub's calendar/earnings endpoint returns ALL earnings between `from`
    and `to`. We probe a window symmetric around today (+/- window_days)
    to capture both prior reports and upcoming dates, mirroring yfinance's
    `limit=4` behaviour which spans roughly the trailing year.
    """
    today = date.today()
    resp = requests.get(
        "https://finnhub.io/api/v1/calendar/earnings",
        params={
            "symbol": ticker,
            "from": (today - timedelta(days=window_days)).isoformat(),
            "to": (today + timedelta(days=window_days)).isoformat(),
            "token": api_key,
        },
        timeout=PER_SOURCE_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[date] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("date")
        if not raw:
            continue
        try:
            out.append(date.fromisoformat(str(raw)[:10]))
        except ValueError:
            logger.warning("Finnhub earnings: unparseable date %r for %s", raw, ticker)
    out.sort(reverse=True)
    return out


def fetch_recent_earnings_dates(
    ticker: str,
    finnhub_key: Optional[str] = None,
    limit: int = 4,
    finnhub_window_days: int = 400,
) -> list[date]:
    """Return recent earnings dates for `ticker`, sorted most-recent first.

    Tries yfinance first. If yfinance raises and `finnhub_key` is provided,
    falls back to Finnhub. Raises `EarningsLookupError` if every source
    fails (or yfinance fails and no Finnhub key is set).

    An empty list (no earnings in the window) is a *successful* response —
    we only raise on actual failure to fetch.
    """
    yf_err: Optional[Exception] = None
    try:
        dates = _fetch_yfinance(ticker, limit=limit)
        return dates
    except Exception as e:
        yf_err = e
        logger.info("yfinance earnings lookup failed for %s (%s: %s)", ticker, type(e).__name__, e)

    if not finnhub_key:
        raise EarningsLookupError(
            f"yfinance earnings lookup failed for {ticker} and no Finnhub key configured: "
            f"{type(yf_err).__name__}: {yf_err}"
        )

    try:
        return _fetch_finnhub(ticker, finnhub_key, window_days=finnhub_window_days)
    except Exception as fh_err:
        raise EarningsLookupError(
            f"both yfinance and Finnhub earnings lookups failed for {ticker} — "
            f"yfinance: {type(yf_err).__name__}: {yf_err}; "
            f"finnhub: {type(fh_err).__name__}: {fh_err}"
        ) from fh_err
