"""
EP News swing strategy filters.

Evaluates scanner candidates against Strategy A and Strategy B rules.
Both strategies are evaluated independently so we can track performance separately.

Strategy A (NEWS-Tight): 65% WR, +18.31% avg, PF 9.13
  1. CHG-OPEN% between 2% and 10%
  2. close_in_range >= 50
  3. downside_from_open < 3%
  4. Prev 10D change% <= -20%
  5. ATR% between 3% and 7%
  6. Volume < 3M
  7. Market cap >= $1B (applied in scanner)
  Stop: -7% | Hold: 50 days

Strategy B (NEWS-Relaxed): 64% WR, +17.87% avg, PF 6.65
  1. CHG-OPEN% between 2% and 10%
  2. close_in_range between 30% and 80%
  3. downside_from_open < 6%
  4. Prev 10D change% <= -10%
  5. ATR% between 3% and 7%
  6. Volume < 5M
  7. Market cap >= $1B (applied in scanner)
  Stop: -10% | Hold: 50 days

Entry: at/near market close (~3:50 PM ET) on gap day.
All features computed using current price as proxy for day's Close at ~3 PM scan time.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def compute_features(
    candidate: dict,
    daily_closes: list[float],
    daily_highs: list[float],
    daily_lows: list[float],
) -> dict[str, float]:
    """
    Compute strategy features for an EP news candidate.

    Uses current_price as proxy for Close (scan runs at ~3 PM, close enough).

    Args:
        candidate: dict from scan_ep_news() with open_price, current_price, etc.
        daily_closes: list of historical daily closes (oldest first)
        daily_highs: list of historical daily highs
        daily_lows: list of historical daily lows

    Returns:
        Dict with computed feature values.
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

    # Prev 10D change% (from daily closes, exclude today)
    if len(daily_closes) >= 11:
        close_10d_ago = daily_closes[-11]
        close_yesterday = daily_closes[-1]
        prev_10d_change_pct = (close_yesterday - close_10d_ago) / close_10d_ago * 100
    else:
        prev_10d_change_pct = 0.0

    # ATR% = 10D ATR / current_price * 100
    atr_pct = _compute_atr_pct(daily_highs, daily_lows, daily_closes, current_price, period=10)

    return {
        "chg_open_pct": round(chg_open_pct, 2),
        "close_in_range": round(close_in_range, 2),
        "downside_from_open": round(downside_from_open, 2),
        "prev_10d_change_pct": round(prev_10d_change_pct, 2),
        "atr_pct": round(atr_pct, 2),
    }


def evaluate_strategy_a(candidate: dict, features: dict, config: dict) -> bool:
    """
    Strategy A (NEWS-Tight).

    Returns True if candidate passes all Strategy A rules.
    """
    cfg = config.get("signals", {})

    # 1. CHG-OPEN% between 2% and 10%
    chg_min = float(cfg.get("ep_news_a_chg_open_min", 2.0))
    chg_max = float(cfg.get("ep_news_a_chg_open_max", 10.0))
    if not (chg_min < features["chg_open_pct"] <= chg_max):
        logger.debug(
            "%s: Strategy A fail - CHG-OPEN%% %.2f not in (%.1f, %.1f]",
            candidate["ticker"], features["chg_open_pct"], chg_min, chg_max,
        )
        return False

    # 2. close_in_range >= 50%
    min_close_in_range = float(cfg.get("ep_news_a_min_close_in_range", 50.0))
    if features["close_in_range"] < min_close_in_range:
        logger.debug("%s: Strategy A fail - close_in_range %.1f < %.1f", candidate["ticker"], features["close_in_range"], min_close_in_range)
        return False

    # 3. downside_from_open < 3%
    max_downside = float(cfg.get("ep_news_a_max_downside_from_open", 3.0))
    if features["downside_from_open"] >= max_downside:
        logger.debug("%s: Strategy A fail - downside_from_open %.2f >= %.1f", candidate["ticker"], features["downside_from_open"], max_downside)
        return False

    # 4. Prev 10D change% <= -20%
    prev_10d_max = float(cfg.get("ep_news_a_prev_10d_max", -20.0))
    if features["prev_10d_change_pct"] > prev_10d_max:
        logger.debug(
            "%s: Strategy A fail - prev_10d %.2f > %.1f",
            candidate["ticker"], features["prev_10d_change_pct"], prev_10d_max,
        )
        return False

    # 5. ATR% between 3% and 7%
    atr_min = float(cfg.get("ep_news_a_atr_pct_min", 3.0))
    atr_max = float(cfg.get("ep_news_a_atr_pct_max", 7.0))
    if not (atr_min <= features["atr_pct"] <= atr_max):
        logger.debug(
            "%s: Strategy A fail - ATR%% %.2f not in [%.1f, %.1f]",
            candidate["ticker"], features["atr_pct"], atr_min, atr_max,
        )
        return False

    # 6. Volume < 3M
    max_volume_m = float(cfg.get("ep_news_a_max_volume_m", 3.0))
    today_volume_m = candidate.get("today_volume", 0) / 1e6
    if today_volume_m >= max_volume_m:
        logger.debug(
            "%s: Strategy A fail - volume %.1fM >= %.1fM",
            candidate["ticker"], today_volume_m, max_volume_m,
        )
        return False

    return True


