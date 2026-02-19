"""
EP pre-market scanner.

Queries Polygon.io for stocks gapping up significantly in pre-market.
Returns a list of EP candidates sorted by gap %.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"


def get_premarket_gappers(
    config: dict,
    min_gap_pct: float | None = None,
    min_volume: int = 100_000,
    max_results: int = 30,
) -> list[dict[str, Any]]:
    """
    Fetch pre-market gappers from Polygon.io.

    Args:
        config: full app config dict
        min_gap_pct: minimum % change premarket (defaults to config value)
        min_volume: minimum premarket volume filter
        max_results: cap on returned candidates

    Returns:
        List of dicts with keys: ticker, gap_pct, premarket_price,
        prev_close, premarket_volume, market_cap
    """
    api_key = os.environ.get("POLYGON_API_KEY") or config["polygon"]["api_key"]
    if min_gap_pct is None:
        min_gap_pct = float(config["signals"]["ep_min_gap_pct"])

    # Use the snapshot gainers endpoint — returns top gainers right now
    url = f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/gainers"
    params = {"apiKey": api_key, "include_otc": False}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("Polygon.io gappers request failed: %s", e)
        return []

    tickers = data.get("tickers", [])
    candidates = []

    for t in tickers:
        try:
            day = t.get("day", {})
            prev_close = t.get("prevDay", {}).get("c", 0)
            premarket_price = t.get("lastQuote", {}).get("P", 0) or day.get("o", 0)
            premarket_volume = day.get("v", 0)

            if prev_close <= 0 or premarket_price <= 0:
                continue

            gap_pct = (premarket_price - prev_close) / prev_close * 100

            if gap_pct < min_gap_pct:
                continue
            if premarket_volume < min_volume:
                continue

            candidates.append({
                "ticker": t["ticker"],
                "gap_pct": round(gap_pct, 2),
                "premarket_price": round(premarket_price, 2),
                "prev_close": round(prev_close, 2),
                "premarket_volume": int(premarket_volume),
                "setup_type": "episodic_pivot",
            })
        except (KeyError, TypeError, ZeroDivisionError):
            continue

    candidates.sort(key=lambda x: x["gap_pct"], reverse=True)
    result = candidates[:max_results]
    logger.info("Gapper scan: found %d EP candidates (min_gap=%.1f%%)", len(result), min_gap_pct)
    return result


def get_ticker_prev_close(ticker: str, api_key: str) -> float | None:
    """Fetch previous day's closing price for a single ticker."""
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/prev"
    try:
        resp = requests.get(url, params={"apiKey": api_key}, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return float(results[0]["c"])
    except (requests.RequestException, KeyError, ValueError) as e:
        logger.warning("prev_close fetch failed for %s: %s", ticker, e)
    return None


def get_premarket_quote(ticker: str, api_key: str) -> dict | None:
    """
    Fetch pre-market quote using the last trade/quote snapshot.
    Returns dict with keys: price, volume, or None on failure.
    """
    url = f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
    try:
        resp = requests.get(url, params={"apiKey": api_key}, timeout=10)
        resp.raise_for_status()
        ticker_data = resp.json().get("ticker", {})
        last_quote = ticker_data.get("lastQuote", {})
        day = ticker_data.get("day", {})
        return {
            "price": last_quote.get("P") or day.get("o", 0),
            "volume": day.get("v", 0),
        }
    except (requests.RequestException, KeyError) as e:
        logger.warning("premarket quote failed for %s: %s", ticker, e)
    return None
