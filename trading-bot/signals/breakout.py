"""
Breakout signal module.

Fires when:
1. Current price breaks above the Opening Range High (ORH)
2. Price is not too far above ORH (max extension guard)
3. Price is above the 20-day moving average
4. Time-of-day-normalized RVOL exceeds threshold
"""

from __future__ import annotations

import logging
import math

from signals.base import (
    SignalResult,
    compute_orh,
    compute_sma,
    compute_avg_volume,
    compute_atr_from_list,
    compute_rvol,
)

logger = logging.getLogger(__name__)

VOLUME_MULTIPLIER = 1.5
MA_PERIOD = 20
ORH_MINUTES = 5
MAX_EXTENSION_PCT = 3.0  # max % above ORH to prevent chasing


def check_breakout(
    ticker: str,
    candles_1m: list[dict],          # today's intraday 1m candles (so far)
    daily_closes: list[float],        # recent daily close prices (oldest → newest)
    daily_volumes: list[int],         # recent daily volumes (oldest → newest)
    current_price: float,
    current_volume: int,
    config: dict | None = None,
    daily_lows: list[float] | None = None,
    daily_highs: list[float] | None = None,
    minutes_since_open: int | None = None,
) -> SignalResult | None:
    """
    Evaluate breakout conditions for a ticker.

    Args:
        ticker: stock symbol
        candles_1m: list of 1m candle dicts for today (keys: open, high, low, close, volume)
        daily_closes: list of recent daily close prices (at least 20 needed)
        daily_volumes: list of recent daily volumes (at least 20 needed)
        current_price: latest trade price
        current_volume: total volume so far today
        config: optional app config (for overriding defaults)
        daily_lows: recent daily low prices (unused, kept for backward compat)
        daily_highs: recent daily high prices (used for ATR cap calculation)
        minutes_since_open: minutes elapsed since 9:30 ET (for RVOL);
            falls back to len(candles_1m) if not provided

    Returns:
        SignalResult if all conditions met, else None
    """
    # Read configurable thresholds (fall back to module-level constants)
    sig_cfg = config.get("signals", {}) if config else {}
    vol_mult = float(sig_cfg.get("breakout_volume_multiplier", VOLUME_MULTIPLIER))
    orh_min = int(sig_cfg.get("orh_minutes", ORH_MINUTES))
    max_ext = float(sig_cfg.get("breakout_max_extension_pct", MAX_EXTENSION_PCT))

    # Input validation — reject NaN/None/invalid prices
    if current_price is None or not isinstance(current_price, (int, float)) or math.isnan(current_price) or current_price <= 0:
        logger.debug("%s: invalid current_price %s", ticker, current_price)
        return None

    if len(candles_1m) < orh_min:
        logger.debug("%s: not enough 1m candles to compute ORH (%d)", ticker, len(candles_1m))
        return None

    # 1. Compute ORH
    orh = compute_orh(candles_1m, n_minutes=orh_min)

    # 2. Price must be above ORH
    if current_price <= orh:
        logger.debug("%s: price %.2f not above ORH %.2f", ticker, current_price, orh)
        return None

    # 3. Extension guard — skip if price has run too far above ORH
    extension_pct = (current_price - orh) / orh * 100
    if extension_pct > max_ext:
        logger.debug(
            "%s: price %.2f is %.1f%% above ORH %.2f (max %.1f%%) — too extended",
            ticker, current_price, extension_pct, orh, max_ext,
        )
        return None

    # 4. Price must be above the 20d MA
    ma20 = compute_sma(daily_closes, MA_PERIOD)
    if ma20 is None:
        logger.debug("%s: insufficient data for 20d MA", ticker)
        return None
    if current_price <= ma20:
        logger.debug("%s: price %.2f below 20d MA %.2f", ticker, current_price, ma20)
        return None

    # 5. Time-of-day-normalized RVOL must be elevated
    avg_vol = compute_avg_volume(daily_volumes, period=20)
    elapsed = minutes_since_open if minutes_since_open is not None else len(candles_1m)
    rvol = compute_rvol(current_volume, avg_vol, elapsed)
    if rvol < vol_mult:
        logger.debug(
            "%s: RVOL %.2f below threshold %.1f (vol=%d, avg_daily=%d, elapsed=%dmin)",
            ticker, rvol, vol_mult, current_volume, int(avg_vol), elapsed,
        )
        return None

    # Stop: low of day (LOD) — Qullamaggie's rule
    stop_price = min(c["low"] for c in candles_1m)

    # Cap stop width at 1x ATR (never risk more than 1 ATR per share)
    if daily_highs and daily_lows and daily_closes and len(daily_closes) >= 15:
        atr = compute_atr_from_list(daily_highs, daily_lows, daily_closes)
        if atr is not None and (current_price - stop_price) > atr:
            stop_price = current_price - atr

    signal = SignalResult(
        ticker=ticker,
        setup_type="breakout",
        side="long",
        entry_price=current_price,
        stop_price=stop_price,
        orh=orh,
        volume_ratio=round(rvol, 2),
        notes=f"price>{orh:.2f} ORH (+{extension_pct:.1f}%), above 20dMA {ma20:.2f}, RVOL={rvol:.2f}x",
    )
    logger.info("BREAKOUT SIGNAL: %s entry=%.2f stop=%.2f RVOL=%.2f", ticker, current_price, stop_price, rvol)
    return signal
