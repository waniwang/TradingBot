"""
EP pre-market scanner.

Queries Alpaca's screener for stocks gapping up significantly in pre-market.
Returns a list of EP candidates sorted by gap %.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_premarket_gappers(
    config: dict,
    client,
    min_gap_pct: float | None = None,
    min_volume: int = 100_000,
    max_results: int = 30,
) -> list[dict[str, Any]]:
    """
    Fetch pre-market gappers via Alpaca screener + snapshots.

    Args:
        config: full app config dict
        client: AlpacaClient instance
        min_gap_pct: minimum % change premarket (defaults to config value)
        min_volume: minimum premarket volume filter
        max_results: cap on returned candidates

    Returns:
        List of dicts with keys: ticker, gap_pct, premarket_price,
        prev_close, premarket_volume, setup_type
    """
    if min_gap_pct is None:
        min_gap_pct = float(config["signals"]["ep_min_gap_pct"])

    # Step 1: Get top gainers from screener
    movers = client.get_market_movers_gainers(top=50)
    if not movers:
        logger.warning("No market movers returned from Alpaca screener")
        return []

    # Step 2: Filter to valid symbols with price > $5
    symbols = []
    for m in movers:
        sym = m["symbol"]
        if len(sym) <= 5 and sym.isalpha() and m["price"] > 5.0:
            symbols.append(sym)

    if not symbols:
        logger.info("Gapper scan: no valid symbols after initial filter")
        return []

    # Step 3: Get snapshots for prev_close + volume
    snapshots = client.get_snapshots(symbols)

    # Step 4: Compute gap_pct from snapshot data and filter
    candidates = []
    for sym in symbols:
        snap = snapshots.get(sym)
        if not snap:
            continue

        prev_close = snap["prev_close"]
        latest_price = snap["latest_price"]
        daily_volume = snap["daily_volume"]

        if prev_close <= 0 or latest_price <= 0:
            continue

        gap_pct = (latest_price - prev_close) / prev_close * 100

        if gap_pct < min_gap_pct:
            continue
        if daily_volume < min_volume:
            continue

        candidates.append({
            "ticker": sym,
            "gap_pct": round(gap_pct, 2),
            "premarket_price": round(latest_price, 2),
            "prev_close": round(prev_close, 2),
            "premarket_volume": daily_volume,
            "setup_type": "episodic_pivot",
        })

    candidates.sort(key=lambda x: x["gap_pct"], reverse=True)
    result = candidates[:max_results]
    logger.info("Gapper scan: found %d EP candidates (min_gap=%.1f%%)", len(result), min_gap_pct)
    return result
