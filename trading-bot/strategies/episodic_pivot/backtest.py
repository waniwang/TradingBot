"""
Episodic Pivot backtest entry/exit logic.

Daily-bar approximation of the live EP signal.
"""

from __future__ import annotations

from core.loader import BacktestEntryResult
from signals.base import compute_atr_from_list


def check_entry(
    ticker: str,
    date: str,
    row,
    history: dict,
    bt_config,
) -> BacktestEntryResult | None:
    """
    Check EP entry conditions on a daily bar.

    Args:
        row: today's bar (open, high, low, close, volume)
        history: dict with closes, highs, lows, volumes lists
        bt_config: BacktestConfig instance

    Returns:
        BacktestEntryResult if entry fires, else None
    """
    closes = history["closes"]
    highs = history["highs"]
    lows = history["lows"]
    avg_vol = history.get("avg_vol", 0)
    today_open = float(row["open"])
    today_high = float(row["high"])
    today_low = float(row["low"])
    today_volume = float(row["volume"])

    if len(closes) < 2:
        return None

    # Gap check
    prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
    if prev_close <= 0:
        return None
    gap_pct = (today_open - prev_close) / prev_close * 100

    if gap_pct < bt_config.ep_min_gap_pct:
        return None

    # Volume check
    if avg_vol > 0 and today_volume / avg_vol < bt_config.ep_volume_multiplier:
        return None

    # Prior rally filter: reject if already up 50%+ in prior 6 months
    if len(closes) >= 130:
        prior_gain = (closes[-2] - closes[0]) / closes[0] * 100
        if prior_gain >= bt_config.ep_prior_rally_max_pct:
            return None

    # Entry: approximate ORH breakout
    entry_price = today_open + (today_high - today_open) * bt_config.ep_entry_range_fraction
    if entry_price <= 0 or today_high < entry_price:
        return None

    stop_price = today_low

    # ATR cap
    atr = compute_atr_from_list(highs, lows, closes)
    if atr is not None and (entry_price - stop_price) > bt_config.ep_stop_atr_mult * atr:
        stop_price = entry_price - bt_config.ep_stop_atr_mult * atr

    if stop_price >= entry_price:
        return None

    return BacktestEntryResult(
        entry_price=entry_price,
        stop_price=stop_price,
        side="long",
    )
