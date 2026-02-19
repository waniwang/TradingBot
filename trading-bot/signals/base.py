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


SetupType = Literal["breakout", "episodic_pivot", "parabolic_short"]
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


def compute_avg_volume(volumes: list[int] | pd.Series, period: int = 20) -> float:
    """Return the N-period average daily volume."""
    s = pd.Series(volumes) if not isinstance(volumes, pd.Series) else volumes
    if len(s) < period:
        return float(s.mean())
    return float(s.tail(period).mean())


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    """Convert list of candle dicts to a DataFrame."""
    return pd.DataFrame(candles)
