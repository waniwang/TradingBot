"""
Shared Phase C filter for simulation scripts.

Mirrors production Phase C in strategies/ep_earnings/scanner.py and
strategies/ep_news/scanner.py:
  - market cap >= min_mcap (default $800M earn, $1B news)
  - quoteType == EQUITY
  - earnings today/yesterday (earn) OR no earnings (news)

Uses yfinance .info + .get_earnings_dates() per ticker. These calls are
rate-limited; callers should cache aggressively.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from functools import lru_cache

import yfinance as yf


@lru_cache(maxsize=4096)
def _fetch_info(ticker: str) -> tuple[float, str]:
    """Return (market_cap, quote_type). (0.0, '') on failure."""
    for attempt in range(3):
        try:
            info = yf.Ticker(ticker).info
            return (
                float(info.get("marketCap", 0) or 0),
                str(info.get("quoteType", "") or ""),
            )
        except Exception as e:
            if "Too Many" in str(e) and attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            return (0.0, "")
    return (0.0, "")


@lru_cache(maxsize=4096)
def _fetch_earnings_dates(ticker: str) -> tuple[date, ...]:
    """Return tuple of earnings dates, empty on failure."""
    for attempt in range(3):
        try:
            ed = yf.Ticker(ticker).get_earnings_dates(limit=8)
            if ed is None or ed.empty:
                return ()
            out = []
            for dt in ed.index:
                dd = dt.date() if hasattr(dt, "date") else dt
                out.append(dd)
            return tuple(out)
        except Exception as e:
            if "Too Many" in str(e) and attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            return ()
    return ()


def passes_phase_c(
    ticker: str,
    signal_date,
    kind: str = "earn",
    min_mcap_earn: float = 800_000_000,
    min_mcap_news: float = 1_000_000_000,
) -> tuple[bool, str]:
    """
    Return (passes, reason). `kind` in {"earn", "news"}.

    Mirrors live scanner logic:
      - earn: mcap>=$800M, EQUITY, earnings today/yesterday
      - news: mcap>=$1B, EQUITY, NO earnings today/yesterday (API must succeed)
    """
    mc, qt = _fetch_info(ticker)
    min_mcap = min_mcap_earn if kind == "earn" else min_mcap_news
    if mc > 0 and mc < min_mcap:
        return False, f"mcap_${mc/1e6:.0f}M<${min_mcap/1e6:.0f}M"
    if qt and qt.upper() != "EQUITY":
        return False, f"type={qt}"
    # if info fetch failed entirely (mc=0 and qt=''), be conservative and skip
    if mc == 0 and not qt:
        return False, "info_fetch_failed"

    ed = _fetch_earnings_dates(ticker)
    target = signal_date if isinstance(signal_date, date) else date.fromisoformat(str(signal_date))
    window = {target - timedelta(days=i) for i in range(0, 2)}  # today + yesterday
    has_earnings = any(d in window for d in ed)

    if kind == "earn":
        if not ed:
            return False, "earnings_api_failed"
        if not has_earnings:
            return False, "no_earnings_in_window"
    else:  # news
        if not ed:
            # API failed — conservative in live scanner means skip
            return False, "earnings_api_failed"
        if has_earnings:
            return False, "has_earnings"

    return True, "ok"
