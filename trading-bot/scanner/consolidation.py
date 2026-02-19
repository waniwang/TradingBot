"""
Breakout setup detector.

For a given ticker, checks whether it is currently in a valid consolidation:
- ATR contracting (range getting tighter)
- Higher lows during the consolidation window
- Price near the 10d or 20d moving average ("surfing" the MA)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean()


def detect_higher_lows(closes: pd.Series, window: int) -> bool:
    """
    Return True if the last `window` daily lows show a generally rising trend.
    Uses linear regression slope as a proxy.
    """
    if len(closes) < window:
        return False
    lows = closes.tail(window).values
    x = np.arange(len(lows))
    slope = np.polyfit(x, lows, 1)[0]
    return slope > 0


def detect_atr_contraction(atr_series: pd.Series, window: int) -> tuple[bool, float]:
    """
    Return (contracting, ratio) where ratio = recent_atr / older_atr.
    A ratio < 1.0 means ATR is contracting (tighter range — bullish for breakout).
    """
    if len(atr_series.dropna()) < window:
        return False, 1.0
    recent = atr_series.dropna().tail(window // 2).mean()
    older = atr_series.dropna().tail(window).head(window // 2).mean()
    if older == 0 or pd.isna(older) or pd.isna(recent):
        return False, 1.0
    ratio = recent / older
    if pd.isna(ratio):
        return False, 1.0
    return ratio < 0.85, round(float(ratio), 3)


def check_near_ma(df: pd.DataFrame, ma_period: int = 20, tolerance_pct: float = 3.0) -> bool:
    """
    Return True if the latest close is within `tolerance_pct`% of the `ma_period`-day MA.
    """
    if len(df) < ma_period:
        return False
    ma = df["close"].rolling(ma_period).mean().iloc[-1]
    close = df["close"].iloc[-1]
    pct_diff = abs(close - ma) / ma * 100
    return pct_diff <= tolerance_pct


def analyze_consolidation(
    ticker: str,
    config: dict,
    daily_bars_df: pd.DataFrame,
    consolidation_days: int | None = None,
) -> dict[str, Any]:
    """
    Analyze whether a ticker is in a valid consolidation for a breakout setup.

    Args:
        ticker: stock symbol
        config: full app config dict
        daily_bars_df: DataFrame with columns [date, open, high, low, close, volume]
        consolidation_days: override consolidation window (defaults to config value)

    Returns:
        {
            "ticker": str,
            "qualifies": bool,
            "consolidation_days": int,
            "atr_contracting": bool,
            "atr_ratio": float,
            "higher_lows": bool,
            "near_20d_ma": bool,
            "setup_type": "breakout",
            "reason": str  # why it didn't qualify, if applicable
        }
    """
    sig_cfg = config["signals"]

    if consolidation_days is None:
        consolidation_days = sig_cfg.get("breakout_consolidation_days_max", 40)

    df = daily_bars_df

    result: dict[str, Any] = {
        "ticker": ticker,
        "qualifies": False,
        "consolidation_days": consolidation_days,
        "atr_contracting": False,
        "atr_ratio": 1.0,
        "higher_lows": False,
        "near_20d_ma": False,
        "setup_type": "breakout",
        "reason": "",
    }

    if df.empty or len(df) < 30:
        result["reason"] = "insufficient_data"
        return result

    atr = compute_atr(df)
    contracting, atr_ratio = detect_atr_contraction(atr, window=consolidation_days)
    result["atr_contracting"] = contracting
    result["atr_ratio"] = atr_ratio

    consol_window = min(consolidation_days, len(df))
    higher_lows = detect_higher_lows(df["low"].tail(consol_window), consol_window)
    result["higher_lows"] = higher_lows

    near_ma = check_near_ma(df, ma_period=20)
    result["near_20d_ma"] = near_ma

    # Volume dry-up: informational indicator, not gating (included in result dict)
    recent_vol = df["volume"].tail(consol_window // 2).mean()
    longer_vol = df["volume"].tail(consol_window * 2).mean()
    volume_drying = (recent_vol / longer_vol) < 0.70 if longer_vol > 0 else False
    result["volume_drying"] = volume_drying

    # Qualify: need ATR contraction + higher lows + near MA
    if not contracting:
        result["reason"] = "atr_not_contracting"
    elif not higher_lows:
        result["reason"] = "no_higher_lows"
    elif not near_ma:
        result["reason"] = "price_far_from_ma"
    else:
        result["qualifies"] = True
        result["reason"] = "ok"

    logger.debug(
        "Consolidation check %s: qualifies=%s atr_ratio=%.3f higher_lows=%s near_ma=%s",
        ticker, result["qualifies"], atr_ratio, higher_lows, near_ma,
    )
    return result


def scan_breakout_candidates(
    tickers: list[str],
    config: dict,
    client,
) -> list[dict[str, Any]]:
    """
    Screen a list of tickers for valid breakout consolidation setups.

    Uses client.get_daily_bars_batch() to fetch data for all tickers at once,
    then analyzes each one for consolidation patterns.

    Returns only qualifying tickers.
    """
    bars_by_symbol = client.get_daily_bars_batch(tickers, days=90)
    candidates = []
    for ticker in tickers:
        try:
            df = bars_by_symbol.get(ticker)
            if df is None or df.empty:
                continue
            result = analyze_consolidation(ticker, config, df)
            if result["qualifies"]:
                candidates.append(result)
        except Exception as e:
            logger.warning("consolidation check failed for %s: %s", ticker, e)
    logger.info(
        "Breakout scan: %d/%d tickers qualify", len(candidates), len(tickers)
    )
    return candidates
