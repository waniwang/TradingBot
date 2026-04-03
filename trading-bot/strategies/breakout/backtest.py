"""
Breakout backtest entry logic.

Daily-bar approximation of the live breakout signal.
"""

from __future__ import annotations

import numpy as np

from core.loader import BacktestEntryResult
from signals.base import compute_sma, compute_atr_from_list


def check_entry(
    ticker: str,
    date: str,
    row,
    history: dict,
    bt_config,
) -> BacktestEntryResult | None:
    """
    Check breakout entry conditions on a daily bar.

    Args:
        row: today's bar (open, high, low, close, volume)
        history: dict with closes, highs, lows, volumes, avg_vol lists
        bt_config: BacktestConfig instance

    Returns:
        BacktestEntryResult if entry fires, else None
    """
    cfg = bt_config
    closes = history["closes"]
    highs = history["highs"]
    lows = history["lows"]
    volumes = history["volumes"]
    avg_vol = history.get("avg_vol", 0)
    today_high = float(row["high"])
    today_low = float(row["low"])
    today_volume = float(row["volume"])

    # Need enough history for consolidation check
    if len(closes) < cfg.breakout_consolidation_days + 60:
        return None

    # Prior large move check (30%+ in 2 months before consolidation)
    consol_end = len(closes) - cfg.breakout_consolidation_days
    lookback = min(consol_end, 60)
    if lookback > 10:
        prior = closes[consol_end - lookback : consol_end]
        if len(prior) > 0:
            move = (max(prior) - min(prior)) / min(prior) * 100
            if move < cfg.breakout_prior_move_pct:
                return None

    # ATR contraction in consolidation window
    consol_highs = highs[-cfg.breakout_consolidation_days:]
    consol_lows = lows[-cfg.breakout_consolidation_days:]
    if len(consol_highs) < cfg.breakout_consolidation_days:
        return None

    recent_ranges = [h - l for h, l in zip(consol_highs[-10:], consol_lows[-10:])]
    older_ranges = [h - l for h, l in zip(consol_highs[:10], consol_lows[:10])]
    avg_recent = np.mean(recent_ranges) if recent_ranges else 1
    avg_older = np.mean(older_ranges) if older_ranges else 1
    if avg_older == 0 or avg_recent / avg_older > cfg.breakout_atr_contraction_ratio:
        return None

    # Near both 10d and 20d MA
    ma10 = compute_sma(closes[:-1], 10)  # use prior day for MA
    ma20 = compute_sma(closes[:-1], 20)
    if ma10 is None or ma20 is None:
        return None
    prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
    ma_tol = cfg.breakout_ma_tolerance_pct / 100.0
    if abs(prev_close - ma10) / ma10 > ma_tol:
        return None
    if abs(prev_close - ma20) / ma20 > ma_tol:
        return None

    # Breakout: today's high > max of prior N days
    if len(highs) < cfg.breakout_lookback + 1:
        return None
    prior_highs = highs[-(cfg.breakout_lookback + 1):-1]
    resistance = max(prior_highs)
    if today_high <= resistance:
        return None

    # Volume check
    if avg_vol > 0 and today_volume / avg_vol < cfg.breakout_volume_multiplier:
        return None

    # Entry
    entry_price = resistance  # breakout price
    stop_price = today_low    # LOD

    # ATR cap on stop
    atr = compute_atr_from_list(highs, lows, closes)
    if atr is not None and (entry_price - stop_price) > cfg.breakout_stop_atr_mult * atr:
        stop_price = entry_price - cfg.breakout_stop_atr_mult * atr

    if stop_price >= entry_price:
        return None

    return BacktestEntryResult(
        entry_price=entry_price,
        stop_price=stop_price,
        side="long",
    )
