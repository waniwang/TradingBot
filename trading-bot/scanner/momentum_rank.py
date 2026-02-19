"""
Relative strength / momentum ranker.

Ranks a universe of stocks by their 1-month, 3-month, and 6-month
price performance to identify the top 1-2% strongest names.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"


def get_ohlcv_range(
    ticker: str,
    api_key: str,
    days_back: int,
    multiplier: int = 1,
    timespan: str = "day",
) -> pd.DataFrame:
    """
    Fetch daily OHLCV bars from Polygon.io for a given lookback.

    Returns DataFrame with columns: date, open, high, low, close, volume.
    """
    end = datetime.utcnow().date()
    start = end - timedelta(days=days_back + 10)  # buffer for weekends/holidays

    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range"
        f"/{multiplier}/{timespan}/{start}/{end}"
    )
    params = {"apiKey": api_key, "adjusted": True, "sort": "asc", "limit": 300}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df["date"] = pd.to_datetime(df["t"], unit="ms")
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        return df[["date", "open", "high", "low", "close", "volume"]].tail(days_back)
    except (requests.RequestException, KeyError, ValueError) as e:
        logger.warning("OHLCV fetch failed for %s: %s", ticker, e)
        return pd.DataFrame()


def compute_rs_score(df: pd.DataFrame) -> dict[str, float]:
    """
    Compute percent change over 1m (21 days), 3m (63 days), 6m (126 days).

    Returns dict with keys: rs_1m, rs_3m, rs_6m, rs_composite.
    """
    if df.empty or len(df) < 21:
        return {}
    closes = df["close"].values
    current = closes[-1]

    def pct(n: int) -> float:
        if len(closes) < n + 1:
            return 0.0
        return (current - closes[-(n + 1)]) / closes[-(n + 1)] * 100

    rs_1m = pct(21)
    rs_3m = pct(63)
    rs_6m = pct(126)
    # Equal-weighted composite
    rs_composite = (rs_1m + rs_3m + rs_6m) / 3.0
    return {
        "rs_1m": round(rs_1m, 2),
        "rs_3m": round(rs_3m, 2),
        "rs_6m": round(rs_6m, 2),
        "rs_composite": round(rs_composite, 2),
    }


def rank_by_momentum(
    tickers: list[str],
    config: dict,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """
    Rank a list of tickers by composite RS score.

    Args:
        tickers: list of ticker symbols to rank
        config: full app config
        top_n: return top N results

    Returns:
        List of dicts sorted by rs_composite descending:
        {ticker, rs_1m, rs_3m, rs_6m, rs_composite, setup_type}
    """
    api_key = os.environ.get("POLYGON_API_KEY") or config["polygon"]["api_key"]
    results = []

    for ticker in tickers:
        try:
            df = get_ohlcv_range(ticker, api_key, days_back=130)
            if df.empty:
                continue
            scores = compute_rs_score(df)
            if not scores:
                continue
            results.append({"ticker": ticker, "setup_type": "breakout", **scores})
        except Exception as e:
            logger.warning("momentum_rank failed for %s: %s", ticker, e)
            continue

    results.sort(key=lambda x: x["rs_composite"], reverse=True)
    top = results[:top_n]
    logger.info("Momentum rank: scored %d tickers, returning top %d", len(results), len(top))
    return top


def get_sp1500_tickers(api_key: str, max_tickers: int = 1500) -> list[str]:
    """
    Fetch a broad universe of US large/mid-cap tickers from Polygon.io.

    Uses the tickers endpoint filtered to US stocks, sorted by market cap.
    """
    url = f"{POLYGON_BASE}/v3/reference/tickers"
    params = {
        "apiKey": api_key,
        "market": "stocks",
        "locale": "us",
        "active": True,
        "order": "desc",
        "sort": "market_cap",
        "limit": 250,
    }
    tickers: list[str] = []
    cursor = None

    while len(tickers) < max_tickers:
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error("Failed to fetch ticker universe: %s", e)
            break

        for item in data.get("results", []):
            t = item.get("ticker", "")
            # Skip OTC, warrants, and non-standard tickers
            if t and len(t) <= 5 and t.isalpha():
                tickers.append(t)

        cursor = data.get("next_url", "").split("cursor=")[-1] if "next_url" in data else None
        if not cursor or not data.get("results"):
            break

    logger.info("Fetched %d tickers from Polygon universe", len(tickers))
    return tickers[:max_tickers]
