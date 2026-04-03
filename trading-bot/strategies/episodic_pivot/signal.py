"""
Episodic Pivot (EP) signal module.

Fires when:
1. Stock gapped up >= 10% premarket (unexpected catalyst)
2. Current price breaks above the Opening Range High (ORH)
3. Price is not too far above ORH (max extension guard)
4. Time-of-day-normalized RVOL exceeds threshold
"""

from __future__ import annotations

import logging
import math

from signals.base import (
    SignalResult,
    compute_orh,
    compute_avg_volume,
    compute_atr_from_list,
    compute_rvol,
)

logger = logging.getLogger(__name__)

VOLUME_MULTIPLIER = 2.0
ORH_MINUTES = 5
MAX_EXTENSION_PCT = 5.0  # EP allows wider extension than breakout (gap stocks run more)


def check_episodic_pivot(
    ticker: str,
    candles_1m: list[dict],          # today's intraday 1m candles (so far)
    daily_volumes: list[int],         # recent daily volumes (oldest -> newest)
    current_price: float,
    current_volume: int,
    gap_pct: float,                   # % gap from previous close to open/premarket
    config: dict | None = None,
    daily_highs: list[float] | None = None,
    daily_lows: list[float] | None = None,
    daily_closes: list[float] | None = None,
    minutes_since_open: int | None = None,
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
        config: strategy config dict (strategies.episodic_pivot section)
        daily_highs: recent daily high prices (used for ATR cap)
        daily_lows: recent daily low prices (used for ATR cap)
        daily_closes: recent daily close prices (used for ATR cap)
        minutes_since_open: minutes elapsed since 9:30 ET (for RVOL);
            falls back to len(candles_1m) if not provided

    Returns:
        SignalResult if all conditions met, else None
    """
    cfg = config or {}
    # Support both flat config and nested {"signals": {"ep_min_gap_pct": ...}}
    if "signals" in cfg and isinstance(cfg.get("signals"), dict):
        _sig = cfg["signals"]
        cfg = {
            "min_gap_pct": _sig.get("ep_min_gap_pct", 10.0),
            "volume_multiplier": _sig.get("ep_volume_multiplier", VOLUME_MULTIPLIER),
            "orh_minutes": _sig.get("orh_minutes", ORH_MINUTES),
            "max_extension_pct": _sig.get("ep_max_extension_pct", MAX_EXTENSION_PCT),
            "stop_atr_mult": _sig.get("ep_stop_atr_mult", 1.5),
        }
    min_gap = float(cfg.get("min_gap_pct", 10.0))
    vol_mult = float(cfg.get("volume_multiplier", VOLUME_MULTIPLIER))
    orh_min = int(cfg.get("orh_minutes", ORH_MINUTES))
    max_ext = float(cfg.get("max_extension_pct", MAX_EXTENSION_PCT))

    # Input validation — reject NaN/None/invalid prices
    if current_price is None or not isinstance(current_price, (int, float)) or math.isnan(current_price) or current_price <= 0:
        logger.debug("%s: invalid current_price %s", ticker, current_price)
        return None
    if gap_pct is None or (isinstance(gap_pct, float) and math.isnan(gap_pct)):
        logger.debug("%s: invalid gap_pct %s", ticker, gap_pct)
        return None

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

    # 4. Extension guard — skip if price has run too far above ORH
    extension_pct = (current_price - orh) / orh * 100
    if extension_pct > max_ext:
        logger.debug(
            "%s: price %.2f is %.1f%% above ORH %.2f (max %.1f%%) — too extended",
            ticker, current_price, extension_pct, orh, max_ext,
        )
        return None

    # 5. Time-of-day-normalized RVOL must be elevated
    avg_vol = compute_avg_volume(daily_volumes, period=20)
    elapsed = minutes_since_open if minutes_since_open is not None else len(candles_1m)
    rvol = compute_rvol(current_volume, avg_vol, elapsed)
    if rvol < vol_mult:
        logger.debug(
            "%s: RVOL %.2f below EP threshold %.1f (vol=%d, avg_daily=%d, elapsed=%dmin)",
            ticker, rvol, vol_mult, current_volume, int(avg_vol), elapsed,
        )
        return None

    # Stop: low of day at time of entry
    lod = min(c["low"] for c in candles_1m)
    stop_price = lod

    # Cap stop width at 1.5x ATR (EP allows wider stops than breakout)
    stop_atr_mult = float(cfg.get("stop_atr_mult", 1.5))
    if daily_highs and daily_lows and daily_closes and len(daily_closes) >= 15:
        atr = compute_atr_from_list(daily_highs, daily_lows, daily_closes)
        if atr is not None and (current_price - stop_price) > stop_atr_mult * atr:
            stop_price = current_price - stop_atr_mult * atr

    signal = SignalResult(
        ticker=ticker,
        setup_type="episodic_pivot",
        side="long",
        entry_price=current_price,
        stop_price=stop_price,
        orh=orh,
        gap_pct=gap_pct,
        volume_ratio=round(rvol, 2),
        notes=(
            f"gap={gap_pct:.1f}%, price>{orh:.2f} ORH (+{extension_pct:.1f}%), "
            f"RVOL={rvol:.2f}x, lod_stop={lod:.2f}"
        ),
    )
    logger.info(
        "EP SIGNAL: %s gap=%.1f%% entry=%.2f stop=%.2f RVOL=%.2f",
        ticker, gap_pct, current_price, stop_price, rvol,
    )
    return signal
