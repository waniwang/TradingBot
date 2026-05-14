"""
EP News swing strategy filters.

A and B are mutually exclusive per ticker — A wins if both pass (tighter
filters), so we never double-stake the same idea. Both use a -7% stop.

Strategy A (NEWS-Tight): 57.6% WR, +11.93% avg, PF 5.34 (corrected backtest)
  1. CHG-OPEN% between 2% and 10%
  2. close_in_range >= 50
  3. downside_from_open < 3%
  4. ATR% between 3% and 7%
  5. Volume < 3M
  6. Market cap >= $1B (applied in scanner)
  Stop: -7% | Hold: 50 days

Strategy B (NEWS-Relaxed): 49.1% WR, +9.92% avg, PF 4.24 (corrected backtest)
  1. CHG-OPEN% between 2% and 10%
  2. close_in_range between 30% and 80%
  3. downside_from_open < 6%
  4. ATR% between 3% and 7%
  5. Volume < 5M
  6. Market cap >= $1B (applied in scanner)
  Stop: -7% | Hold: 50 days  (was -10% pre-2026-05-08; tightening lifted PF
                              from 3.48 → 4.24 and turned 2021 from a losing
                              to a flat year. See README "History".)

Entry: at/near market close (~3:50 PM ET) on gap day.
All features computed using current price as proxy for day's Close at ~3 PM scan time.

History: Strategy C was dropped 2026-05-08 after a re-validation on corrected
Spikeet data showed C-with-day-2-confirm had PF 2.25 — barely better than
buying every gap (PF 1.95) — while contributing 267 trades/year of dead
weight. See README "History".
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

    # Prev 10D change% (from daily closes, exclude today).
    # Raise on insufficient data — silent fallback would zero the value and
    # cause every P10D-based filter (A, B, C) to fail silently, making
    # yfinance fetch misses look like strategy rejections.
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


def evaluate_strategy_a(candidate: dict, features: dict, config: dict) -> str | None:
    """
    Strategy A (NEWS-Tight).

    Returns `None` if candidate passes; else a short filter-code identifying
    the first-failed filter ("chg_open", "cir", "downside", "atr", "volume").
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
        return "chg_open"

    # 2. close_in_range >= 50%
    min_close_in_range = float(cfg.get("ep_news_a_min_close_in_range", 50.0))
    if features["close_in_range"] < min_close_in_range:
        logger.debug("%s: Strategy A fail - close_in_range %.1f < %.1f", candidate["ticker"], features["close_in_range"], min_close_in_range)
        return "cir"

    # 3. downside_from_open < 3%
    max_downside = float(cfg.get("ep_news_a_max_downside_from_open", 3.0))
    if features["downside_from_open"] >= max_downside:
        logger.debug("%s: Strategy A fail - downside_from_open %.2f >= %.1f", candidate["ticker"], features["downside_from_open"], max_downside)
        return "downside"

    # Prev 10D filter removed 2026-04-21 after Spikeet data column proved
    # unreliable (sign-inverted vs yfinance on every 2026-04-20 candidate).
    # Full 2020-2026 backtest showed PF 8.72 without it vs 9.00 with — and
    # trade count doubles from 51 to 107. See strategies/ep_news/README.md.

    # ATR% between 3% and 7%
    atr_min = float(cfg.get("ep_news_a_atr_pct_min", 3.0))
    atr_max = float(cfg.get("ep_news_a_atr_pct_max", 7.0))
    if not (atr_min <= features["atr_pct"] <= atr_max):
        logger.debug(
            "%s: Strategy A fail - ATR%% %.2f not in [%.1f, %.1f]",
            candidate["ticker"], features["atr_pct"], atr_min, atr_max,
        )
        return "atr"

    # Volume < 3M
    max_volume_m = float(cfg.get("ep_news_a_max_volume_m", 3.0))
    today_volume_m = candidate.get("today_volume", 0) / 1e6
    if today_volume_m >= max_volume_m:
        logger.debug(
            "%s: Strategy A fail - volume %.1fM >= %.1fM",
            candidate["ticker"], today_volume_m, max_volume_m,
        )
        return "volume"

    return None


