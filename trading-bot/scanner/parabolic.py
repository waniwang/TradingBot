"""
Parabolic short scanner.

Identifies stocks that have moved up parabolically (multi-day run-up)
and may be candidates for a short reversal trade.

Reuses the same Alpaca screener (top gainers) as the gapper scanner,
then validates candidates against daily bars for sustained parabolic moves.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def scan_parabolic_candidates(
    config: dict,
    client,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """
    Find stocks with multi-day parabolic moves suitable for short setups.

    Uses Alpaca screener top gainers as the initial universe, then filters
    using daily bars to confirm a sustained parabolic move (>= min_gain_pct
    over >= min_days).

    Args:
        config: full app config dict
        client: AlpacaClient instance
        max_results: cap on returned candidates

    Returns:
        List of dicts with keys: ticker, gain_pct, base_price,
        recent_high, parabolic_days, setup_type
    """
    sig_cfg = config.get("signals", {})
    min_gain_largecap = float(sig_cfg.get("parabolic_min_gain_pct_largecap", 50.0))
    min_gain_smallcap = float(sig_cfg.get("parabolic_min_gain_pct_smallcap", 200.0))
    min_days = int(sig_cfg.get("parabolic_min_days", 3))

    # Step 1: Get top gainers from screener (same source as gapper.py)
    movers = client.get_market_movers_gainers(top=50)
    if not movers:
        logger.info("Parabolic scan: no market movers returned")
        return []

    # Step 2: Filter to valid symbols with price > $5
    symbols = []
    for m in movers:
        sym = m["symbol"]
        if len(sym) <= 5 and sym.isalpha() and m["price"] > 5.0:
            symbols.append(sym)

    if not symbols:
        logger.info("Parabolic scan: no valid symbols after initial filter")
        return []

    # Step 3: Fetch daily bars for candidates
    bars_by_symbol = client.get_daily_bars_batch(symbols, days=min_days + 5)

    # Step 4: Check for multi-day parabolic move using daily highs
    candidates = []
    for sym in symbols:
        df = bars_by_symbol.get(sym)
        if df is None or df.empty or len(df) < min_days + 1:
            continue

        highs = df["high"].values
        closes = df["close"].values

        base_price = closes[-(min_days + 1)]
        recent_high = float(max(highs[-min_days:]))

        if base_price <= 0:
            continue

        gain_pct = (recent_high - base_price) / base_price * 100

        # B1: Use price as proxy for market cap —
        # price > $50 → large-cap threshold, price < $20 → small-cap threshold
        latest_price = float(closes[-1])
        if latest_price > 50:
            threshold = min_gain_largecap
        elif latest_price < 20:
            threshold = min_gain_smallcap
        else:
            # Mid-range: interpolate between thresholds
            t = (latest_price - 20) / 30  # 0 at $20, 1 at $50
            threshold = min_gain_smallcap + t * (min_gain_largecap - min_gain_smallcap)

        if gain_pct < threshold:
            continue

        candidates.append({
            "ticker": sym,
            "gain_pct": round(gain_pct, 2),
            "base_price": round(float(base_price), 2),
            "recent_high": round(recent_high, 2),
            "parabolic_days": min_days,
            "setup_type": "parabolic_short",
        })

    candidates.sort(key=lambda x: x["gain_pct"], reverse=True)
    result = candidates[:max_results]
    logger.info(
        "Parabolic scan: found %d candidates (largecap=%.1f%%, smallcap=%.1f%%, min_days=%d)",
        len(result), min_gain_largecap, min_gain_smallcap, min_days,
    )
    return result
