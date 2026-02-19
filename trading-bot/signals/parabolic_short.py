"""
Parabolic Short signal module.

Fires when:
1. Stock has moved up parabolically (>= 50% in 3-5 days)
2. Current price breaks below the Opening Range Low (ORB low)
3. Price fails to reclaim VWAP (VWAP failure confirmation)

NOTE: Requires short-locate access in Moomoo margin account.
Start with long setups first.
"""

from __future__ import annotations

import logging

from signals.base import (
    SignalResult,
    compute_orb_low,
    compute_vwap,
)

logger = logging.getLogger(__name__)

ORB_MINUTES = 5


def check_parabolic_short(
    ticker: str,
    candles_1m: list[dict],          # today's intraday 1m candles (so far)
    daily_closes: list[float],        # recent daily closes (oldest → newest)
    current_price: float,
    current_volume: int,
    config: dict | None = None,
) -> SignalResult | None:
    """
    Evaluate parabolic short conditions for a ticker.

    Args:
        ticker: stock symbol
        candles_1m: today's 1m candles
        daily_closes: recent daily close prices (at least parabolic_min_days + 1)
        current_price: latest trade price
        current_volume: total volume today
        config: optional app config

    Returns:
        SignalResult (side='short') if all conditions met, else None
    """
    min_gain_pct = 50.0
    min_days = 3
    if config:
        sig = config.get("signals", {})
        min_gain_pct = float(sig.get("parabolic_min_gain_pct", 50.0))
        min_days = int(sig.get("parabolic_min_days", 3))

    # 1. Verify the stock has moved up parabolically
    if len(daily_closes) < min_days + 1:
        logger.debug("%s: not enough daily close data (%d)", ticker, len(daily_closes))
        return None

    base_price = daily_closes[-(min_days + 1)]
    recent_high = max(daily_closes[-min_days:])
    gain_pct = (recent_high - base_price) / base_price * 100 if base_price > 0 else 0

    if gain_pct < min_gain_pct:
        logger.debug(
            "%s: gain %.1f%% over %d days below parabolic threshold %.1f%%",
            ticker, gain_pct, min_days, min_gain_pct,
        )
        return None

    if len(candles_1m) < ORB_MINUTES:
        logger.debug("%s: not enough 1m candles for ORB (%d)", ticker, len(candles_1m))
        return None

    # 2. Compute ORB low
    orb_low = compute_orb_low(candles_1m, n_minutes=ORB_MINUTES)

    # 3. Price must break below ORB low
    if current_price >= orb_low:
        logger.debug("%s: price %.2f not below ORB low %.2f", ticker, current_price, orb_low)
        return None

    # 4. VWAP failure: price must be below VWAP
    vwap_series = compute_vwap(candles_1m)
    if vwap_series.empty:
        logger.debug("%s: VWAP computation failed", ticker)
        return None
    current_vwap = float(vwap_series.iloc[-1])

    if current_price >= current_vwap:
        logger.debug(
            "%s: price %.2f not below VWAP %.2f — no VWAP failure",
            ticker, current_price, current_vwap,
        )
        return None

    # Stop: today's high (if price reclaims high, cut immediately)
    day_high = max(c["high"] for c in candles_1m)
    stop_price = day_high

    signal = SignalResult(
        ticker=ticker,
        setup_type="parabolic_short",
        side="short",
        entry_price=current_price,
        stop_price=stop_price,
        orb_low=orb_low,
        notes=(
            f"parabolic +{gain_pct:.1f}% in {min_days}d, "
            f"price<ORB_low {orb_low:.2f}, VWAP_fail at {current_vwap:.2f}"
        ),
    )
    logger.info(
        "PARABOLIC SHORT SIGNAL: %s entry=%.2f stop=%.2f (day_high)",
        ticker, current_price, stop_price,
    )
    return signal