def evaluate_strategy_b(candidate: dict, features: dict, config: dict) -> str | None:
    """
    Strategy B (NEWS-Relaxed).

    Returns `None` if candidate passes; else a short filter-code identifying
    the first-failed filter ("chg_open", "cir", "downside", "atr", "volume").
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
        return "chg_open"

    # 2. close_in_range between 30% and 80%
    cir_min = float(cfg.get("ep_news_b_min_close_in_range", 30.0))
    cir_max = float(cfg.get("ep_news_b_max_close_in_range", 80.0))
    if not (cir_min <= features["close_in_range"] <= cir_max):
        logger.debug(
            "%s: Strategy B fail - close_in_range %.1f not in [%.1f, %.1f]",
            candidate["ticker"], features["close_in_range"], cir_min, cir_max,
        )
        return "cir"

    # 3. downside_from_open < 6%
    max_downside = float(cfg.get("ep_news_b_max_downside_from_open", 6.0))
    if features["downside_from_open"] >= max_downside:
        logger.debug("%s: Strategy B fail - downside_from_open %.2f >= %.1f", candidate["ticker"], features["downside_from_open"], max_downside)
        return "downside"

    # Prev 10D filter removed 2026-04-21 (see evaluate_strategy_a).

    # ATR% between 3% and 7%
    atr_min = float(cfg.get("ep_news_b_atr_pct_min", 3.0))
    atr_max = float(cfg.get("ep_news_b_atr_pct_max", 7.0))
    if not (atr_min <= features["atr_pct"] <= atr_max):
        logger.debug(
            "%s: Strategy B fail - ATR%% %.2f not in [%.1f, %.1f]",
            candidate["ticker"], features["atr_pct"], atr_min, atr_max,
        )
        return "atr"

    # Volume < 5M
    max_volume_m = float(cfg.get("ep_news_b_max_volume_m", 5.0))
    today_volume_m = candidate.get("today_volume", 0) / 1e6
    if today_volume_m >= max_volume_m:
        logger.debug(
            "%s: Strategy B fail - volume %.1fM >= %.1fM",
            candidate["ticker"], today_volume_m, max_volume_m,
        )
        return "volume"

    return None


def evaluate_ep_news_strategies(
    candidates: list[dict],
    daily_bars: dict,
    config: dict,
) -> tuple[list[dict], list[dict]]:
    """
    Evaluate scanner candidates against Strategy A and B.

    A and B are mutually exclusive per ticker — if A passes, B is skipped.
    A has the tighter filters, so picking A when both pass prevents the same
    idea from consuming two position slots. Both use a -7% stop.

    Args:
        candidates: list of dicts from scan_ep_news()
        daily_bars: {ticker: DataFrame} with OHLCV columns
        config: full app config dict

    Returns:
        Tuple of (entries, rejections).
        - entries: list of entry dicts ready for persistence/execution.
        - rejections: list of {"ticker", "reason", "is_data_error"} dicts for
          every candidate that did not produce an entry. `is_data_error=True`
          means missing/short daily bars (a bug to investigate, not a normal
          filter miss); `False` means the ticker was evaluated and its feature
          values did not satisfy A or B.
    """
    cfg = config.get("signals", {})
    stop_a = float(cfg.get("ep_news_a_stop_loss_pct", 7.0))
    stop_b = float(cfg.get("ep_news_b_stop_loss_pct", 7.0))
    max_hold_days = int(cfg.get("ep_news_max_hold_days", 50))

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
            **features,
        }

        # A wins if both pass (tighter filters).
        a_failed_filter = evaluate_strategy_a(c, features, config)
        b_failed_filter: str | None = None
        passed_a = a_failed_filter is None
        passed_b = False
        if passed_a:
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
        else:
            b_failed_filter = evaluate_strategy_b(c, features, config)
            passed_b = b_failed_filter is None
            if passed_b:
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

        if not (passed_a or passed_b):
            rejections.append({
                "ticker": ticker,
                "reason": (
                    f"A failed on {a_failed_filter}, B failed on {b_failed_filter} "
                    f"(CHG {features['chg_open_pct']:+.1f}% "
                    f"CIR {features['close_in_range']:.0f} "
                    f"DS {features['downside_from_open']:.1f}% "
                    f"ATR {features['atr_pct']:.1f}% "
                    f"P10D {features['prev_10d_change_pct']:+.1f}% "
                    f"Vol {c.get('today_volume', 0) / 1e6:.1f}M)"
                ),
                "is_data_error": False,
                "rejected_filter_a": a_failed_filter,
                "rejected_filter_b": b_failed_filter,
            })

    a_count = sum(1 for e in entries if e["ep_strategy"] == "A")
    b_count = sum(1 for e in entries if e["ep_strategy"] == "B")
    data_err_count = sum(1 for r in rejections if r["is_data_error"])
    logger.info(
        "EP News strategy evaluation: %d entries from %d candidates "
        "(A=%d, B=%d; %d data errors, %d filter misses)",
        len(entries), len(candidates), a_count, b_count,
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
    # Raise on insufficient data — silent 0.0 fallback would fail A/B/C's
    # ATR-range filters and misattribute a data error as a strategy rejection.
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
