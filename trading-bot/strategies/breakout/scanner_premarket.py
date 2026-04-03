"""
Breakout premarket scanner.

Promotes breakout candidates from 'ready' to 'active' stage for today's trading.
"""

from __future__ import annotations

import logging

from scanner.watchlist_manager import promote_ready_to_active

logger = logging.getLogger(__name__)


def promote_ready_candidates(db_engine, today) -> list[dict]:
    """
    Promote breakout entries from ready -> active for today.

    Returns list of promoted candidate dicts (for watchlist merge).
    """
    from scanner.watchlist_manager import get_active_watchlist

    count = promote_ready_to_active(today, db_engine)
    logger.info("Breakout premarket: promoted %d candidates to active", count)

    # Return the promoted entries for logging/notification
    if count > 0:
        all_active = get_active_watchlist(db_engine)
        return [c for c in all_active if c.get("setup_type") == "breakout"]
    return []
