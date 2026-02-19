"""
Relative strength / momentum ranker.

Ranks a universe of stocks by their 1-month, 3-month, and 6-month
price performance to identify the top 1-2% strongest names.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def compute_rs_score(df: pd.DataFrame) -> dict[str, float]:
    """
    Compute percent change over 1m (21 days), 3m (63 days), 6m (126 days).

    Returns dict with keys: rs_1m, rs_3m, rs_6m, rs_composite.
    """
    if df.empty or len(df) < 21:
        return {}
    closes = df["close"].values
    current = closes[-1]

    def pct(n: int) -> float | None:
        if len(closes) < n + 1:
            return None
        ref = closes[-(n + 1)]
        if ref <= 0:
            return None
        return (current - ref) / ref * 100

    rs_1m = pct(21)
    rs_3m = pct(63)
    rs_6m = pct(126)
    # Composite from available periods only (don't average in fake zeros)
    available = [v for v in (rs_1m, rs_3m, rs_6m) if v is not None]
    rs_composite = sum(available) / len(available) if available else 0.0
    return {
        "rs_1m": round(rs_1m, 2) if rs_1m is not None else 0.0,
        "rs_3m": round(rs_3m, 2) if rs_3m is not None else 0.0,
        "rs_6m": round(rs_6m, 2) if rs_6m is not None else 0.0,
        "rs_composite": round(rs_composite, 2),
    }


def rank_by_momentum(
    tickers: list[str],
    config: dict,
    client,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """
    Rank a list of tickers by composite RS score.

    Args:
        tickers: list of ticker symbols to rank
        config: full app config
        client: AlpacaClient instance
        top_n: return top N results

    Returns:
        List of dicts sorted by rs_composite descending:
        {ticker, rs_1m, rs_3m, rs_6m, rs_composite, setup_type}
    """
    bars_by_symbol = client.get_daily_bars_batch(tickers, days=130)
    results = []

    for ticker in tickers:
        try:
            df = bars_by_symbol.get(ticker)
            if df is None or df.empty:
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
