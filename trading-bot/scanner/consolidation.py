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


def detect_atr_contraction(atr_series: pd.Series, window: int, threshold: float = 0.95) -> tuple[bool, float]:
    """
    Return (contracting, ratio) where ratio = recent_atr / older_atr.
    A ratio < threshold means ATR is contracting (tighter range — bullish for breakout).
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
    return ratio < threshold, round(float(ratio), 3)


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

    min_days = sig_cfg.get("breakout_consolidation_days_min", 10)

    result: dict[str, Any] = {
        "ticker": ticker,
        "qualifies": False,
        "consolidation_days": consolidation_days,
        "atr_contracting": False,
        "atr_ratio": 1.0,
        "higher_lows": False,
        "near_10d_ma": False,
        "near_20d_ma": False,
        "has_prior_move": False,
        "setup_type": "breakout",
        "reason": "",
    }

    if df.empty or len(df) < 30:
        result["reason"] = "insufficient_data"
        return result

    consol_window = min(consolidation_days, len(df))

    # A6: Enforce minimum consolidation duration
    if consol_window < min_days:
        result["reason"] = "consolidation_too_short"
        return result

    # A5: Check for prior large directional advance (30%+ from low to high
    # in the ~2 months before consolidation). Uses directional move (low→high)
    # rather than range, matching Qullamaggie's "the stock had a big move UP".
    lookback = min(len(df) - consol_window, 60)
    if lookback > 10:
        prior_section = df.iloc[-(consol_window + lookback):-consol_window]
        if len(prior_section) > 0:
            low_idx = prior_section["low"].idxmin()
            high_after_low = prior_section.loc[low_idx:, "high"].max()
            low_val = prior_section["low"].loc[low_idx]
            move_pct = (high_after_low - low_val) / low_val * 100 if low_val > 0 else 0
            prior_move_min = sig_cfg.get("consolidation_prior_move_pct", 30.0)
            result["has_prior_move"] = bool(move_pct >= prior_move_min)
            if move_pct < prior_move_min:
                result["reason"] = "no_prior_move"
                return result
    else:
        result["has_prior_move"] = True  # insufficient data to check — pass through

    atr_threshold = sig_cfg.get("consolidation_atr_ratio", 0.95)
    ma_tolerance = sig_cfg.get("consolidation_ma_tolerance_pct", 3.0)

    atr = compute_atr(df)
    contracting, atr_ratio = detect_atr_contraction(atr, window=consolidation_days, threshold=atr_threshold)
    result["atr_contracting"] = bool(contracting)
    result["atr_ratio"] = float(atr_ratio)

    higher_lows = detect_higher_lows(df["low"].tail(consol_window), consol_window)
    result["higher_lows"] = bool(higher_lows)

    # A4: Check both 10d and 20d MA (stock should surf both)
    near_10d = check_near_ma(df, ma_period=10, tolerance_pct=ma_tolerance)
    near_20d = check_near_ma(df, ma_period=20, tolerance_pct=ma_tolerance)
    result["near_10d_ma"] = bool(near_10d)
    result["near_20d_ma"] = bool(near_20d)

    # Volume dry-up: informational indicator, not gating (included in result dict)
    recent_vol = df["volume"].tail(consol_window // 2).mean()
    longer_vol = df["volume"].tail(consol_window * 2).mean()
    volume_drying = (recent_vol / longer_vol) < 0.70 if longer_vol > 0 else False
    result["volume_drying"] = bool(volume_drying)

    # Qualify: need ATR contraction + higher lows + near BOTH MAs
    if not contracting:
        result["reason"] = "atr_not_contracting"
    elif not higher_lows:
        result["reason"] = "no_higher_lows"
    elif not near_10d:
        result["reason"] = "price_far_from_10d_ma"
    elif not near_20d:
        result["reason"] = "price_far_from_20d_ma"
    else:
        result["qualifies"] = True  # already native bool
        result["reason"] = "ok"

    logger.debug(
        "Consolidation check %s: qualifies=%s atr_ratio=%.3f higher_lows=%s "
        "near_10d=%s near_20d=%s prior_move=%s",
        ticker, result["qualifies"], atr_ratio, higher_lows,
        near_10d, near_20d, result["has_prior_move"],
    )
    return result


def classify_consolidation_stage(result: dict[str, Any]) -> str:
    """
    Classify a consolidation analysis result into a watchlist stage.

    Takes the output dict from analyze_consolidation() and returns:
      - "ready"    if qualifies=True (all criteria met)
      - "watching"  if has_prior_move and some tightening (ATR contracting or ratio < 1.0)
      - "failed"   otherwise (pattern broke down or never formed)
    """
    if result.get("qualifies"):
        return "ready"

    has_prior_move = result.get("has_prior_move", False)
    atr_contracting = result.get("atr_contracting", False)
    atr_ratio = result.get("atr_ratio", 1.0)

    if has_prior_move and (atr_contracting or atr_ratio < 1.0):
        return "watching"

    return "failed"


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