def evaluate_strategy_b(candidate: dict, features: dict, config: dict) -> bool:
    """
    Strategy B (NEWS-Relaxed).

    Returns True if candidate passes all Strategy B rules.
    """
    cfg = config.get("signals", {})

    # 1. CHG-OPEN% between 2% and 10%
    chg_min = float(cfg.get("ep_news_b_chg_open_min", 2.0))
    chg_max = float(cfg.get("ep_news_b_chg_open_max", 10.0))
    if not (chg_min < features["chg_open_pct"] <= chg_max):
        logger.debug(
            "%s: Strategy B fail - CHG-OPEN%% %.2f not in (%.1f, %.1f]",
            candidate["ticker"], features["chg_open_pct"], chg_min, chg_max,
        )
        return False

    # 2. close_in_range between 30% and 80%
    cir_min = float(cfg.get("ep_news_b_min_close_in_range", 30.0))
    cir_max = float(cfg.get("ep_news_b_max_close_in_range", 80.0))
    if not (cir_min <= features["close_in_range"] <= cir_max):
        logger.debug(
            "%s: Strategy B fail - close_in_range %.1f not in [%.1f, %.1f]",
            candidate["ticker"], features["close_in_range"], cir_min, cir_max,
        )
        return False

    # 3. downside_from_open < 6%
    max_downside = float(cfg.get("ep_news_b_max_downside_from_open", 6.0))
    if features["downside_from_open"] >= max_downside:
        logger.debug("%s: Strategy B fail - downside_from_open %.2f >= %.1f", candidate["ticker"], features["downside_from_open"], max_downside)
        return False

    # 4. Prev 10D change% <= -10%
    prev_10d_max = float(cfg.get("ep_news_b_prev_10d_max", -10.0))
    if features["prev_10d_change_pct"] > prev_10d_max:
        logger.debug(
            "%s: Strategy B fail - prev_10d %.2f > %.1f",
            candidate["ticker"], features["prev_10d_change_pct"], prev_10d_max,
        )
        return False

    # 5. ATR% between 3% and 7%
    atr_min = float(cfg.get("ep_news_b_atr_pct_min", 3.0))
    atr_max = float(cfg.get("ep_news_b_atr_pct_max", 7.0))
    if not (atr_min <= features["atr_pct"] <= atr_max):
        logger.debug(
            "%s: Strategy B fail - ATR%% %.2f not in [%.1f, %.1f]",
            candidate["ticker"], features["atr_pct"], atr_min, atr_max,
        )
        return False

    # 6. Volume < 5M
    max_volume_m = float(cfg.get("ep_news_b_max_volume_m", 5.0))
    today_volume_m = candidate.get("today_volume", 0) / 1e6
    if today_volume_m >= max_volume_m:
        logger.debug(
            "%s: Strategy B fail - volume %.1fM >= %.1fM",
            candidate["ticker"], today_volume_m, max_volume_m,
        )
        return False

    return True


