"""
Episodic Pivot (EP) signal module.

Fires when:
1. Stock gapped up >= 10% premarket (unexpected catalyst)
2. Current price breaks above the Opening Range High (ORH)
3. Current volume is > 2x the premarket average (or 20d avg if premarket unavailable)
"""

from __future__ import annotations

import logging

from signals.base import (
    SignalResult,
    compute_orh,
    compute_avg_volume,
    compute_atr_from_list,
)

logger = logging.getLogger(__name__)

VOLUME_MULTIPLIER = 2.0
ORH_MINUTES = 5


def check_episodic_pivot(
    ticker: str,
    candles_1m: list[dict],          # today's intraday 1m candles (so far)
    daily_volumes: list[int],         # recent daily volumes (oldest → newest)
    current_price: float,
    current_volume: int,
    gap_pct: float,                   # % gap from previous close to open/premarket
    config: dict | None = None,
    daily_highs: list[float] | None = None,
    daily_lows: list[float] | None = None,
    daily_closes: list[float] | None = None,
) -> SignalResult | None:
    """
    Evaluate episodic pivot conditions for a ticker.

    Args:
        ticker: stock symbol
        candles_1m: list of 1m candle dicts for today
        daily_volumes: recent daily volumes for avg calculation
        current_price: latest trade price
        current_volume: total volume so far today
        gap_pct: percentage gap from prior close (e.g. 15.0 for a 15% gap)
        config: optional app config

    Returns:
        SignalResult if all conditions met, else None
    """
    sig_cfg = config.get("signals", {}) if config else {}
    min_gap = float(sig_cfg.get("ep_min_gap_pct", 10.0))
    vol_mult = float(sig_cfg.get("ep_volume_multiplier", VOLUME_MULTIPLIER))
    orh_min = int(sig_cfg.get("orh_minutes", ORH_MINUTES))

    # 1. Gap must be >= min threshold
    if gap_pct < min_gap:
        logger.debug("%s: gap %.2f%% below threshold %.1f%%", ticker, gap_pct, min_gap)
        return None

    if len(candles_1m) < orh_min:
        logger.debug("%s: not enough 1m candles for ORH (%d)", ticker, len(candles_1m))
        return None

    # 2. Compute ORH
    orh = compute_orh(candles_1m, n_minutes=orh_min)

    # 3. Price must be above ORH
    if current_price <= orh:
        logger.debug("%s: price %.2f not above ORH %.2f", ticker, current_price, orh)
        return None

    # 4. Volume must be elevated
    avg_vol = compute_avg_volume(daily_volumes, period=20)
    vol_ratio = current_volume / avg_vol if avg_vol > 0 else 0.0
    if vol_ratio < vol_mult:
        logger.debug(
            "%s: volume ratio %.2f below EP threshold %.1f",
            ticker, vol_ratio, vol_mult,
        )
        return None

    # Stop: low of day at time of entry
    lod = min(c["low"] for c in candles_1m)
    stop_price = lod

    # Cap stop width at 1.5x ATR (EP allows wider stops than breakout)
    if daily_highs and daily_lows and daily_closes and len(daily_closes) >= 15:
        atr = compute_atr_from_list(daily_highs, daily_lows, daily_closes)
        if atr is not None and (current_price - stop_price) > 1.5 * atr:
            stop_price = current_price - 1.5 * atr

    signal = SignalResult(
        ticker=ticker,
        setup_type="episodic_pivot",
        side="long",
        entry_price=current_price,
        stop_price=stop_price,
        orh=orh,
        gap_pct=gap_pct,
        volume_ratio=round(vol_ratio, 2),
        notes=(
            f"gap={gap_pct:.1f}%, price>{orh:.2f} ORH, "
            f"vol_ratio={vol_ratio:.2f}x, lod_stop={lod:.2f}"
        ),
    )
    logger.info(
        "EP SIGNAL: %s gap=%.1f%% entry=%.2f stop=%.2f",
        ticker, gap_pct, current_price, stop_price,
    )
    return signal
