"""
Signal strategy registry.

LEGACY MODULE — kept for backward compatibility with tests and other code
that imports `evaluate_signal` from here. The live orchestrator (main.py)
now uses strategy plugins directly via core.loader.

To add a new strategy:
1. Create strategies/<name>/ with plugin.py, scanner.py, signal.py, backtest.py, config.yaml
2. Implement PLUGIN satisfying the StrategyPlugin protocol
3. Add the strategy name to strategies.enabled in config.yaml
4. No changes needed to this file, main.py, or db/models.py
"""

from __future__ import annotations

import logging
from typing import Callable

from signals.base import SignalResult
from strategies.breakout.signal import check_breakout
from strategies.episodic_pivot.signal import check_episodic_pivot
from strategies.parabolic_short.signal import check_parabolic_short

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
        minutes_since_open=ctx.get("minutes_since_open"),
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
        minutes_since_open=ctx.get("minutes_since_open"),
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
# Legacy registry — used by tests. Live path uses plugin.evaluate_signal().
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
    current_price, current_volume, gap_pct, config, ...) should be passed
    as keyword arguments.  Each adapter picks what it needs.

    Returns SignalResult on a valid signal, None otherwise.
    """
    checker = STRATEGY_REGISTRY.get(setup_type)
    if checker is None:
        logger.warning("Unknown setup_type '%s' — skipping", setup_type)
        return None
    return checker(ticker, **ctx)