def evaluate_ep_news_strategies(
    candidates: list[dict],
    daily_bars: dict,
    config: dict,
) -> list[dict]:
    """
    Evaluate scanner candidates against both Strategy A and B.

    For each candidate that passes either strategy, creates an entry dict
    with strategy tag and computed features. A single stock can produce
    two entries (one for A, one for B) if it passes both.

    Args:
        candidates: list of dicts from scan_ep_news()
        daily_bars: {ticker: DataFrame} with OHLCV columns
        config: full app config dict

    Returns:
        List of entry dicts ready for persistence/execution.
    """
    cfg = config.get("signals", {})
    stop_a = float(cfg.get("ep_news_a_stop_loss_pct", 7.0))
    stop_b = float(cfg.get("ep_news_b_stop_loss_pct", 10.0))
    max_hold_days = int(cfg.get("ep_news_max_hold_days", 50))

    entries = []

    for c in candidates:
        ticker = c["ticker"]
        df = daily_bars.get(ticker)
        if df is None or (hasattr(df, "empty") and df.empty):
            logger.debug("%s: no daily bars for strategy evaluation, skipping", ticker)
            continue

        daily_closes = list(df["close"].values) if hasattr(df, "values") else list(df["close"])
        daily_highs = list(df["high"].values) if hasattr(df, "values") else list(df["high"])
        daily_lows = list(df["low"].values) if hasattr(df, "values") else list(df["low"])

        # Compute strategy features
        features = compute_features(c, daily_closes, daily_highs, daily_lows)

        entry_price = c["current_price"]

        # Base entry dict (shared fields)
        base = {
            "ticker": ticker,
            "setup_type": "ep_news",
            "entry_price": entry_price,
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
            # Computed features
            **features,
        }

        # Evaluate Strategy A
        if evaluate_strategy_a(c, features, config):
            stop_price_a = round(entry_price * (1 - stop_a / 100), 2)
            entry_a = {**base, "ep_strategy": "A", "stop_price": stop_price_a, "stop_loss_pct": stop_a}
            entries.append(entry_a)
            logger.info(
                "%s: PASSES News Strategy A (CHG-OPEN=%.1f%%, CIR=%.0f, DS=%.1f%%, "
                "P10D=%.1f%%, ATR=%.1f%%, Vol=%.1fM)",
                ticker, features["chg_open_pct"], features["close_in_range"],
                features["downside_from_open"], features["prev_10d_change_pct"],
                features["atr_pct"], c.get("today_volume", 0) / 1e6,
            )

        # Evaluate Strategy B
        if evaluate_strategy_b(c, features, config):
            stop_price_b = round(entry_price * (1 - stop_b / 100), 2)
            entry_b = {**base, "ep_strategy": "B", "stop_price": stop_price_b, "stop_loss_pct": stop_b}
            entries.append(entry_b)
            logger.info(
                "%s: PASSES News Strategy B (CHG-OPEN=%.1f%%, CIR=%.0f, DS=%.1f%%, "
                "P10D=%.1f%%, ATR=%.1f%%, Vol=%.1fM)",
                ticker, features["chg_open_pct"], features["close_in_range"],
                features["downside_from_open"], features["prev_10d_change_pct"],
                features["atr_pct"], c.get("today_volume", 0) / 1e6,
            )

    logger.info(
        "EP News strategy evaluation: %d entries from %d candidates (A=%d, B=%d)",
        len(entries), len(candidates),
        sum(1 for e in entries if e["ep_strategy"] == "A"),
        sum(1 for e in entries if e["ep_strategy"] == "B"),
    )
    return entries


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
        return 0.0

    # True Range for last (period + 1) bars
    trs = []
    for i in range(-period, 0):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)

    if not trs:
        return 0.0

    atr = float(np.mean(trs))
    if current_price <= 0:
        return 0.0

    return atr / current_price * 100
