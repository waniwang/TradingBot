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
    # Weighted composite: 50% 1m, 30% 3m, 20% 6m — favors recent momentum
    # per Qullamaggie's emphasis on stocks "moving NOW"
    weights = [(rs_1m, 0.50), (rs_3m, 0.30), (rs_6m, 0.20)]
    available = [(v, w) for v, w in weights if v is not None]
    if available:
        total_weight = sum(w for _, w in available)
        rs_composite = sum(v * w for v, w in available) / total_weight
    else:
        rs_composite = 0.0
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
    min_price: float = 0,
    min_avg_volume: int = 0,
    progress_cb=None,
) -> list[dict[str, Any]]:
    """
    Rank a list of tickers by composite RS score.

    Args:
        tickers: list of ticker symbols to rank
        config: full app config
        client: AlpacaClient instance
        top_n: return top N results
        min_price: filter out stocks with latest close below this price
        min_avg_volume: filter out stocks with 20-day avg volume below this
        progress_cb: optional callback(processed, total) for download progress

    Returns:
        List of dicts sorted by rs_composite descending:
        {ticker, rs_1m, rs_3m, rs_6m, rs_composite, setup_type}
    """
    bars_by_symbol = client.get_daily_bars_batch(tickers, days=130, progress_cb=progress_cb)
    results = []
    filtered_count = 0

    for ticker in tickers:
        try:
            df = bars_by_symbol.get(ticker)
            if df is None or df.empty:
                continue

            # Price/volume filter using historical data (more reliable than snapshots)
            if min_price > 0 or min_avg_volume > 0:
                latest_close = df["close"].iloc[-1]
                avg_vol_20d = df["volume"].tail(20).mean()
                if latest_close < min_price or avg_vol_20d < min_avg_volume:
                    filtered_count += 1
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
    logger.info(
        "Momentum rank: %d tickers with data, %d filtered (price/vol), %d scored, returning top %d",
        len(bars_by_symbol), filtered_count, len(results), len(top),
    )
    return top
