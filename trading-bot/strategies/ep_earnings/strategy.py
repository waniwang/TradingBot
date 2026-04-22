"""
EP Earnings swing strategy filters.

Evaluates scanner candidates against Strategy A, B, and C rules.
A and B are mutually exclusive per ticker — A wins if both pass (tighter filter set)
so we never double-stake the same idea. C is day-2 gated and can coexist with A/B.

Strategy A (Tight Filters): 69% WR, +9.18% avg, PF 5.68
  1. CHG-OPEN% > 0 (closed above open)
  2. close_in_range >= 50 (closed in top half of day's range)
  3. downside_from_open < 3% (didn't dip much below open)
  4. Prev 10D change% between -30% and -10%
  5. Stop: -7% | Hold: 50 days

Strategy B (Relaxed Filters): 61% WR, +11.75% avg, PF 5.62
  1. CHG-OPEN% > 0
  2. close_in_range >= 50
  3. ATR% between 2% and 5%
  4. Prev 10D change% < -10%
  5. Stop: -7% | Hold: 50 days

Strategy C (Bear Market / Day-2 Confirm): ~48% WR, +7.6% avg, PF ~3.3
  1. Prev 10D change% <= -10% (beaten down pre-earnings)
  2. No CHG-OPEN% or close_in_range filters (works in all regimes)
  3. Day-2 confirmation: only enter if 1D return > 0 (stock holds up next day)
  4. Entry at day 2 close, not gap day close
  5. Stop: -7% | Hold: 20 days

Entry for A/B: at/near market close (~3:50 PM ET) on gap day.
Entry for C: at/near market close on day 2, after confirming positive 1D return.
All features computed using current price as proxy for day's Close at ~3 PM scan time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    """Result of evaluating a candidate against EP earnings strategies."""
    ticker: str
    strategy: str  # "ep_earnings_a", "ep_earnings_b", or "ep_earnings_c"
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

    Args:
        candidate: dict from scan_ep_earnings() with open_price, current_price, etc.
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


def evaluate_strategy_a(candidate: dict, features: dict, config: dict) -> bool:
    """
    Strategy A (Tight Filters).

    Returns True if candidate passes all Strategy A rules.
    """
    cfg = config.get("signals", {})

    # 1. CHG-OPEN% > 0
    if features["chg_open_pct"] <= 0:
        logger.debug("%s: Strategy A fail - CHG-OPEN%% %.2f <= 0", candidate["ticker"], features["chg_open_pct"])
        return False

    # 2. close_in_range >= 50
    min_close_in_range = float(cfg.get("ep_earnings_a_min_close_in_range", 50.0))
    if features["close_in_range"] < min_close_in_range:
        logger.debug("%s: Strategy A fail - close_in_range %.1f < %.1f", candidate["ticker"], features["close_in_range"], min_close_in_range)
        return False

    # 3. downside_from_open < 3%
    max_downside = float(cfg.get("ep_earnings_a_max_downside_from_open", 3.0))
    if features["downside_from_open"] >= max_downside:
        logger.debug("%s: Strategy A fail - downside_from_open %.2f >= %.1f", candidate["ticker"], features["downside_from_open"], max_downside)
        return False

    # Prev 10D filter removed 2026-04-21 after Spikeet data column proved
    # unreliable (sign-inverted vs yfinance on every 2026-04-20 candidate).
    # Full backtest (2020-2026, 960 earnings rows) showed PF barely changes
    # without it while trade count jumps +38%, so the filter was gating on
    # noise. See strategies/ep_earnings/README.md "History".

    return True


def evaluate_strategy_b(candidate: dict, features: dict, config: dict) -> bool:
    """
    Strategy B (Relaxed Filters).

    Returns True if candidate passes all Strategy B rules.
    """
    cfg = config.get("signals", {})

    # 1. CHG-OPEN% > 0
    if features["chg_open_pct"] <= 0:
        logger.debug("%s: Strategy B fail - CHG-OPEN%% %.2f <= 0", candidate["ticker"], features["chg_open_pct"])
        return False

    # 2. close_in_range >= 50
    min_close_in_range = float(cfg.get("ep_earnings_b_min_close_in_range", 50.0))
    if features["close_in_range"] < min_close_in_range:
        logger.debug("%s: Strategy B fail - close_in_range %.1f < %.1f", candidate["ticker"], features["close_in_range"], min_close_in_range)
        return False

    # 3. ATR% between 2% and 5%
    atr_min = float(cfg.get("ep_earnings_b_atr_pct_min", 2.0))
    atr_max = float(cfg.get("ep_earnings_b_atr_pct_max", 5.0))
    if not (atr_min <= features["atr_pct"] <= atr_max):
        logger.debug(
            "%s: Strategy B fail - ATR%% %.2f not in [%.1f, %.1f]",
            candidate["ticker"], features["atr_pct"], atr_min, atr_max,
        )
        return False

    # Prev 10D filter removed 2026-04-21 (see evaluate_strategy_a).

    return True


def evaluate_strategy_c(candidate: dict, features: dict, config: dict) -> bool:
    """
    Strategy C (Day-2 Confirm).

    The original P10D-based pre-earnings filter was removed 2026-04-21 after
    its Spikeet backtest column proved unreliable. C now accepts every
    scanner candidate and relies entirely on the plugin's day-2 confirmation
    (price on day 2 > gap-day close) to gate entry — this is where the edge
    actually comes from. Position cap + risk manager limit overall exposure.

    Returns True unconditionally for any scanner candidate.
    """
    return True


def evaluate_ep_earnings_strategies(
    candidates: list[dict],
    daily_bars: dict,
    config: dict,
) -> tuple[list[dict], list[dict]]:
    """
    Evaluate scanner candidates against Strategy A, B, and C.

    A and B are mutually exclusive per ticker — if A passes, B is skipped.
    A is the tighter filter set, so picking A when both pass gives the
    stronger signal and prevents the same idea from consuming two position
    slots / doubling the risk budget. Strategy C runs independently and
    can coexist with A/B because it enters on day 2, not the gap day.

    Strategy C entries are tagged with day2_confirm=True; they should NOT
    be executed on gap day but held for day-2 confirmation by the plugin.

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
          values did not satisfy any of A/B/C.
    """
    cfg = config.get("signals", {})
    stop_loss_pct = float(cfg.get("ep_earnings_stop_loss_pct", 7.0))
    max_hold_days = int(cfg.get("ep_earnings_max_hold_days", 50))
    stop_c = float(cfg.get("ep_earnings_c_stop_loss_pct", 7.0))
    max_hold_c = int(cfg.get("ep_earnings_c_max_hold_days", 20))

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
        stop_price = round(entry_price * (1 - stop_loss_pct / 100), 2)

        # Base entry dict (shared fields)
        base = {
            "ticker": ticker,
            "setup_type": "ep_earnings",
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
            # Computed features
            **features,
        }

        # Evaluate Strategy A and B — A wins if both pass (A is the tighter filter set).
        # Only one of A/B can produce an entry per ticker so we never double-stake the same
        # idea across position slots or risk budget. C is independent and day-2 gated, so
        # it can still coexist with A/B (different entry day).
        passed_a = evaluate_strategy_a(c, features, config)
        passed_b = False
        if passed_a:
            entry_a = {**base, "ep_strategy": "A"}
            entries.append(entry_a)
            logger.info(
                "%s: PASSES Strategy A (CHG-OPEN=%.1f%%, CIR=%.0f, DS=%.1f%%, P10D=%.1f%%)",
                ticker, features["chg_open_pct"], features["close_in_range"],
                features["downside_from_open"], features["prev_10d_change_pct"],
            )
        else:
            passed_b = evaluate_strategy_b(c, features, config)
            if passed_b:
                entry_b = {**base, "ep_strategy": "B"}
                entries.append(entry_b)
                logger.info(
                    "%s: PASSES Strategy B (CHG-OPEN=%.1f%%, CIR=%.0f, ATR=%.1f%%, P10D=%.1f%%)",
                    ticker, features["chg_open_pct"], features["close_in_range"],
                    features["atr_pct"], features["prev_10d_change_pct"],
                )

        # Evaluate Strategy C (day-2 confirmation required)
        passed_c = evaluate_strategy_c(c, features, config)
        if passed_c:
            stop_price_c = round(entry_price * (1 - stop_c / 100), 2)
            entry_c = {
                **base,
                "ep_strategy": "C",
                "stop_price": stop_price_c,
                "stop_loss_pct": stop_c,
                "max_hold_days": max_hold_c,
                "day2_confirm": True,
                "gap_day_close": entry_price,  # save for day-2 comparison
            }
            entries.append(entry_c)
            logger.info(
                "%s: PASSES Strategy C (P10D=%.1f%%) — pending day-2 confirmation",
                ticker, features["prev_10d_change_pct"],
            )

        if not (passed_a or passed_b or passed_c):
            rejections.append({
                "ticker": ticker,
                "reason": (
                    f"no strategy passed "
                    f"(CHG {features['chg_open_pct']:+.1f}% "
                    f"CIR {features['close_in_range']:.0f} "
                    f"DS {features['downside_from_open']:.1f}% "
                    f"ATR {features['atr_pct']:.1f}% "
                    f"P10D {features['prev_10d_change_pct']:+.1f}%)"
                ),
                "is_data_error": False,
            })

    a_count = sum(1 for e in entries if e["ep_strategy"] == "A")
    b_count = sum(1 for e in entries if e["ep_strategy"] == "B")
    c_count = sum(1 for e in entries if e["ep_strategy"] == "C")
    data_err_count = sum(1 for r in rejections if r["is_data_error"])
    logger.info(
        "EP Earnings strategy evaluation: %d entries from %d candidates "
        "(A=%d, B=%d, C=%d pending; %d data errors, %d filter misses)",
        len(entries), len(candidates), a_count, b_count, c_count,
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
