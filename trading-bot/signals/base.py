"""
Base signal infrastructure.

Provides:
- SignalResult dataclass
- ORH / ORB computation
- VWAP computation
- MA helpers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd
import numpy as np


SetupType = str  # Any registered strategy name (was Literal, now open for plugins)
Side = Literal["long", "short"]


@dataclass
class SignalResult:
    """Output from any signal module."""

    ticker: str
    setup_type: SetupType
    side: Side
    entry_price: float          # suggested limit entry
    stop_price: float           # initial stop
    orh: float | None = None    # opening range high used
    orb_low: float | None = None
    gap_pct: float | None = None
    volume_ratio: float | None = None  # current volume / avg volume
    fired_at: datetime = field(default_factory=datetime.utcnow)
    notes: str = ""

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_price - self.stop_price)


def compute_orh(candles: list[dict], n_minutes: int = 5) -> float:
    """
    Compute the Opening Range High from the first n_minutes of 1m candles.

    Args:
        candles: list of dicts with keys: open, high, low, close, volume
        n_minutes: number of minutes in the opening range (default 5)

    Returns:
        float — the high of the opening range
    """
    if not candles:
        raise ValueError("candles list is empty")
    window = candles[:n_minutes]
    return max(c["high"] for c in window)


def compute_orb_low(candles: list[dict], n_minutes: int = 5) -> float:
    """
    Compute the Opening Range Low (used for short entries).

    Returns:
        float — the low of the opening range
    """
    if not candles:
        raise ValueError("candles list is empty")
    window = candles[:n_minutes]
    return min(c["low"] for c in window)


def compute_vwap(candles: list[dict]) -> pd.Series:
    """
    Compute intraday VWAP from 1m candle list.

    VWAP = cumulative(typical_price * volume) / cumulative(volume)
    typical_price = (high + low + close) / 3
    """
    if not candles:
        return pd.Series(dtype=float)
    df = pd.DataFrame(candles)
    df["typical"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["typical"] * df["volume"]
    df["cum_tp_vol"] = df["tp_vol"].cumsum()
    df["cum_vol"] = df["volume"].cumsum()
    df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)
    # Forward-fill any NaN gaps (e.g. zero-volume bars), then back-fill leading NaN
    return df["vwap"].ffill().bfill()


def compute_sma(closes: list[float] | pd.Series, period: int) -> float | None:
    """Return the latest SMA value or None if insufficient data."""
    s = pd.Series(closes) if not isinstance(closes, pd.Series) else closes
    if len(s) < period:
        return None
    result = s.rolling(period).mean().iloc[-1]
    if pd.isna(result):
        return None
    return float(result)


def compute_atr_from_list(
    daily_highs: list[float],
    daily_lows: list[float],
    daily_closes: list[float],
    period: int = 14,
) -> float | None:
    """
    Compute the latest ATR value from plain lists (no DataFrame needed).

    Returns None if insufficient data.
    """
    n = len(daily_closes)
    if n < period + 1 or len(daily_highs) < period + 1 or len(daily_lows) < period + 1:
        return None

    true_ranges = []
    for i in range(1, n):
        h = daily_highs[i]
        l = daily_lows[i]
        prev_c = daily_closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    # Simple moving average of the last `period` true ranges
    atr = sum(true_ranges[-period:]) / period
    return atr


def compute_avg_volume(volumes: list[int] | pd.Series, period: int = 20) -> float:
    """Return the N-period average daily volume. Returns 0.0 if data is empty or NaN."""
    s = pd.Series(volumes) if not isinstance(volumes, pd.Series) else volumes
    if len(s) == 0:
        return 0.0
    if len(s) < period:
        result = s.mean()
    else:
        result = s.tail(period).mean()
    if pd.isna(result):
        return 0.0
    return float(result)


# ---------------------------------------------------------------------------
# Intraday cumulative volume profile (fraction of daily volume by minute)
# ---------------------------------------------------------------------------

# Empirical U-shaped curve: heavy at open, light midday, picks up into close.
# Key anchor points (cumulative fraction of total daily volume by minute offset
# from 9:30 ET):
#   0 min  (9:30) = 0.00    5 min  (9:35) = 0.065   15 min (9:45) = 0.14
#  30 min (10:00) = 0.22   60 min (10:30) = 0.33   120 min(11:30) = 0.50
# 180 min (12:30) = 0.60  240 min (13:30) = 0.70   300 min(14:30) = 0.80
# 360 min (15:30) = 0.92  390 min (16:00) = 1.00
_PROFILE_ANCHORS = [
    (0, 0.00), (5, 0.065), (15, 0.14), (30, 0.22), (60, 0.33),
    (120, 0.50), (180, 0.60), (240, 0.70), (300, 0.80), (360, 0.92),
    (390, 1.00),
]


def _cumulative_volume_fraction(minutes_since_open: int) -> float:
    """
    Return expected cumulative fraction of daily volume at *minutes_since_open*
    minutes after 9:30 ET using linear interpolation of the anchor profile.
    """
    if minutes_since_open <= 0:
        return 0.0
    if minutes_since_open >= 390:
        return 1.0
    for i in range(1, len(_PROFILE_ANCHORS)):
        t0, f0 = _PROFILE_ANCHORS[i - 1]
        t1, f1 = _PROFILE_ANCHORS[i]
        if minutes_since_open <= t1:
            alpha = (minutes_since_open - t0) / (t1 - t0)
            return f0 + alpha * (f1 - f0)
    return 1.0  # pragma: no cover


def compute_rvol(
    today_volume: int,
    avg_daily_volume: float,
    minutes_since_open: int,
) -> float:
    """
    Compute time-of-day-normalized relative volume (RVOL).

    RVOL = today_volume / (avg_daily_volume * expected_fraction_at_this_time)

    An RVOL of 2.0 at 9:35 AM means the stock has traded 2x the volume
    normally expected in the first 5 minutes — the kind of surge Qullamaggie
    looks for on breakout/EP entries.

    Returns 0.0 if inputs are invalid (zero avg volume or pre-market).
    """
    if avg_daily_volume <= 0 or minutes_since_open <= 0:
        return 0.0
    expected_fraction = _cumulative_volume_fraction(minutes_since_open)
    if expected_fraction <= 0:
        return 0.0
    expected_volume = avg_daily_volume * expected_fraction
    return today_volume / expected_volume


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    """Convert list of candle dicts to a DataFrame."""
    return pd.DataFrame(candles)
