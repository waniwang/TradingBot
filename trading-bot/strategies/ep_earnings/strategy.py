"""
EP Earnings swing strategy filters.

Strategy B (the only remaining variant): 44.7% WR, +4.95% avg, PF 2.57
  1. CHG-OPEN% > 0
  2. close_in_range >= 50
  3. ATR% between 2% and 5%
  4. Stop: -7% | Hold: 50 days

Entry: at/near market close (~3:50 PM ET) on gap day. Features computed using
current price as proxy for day's Close at ~3 PM scan time.

History: Strategies A and C were dropped 2026-05-08 after a re-validation
on corrected Spikeet data showed A-only trades had PF 1.36 (worse than B
alone) and C-with-day-2-confirm had PF 1.85 (worse than no filter at all).
See README "History" for the full audit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    """Result of evaluating a candidate against the EP earnings strategy."""
    ticker: str
    strategy: str  # "ep_earnings_b"
    passes: bool
    entry_price: float
    stop_price: float

    # Computed features
    chg_open_pct: float
    close_in_range: float
    downside_from_open: float
    prev_10d_change_pct: float
    atr_pct: float

    # Scanner data carried forward
    gap_pct: float
    open_price: float
    prev_close: float
    market_cap: float
    rvol: float

    notes: str = ""


def compute_features(
    candidate: dict,
    daily_closes: list[float],
    daily_highs: list[float],
    daily_lows: list[float],
) -> dict[str, float]:
    """
    Compute strategy features for an EP earnings candidate.

    Uses current_price as proxy for Close (scan runs at ~3 PM, close enough).
    """
    open_price = candidate["open_price"]
    current_price = candidate["current_price"]
    today_high = candidate.get("today_high", 0)
    today_low = candidate.get("today_low", 0)

    # CHG-OPEN% = (Close - Open) / Open * 100
    chg_open_pct = (current_price - open_price) / open_price * 100 if open_price > 0 else 0

    # close_in_range = (Close - Low) / (High - Low) * 100
    day_range = today_high - today_low
    if day_range > 0:
        close_in_range = (current_price - today_low) / day_range * 100
    else:
        close_in_range = 50.0  # flat day, neutral

    # downside_from_open = (Open - Low) / Open * 100
    downside_from_open = (open_price - today_low) / open_price * 100 if open_price > 0 else 0

    # Prev 10D change% (from daily closes, exclude today). Raise on insufficient
    # data — silent fallback would zero the value and make yfinance fetch
    # misses look like strategy rejections.
    if len(daily_closes) < 11:
        raise ValueError(
            f"{candidate['ticker']}: only {len(daily_closes)} daily closes, "
            f"need >=11 to compute prev_10d_change"
        )
    close_10d_ago = daily_closes[-11]
    close_yesterday = daily_closes[-1]
    if close_10d_ago == 0:
        raise ValueError(f"{candidate['ticker']}: close 10 days ago is 0")
    prev_10d_change_pct = (close_yesterday - close_10d_ago) / close_10d_ago * 100

    # ATR% = 10D ATR / current_price * 100 (raises on insufficient data)
    atr_pct = _compute_atr_pct(daily_highs, daily_lows, daily_closes, current_price, period=10)

    return {
        "chg_open_pct": round(chg_open_pct, 2),
        "close_in_range": round(close_in_range, 2),
        "downside_from_open": round(downside_from_open, 2),
        "prev_10d_change_pct": round(prev_10d_change_pct, 2),
        "atr_pct": round(atr_pct, 2),
    }


def evaluate_strategy_b(candidate: dict, features: dict, config: dict) -> str | None:
    """
    Strategy B (the only remaining EP earnings variant).

    Returns `None` if candidate passes all Strategy B rules, else a short
    code identifying the first-failed filter (one of: "chg_open", "cir",
    "atr"). Used by the plugin job_scan to aggregate per-filter rejection
    counts into the dashboard's result_summary field.
    """
    cfg = config.get("signals", {})

    # 1. CHG-OPEN% > 0
    if features["chg_open_pct"] <= 0:
        logger.debug("%s: Strategy B fail - CHG-OPEN%% %.2f <= 0", candidate["ticker"], features["chg_open_pct"])
        return "chg_open"

    # 2. close_in_range >= 50
    min_close_in_range = float(cfg.get("ep_earnings_b_min_close_in_range", 50.0))
    if features["close_in_range"] < min_close_in_range:
        logger.debug("%s: Strategy B fail - close_in_range %.1f < %.1f", candidate["ticker"], features["close_in_range"], min_close_in_range)
        return "cir"

    # 3. ATR% between 2% and 5%
    atr_min = float(cfg.get("ep_earnings_b_atr_pct_min", 2.0))
    atr_max = float(cfg.get("ep_earnings_b_atr_pct_max", 5.0))
    if not (atr_min <= features["atr_pct"] <= atr_max):
        logger.debug(
            "%s: Strategy B fail - ATR%% %.2f not in [%.1f, %.1f]",
            candidate["ticker"], features["atr_pct"], atr_min, atr_max,
        )
        return "atr"

    return None


def evaluate_ep_earnings_strategies(
    candidates: list[dict],
    daily_bars: dict,
    config: dict,
) -> tuple[list[dict], list[dict]]:
    """
    Evaluate scanner candidates against Strategy B.

    Args:
        candidates: list of dicts from scan_ep_earnings()
        daily_bars: {ticker: DataFrame} with OHLCV columns
        config: full app config dict

    Returns:
        Tuple of (entries, rejections).
        - entries: list of entry dicts ready for persistence/execution.
        - rejections: list of {"ticker", "reason", "is_data_error"} dicts for
          every candidate that did not produce an entry. `is_data_error=True`
          means missing/short daily bars (a bug to investigate, not a normal
          filter miss); `False` means the ticker was evaluated and its feature
          values did not satisfy Strategy B.
    """
    cfg = config.get("signals", {})
    stop_loss_pct = float(cfg.get("ep_earnings_stop_loss_pct", 7.0))
    max_hold_days = int(cfg.get("ep_earnings_max_hold_days", 50))

    entries: list[dict] = []
    rejections: list[dict] = []

    for c in candidates:
        ticker = c["ticker"]
        df = daily_bars.get(ticker)
        if df is None or (hasattr(df, "empty") and df.empty):
            logger.error("%s: no daily bars returned — data error, not a filter miss", ticker)
            rejections.append({
                "ticker": ticker,
                "reason": "no daily bars returned (likely yfinance batch failure)",
                "is_data_error": True,
            })
            continue

        daily_closes = list(df["close"].values) if hasattr(df, "values") else list(df["close"])
        daily_highs = list(df["high"].values) if hasattr(df, "values") else list(df["high"])
        daily_lows = list(df["low"].values) if hasattr(df, "values") else list(df["low"])

        try:
            features = compute_features(c, daily_closes, daily_highs, daily_lows)
        except ValueError as e:
            logger.error("%s: feature computation failed — %s", ticker, e)
            rejections.append({
                "ticker": ticker,
                "reason": f"insufficient daily bars: {e}",
                "is_data_error": True,
            })
            continue

        failed_filter = evaluate_strategy_b(c, features, config)
        if failed_filter is not None:
            rejections.append({
                "ticker": ticker,
                "reason": (
                    f"Strategy B failed on {failed_filter} "
                    f"(CHG {features['chg_open_pct']:+.1f}% "
                    f"CIR {features['close_in_range']:.0f} "
                    f"ATR {features['atr_pct']:.1f}%)"
                ),
                "is_data_error": False,
                "rejected_filter": failed_filter,
            })
            continue

        entry_price = c["current_price"]
        stop_price = round(entry_price * (1 - stop_loss_pct / 100), 2)

        entry = {
            "ticker": ticker,
            "setup_type": "ep_earnings",
            "ep_strategy": "B",
            "entry_price": entry_price,
            "stop_price": stop_price,
            "stop_loss_pct": stop_loss_pct,
            "max_hold_days": max_hold_days,
            "gap_pct": c["gap_pct"],
            "open_price": c["open_price"],
            "prev_close": c["prev_close"],
            "prev_high": c.get("prev_high", 0),
            "current_price": entry_price,
            "today_volume": c.get("today_volume", 0),
            "sma_200": c.get("sma_200"),
            "market_cap": c.get("market_cap", 0),
            "rvol": c.get("rvol", 0),
            "today_high": c.get("today_high", 0),
            "today_low": c.get("today_low", 0),
            **features,
        }
        entries.append(entry)
        logger.info(
            "%s: PASSES Strategy B (CHG-OPEN=%.1f%%, CIR=%.0f, ATR=%.1f%%, P10D=%.1f%%)",
            ticker, features["chg_open_pct"], features["close_in_range"],
            features["atr_pct"], features["prev_10d_change_pct"],
        )

    data_err_count = sum(1 for r in rejections if r["is_data_error"])
    logger.info(
        "EP Earnings strategy evaluation: %d entries from %d candidates "
        "(%d data errors, %d filter misses)",
        len(entries), len(candidates),
        data_err_count, len(rejections) - data_err_count,
    )
    return entries, rejections


def _compute_atr_pct(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    current_price: float,
    period: int = 10,
) -> float:
    """
    Compute ATR% = ATR(period) / current_price * 100.

    Uses Wilder's smoothing (same as standard ATR).
    """
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        raise ValueError(
            f"insufficient bars for ATR(period={period}): "
            f"highs={len(highs)} lows={len(lows)} closes={len(closes)}, need >={period + 1}"
        )
    if current_price <= 0:
        raise ValueError(f"current_price must be positive, got {current_price}")

    # True Range for last (period) bars
    trs = []
    for i in range(-period, 0):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)

    atr = float(np.mean(trs))
    return atr / current_price * 100
