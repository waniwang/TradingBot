"""
Signal strategy registry.

To add a new strategy:
1. Create signals/<name>.py with a check function returning SignalResult | None
2. Add a wrapper below that accepts **kwargs and calls your check function
3. Register it in STRATEGY_REGISTRY
4. Add the setup_type to SetupType in base.py
5. Add the setup_type to the DB enum in db/models.py (requires migration)
6. Add a scanner entry (or manual watchlist logic) so tickers get tagged
   with your new setup_type
"""

from __future__ import annotations

import logging
from typing import Callable

from signals.base import SignalResult
from signals.breakout import check_breakout
from signals.episodic_pivot import check_episodic_pivot
from signals.parabolic_short import check_parabolic_short

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter wrappers — normalise each strategy to a common (ticker, **ctx) call
# ---------------------------------------------------------------------------

def _eval_breakout(ticker: str, **ctx) -> SignalResult | None:
    return check_breakout(
        ticker,
        ctx["candles_1m"],
        ctx["daily_closes"],
        ctx["daily_volumes"],
        ctx["current_price"],
        ctx["current_volume"],
        ctx.get("config"),
        daily_lows=ctx.get("daily_lows"),
        daily_highs=ctx.get("daily_highs"),
    )


def _eval_episodic_pivot(ticker: str, **ctx) -> SignalResult | None:
    return check_episodic_pivot(
        ticker,
        ctx["candles_1m"],
        ctx["daily_volumes"],
        ctx["current_price"],
        ctx["current_volume"],
        ctx.get("gap_pct", 0.0),
        ctx.get("config"),
        daily_highs=ctx.get("daily_highs"),
        daily_lows=ctx.get("daily_lows"),
        daily_closes=ctx.get("daily_closes"),
    )


def _eval_parabolic_short(ticker: str, **ctx) -> SignalResult | None:
    return check_parabolic_short(
        ticker,
        ctx["candles_1m"],
        ctx["daily_closes"],
        ctx["current_price"],
        ctx["current_volume"],
        ctx.get("config"),
        daily_highs=ctx.get("daily_highs"),
    )


# ---------------------------------------------------------------------------
# Registry — maps setup_type string → adapter callable
# ---------------------------------------------------------------------------

StrategyFn = Callable[..., SignalResult | None]

STRATEGY_REGISTRY: dict[str, StrategyFn] = {
    "breakout": _eval_breakout,
    "episodic_pivot": _eval_episodic_pivot,
    "parabolic_short": _eval_parabolic_short,
}


def evaluate_signal(setup_type: str, ticker: str, **ctx) -> SignalResult | None:
    """
    Look up *setup_type* in the registry and evaluate the strategy.

    All available context (candles_1m, daily_closes, daily_volumes,
    current_price, current_volume, gap_pct, config, …) should be passed
    as keyword arguments.  Each adapter picks what it needs.

    Returns SignalResult on a valid signal, None otherwise.
    """
    checker = STRATEGY_REGISTRY.get(setup_type)
    if checker is None:
        logger.warning("Unknown setup_type '%s' — skipping", setup_type)
        return None
    return checker(ticker, **ctx)
