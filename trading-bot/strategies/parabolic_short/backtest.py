"""
Parabolic Short backtest entry/exit logic.

Daily-bar approximation of the live parabolic short signal.
"""

from __future__ import annotations

from core.loader import BacktestEntryResult
from signals.base import compute_sma


def check_entry(
    ticker: str,
    date: str,
    row,
    history: dict,
    bt_config,
) -> BacktestEntryResult | None:
    """
    Check parabolic short entry conditions on a daily bar.

    Args:
        row: today's bar (open, high, low, close, volume)
        history: dict with closes, highs, lows lists
        bt_config: BacktestConfig instance

    Returns:
        BacktestEntryResult if entry fires, else None
    """
    closes = history["closes"]
    highs = history["highs"]
    today_open = float(row["open"])
    today_high = float(row["high"])
    today_close = float(row["close"])

    if len(closes) < bt_config.parabolic_min_days + 1:
        return None

    base_price = closes[-(bt_config.parabolic_min_days + 1)]
    recent_high = max(highs[-bt_config.parabolic_min_days:])
    if base_price <= 0:
        return None

    gain_pct = (recent_high - base_price) / base_price * 100
    if gain_pct < bt_config.parabolic_min_gain_pct:
        return None

    # Reversal day: red candle
    if today_close >= today_open:
        return None

    # Entry at today's close (proxy for ORB low short)
    entry_price = today_close
    stop_price = today_high

    if stop_price <= entry_price:
        return None

    return BacktestEntryResult(
        entry_price=entry_price,
        stop_price=stop_price,
        side="short",
    )


def check_exit(
    pos,
    date: str,
    row,
    history: dict,
    bt_config,
) -> tuple[float, str] | None:
    """
    Parabolic short exit: cover at MA targets.

    Returns (exit_price, reason) or None for shared exit logic.
    """
    if pos.side != "short":
        return None

    closes = history["closes"]
    close = float(row["close"])
    ma_short = bt_config.parabolic_target_ma_short
    ma_long = bt_config.parabolic_target_ma_long

    if len(closes) < ma_short:
        return None

    # Cover half at short MA (handled as partial by runner)
    ma_s = compute_sma(closes, ma_short)
    if ma_s is not None and close <= ma_s and not pos.partial_exit_done:
        # Return None here — let the runner do partial exit via shared logic
        # but signal that the partial should happen
        return None  # partial exits are handled by the runner's shared logic

    # Cover remainder at long MA
    if len(closes) >= ma_long and pos.partial_exit_done:
        ma_l = compute_sma(closes, ma_long)
        if ma_l is not None and close <= ma_l:
            return (close, "parabolic_target")

    return None
