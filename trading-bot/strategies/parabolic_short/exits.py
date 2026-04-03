"""
Parabolic short exit logic.

Custom exit: cover at 10d/20d MA profit targets.
Extracted from monitor/position_tracker.py.
"""

from __future__ import annotations

import logging

from core.loader import ExitAction
from signals.base import compute_sma

logger = logging.getLogger(__name__)


def check_parabolic_target(
    pos,
    current_price: float,
    daily_closes: list[float],
) -> ExitAction | None:
    """
    For parabolic short positions, cover at 10d/20d MA profit targets.

    - Cover half at 10d MA (partial exit)
    - Cover remainder at 20d MA (full close)

    Returns ExitAction if a target was hit, None otherwise.
    """
    if pos.side != "short":
        return None

    if not daily_closes or len(daily_closes) < 20:
        return None

    ma10 = compute_sma(daily_closes, 10)
    ma20 = compute_sma(daily_closes, 20)

    # Cover half at 10d MA
    if not pos.partial_exit_done and ma10 is not None and current_price <= ma10:
        logger.info(
            "Parabolic target: %s price %.2f <= 10d MA %.2f — partial cover",
            pos.ticker, current_price, ma10,
        )
        return ExitAction(action="partial", reason="parabolic_target")

    # Cover remainder at 20d MA
    if pos.partial_exit_done and ma20 is not None and current_price <= ma20:
        logger.info(
            "Parabolic target: %s price %.2f <= 20d MA %.2f — full cover",
            pos.ticker, current_price, ma20,
        )
        return ExitAction(action="close", reason="parabolic_target")

    return None
