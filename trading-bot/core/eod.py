"""Shared end-of-day helpers used by both the Alpaca bot (main.py) and the
IBKR bot (main_ib.py).

Originally these lived inline in main.py. They were extracted so the IB bot's
3:55 PM EOD job can produce the same Telegram summary shape as Alpaca's
without copy-pasting (and inevitably drifting from) the breakdown logic.

Public API:
    SETUP_LABELS                       — friendly labels for setup_type prefixes
    compute_eod_strategy_breakdown(..) — counts of opened/closed/failed by
                                         setup + A/B/C variant for trade_date
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from typing import Any

import pytz

ET = pytz.timezone("America/New_York")


# Friendly labels for the EOD summary. Keep keys covering every setup_type
# the EOD breakdown might encounter — e.g. ep_earnings, ep_earnings_c (the
# strategy-suffixed Position rows), ep_news, ep_news_c.
SETUP_LABELS: dict[str, str] = {
    "ep_earnings": "EP Earnings",
    "ep_earnings_a": "EP Earnings",
    "ep_earnings_b": "EP Earnings",
    "ep_earnings_c": "EP Earnings",
    "ep_news": "EP News",
    "ep_news_a": "EP News",
    "ep_news_b": "EP News",
    "ep_news_c": "EP News",
}


def compute_eod_strategy_breakdown(
    trade_date,
    db_engine: Any,
) -> tuple[str, int, int, int]:
    """Summarize today's strategy executions for the EOD Telegram summary.

    Args:
        trade_date: ET date being summarized (e.g. ``date(2026, 4, 30)``).
        db_engine: SQLAlchemy engine for the bot whose books we're summarizing
            (Alpaca → trading_bot.db, IB → trading_bot_ib.db).

    Returns:
        ``(strategy_line, opened_count, closed_count, failed_count)``.

        * ``strategy_line`` looks like ``"EP Earnings A(2), EP News C(1)"``,
          or ``"none"`` if no positions opened today. Counts are by
          (setup-label, A/B/C variant) bucket.
        * ``opened_count`` — Position rows whose ``opened_at`` falls within
          the ET trade_date window.
        * ``closed_count`` — Position rows whose ``closed_at`` falls within
          the ET trade_date window.
        * ``failed_count`` — Order rows created today whose status is
          ``cancelled`` or ``rejected``.

    Imports are kept inside the function to avoid forcing the heavy SQLAlchemy
    + variation modules onto callers that just need ``SETUP_LABELS``.
    """
    from db.models import Order, Position, get_session
    from api.variation import resolve_variations_batch

    day_start_et = ET.localize(datetime.combine(trade_date, dtime.min))
    day_end_et = day_start_et + timedelta(days=1)
    day_start_utc = day_start_et.astimezone(pytz.UTC).replace(tzinfo=None)
    day_end_utc = day_end_et.astimezone(pytz.UTC).replace(tzinfo=None)

    with get_session(db_engine) as session:
        opened = session.query(Position).filter(
            Position.opened_at >= day_start_utc,
            Position.opened_at < day_end_utc,
        ).all()
        closed_n = session.query(Position).filter(
            Position.closed_at >= day_start_utc,
            Position.closed_at < day_end_utc,
        ).count()
        failed_n = session.query(Order).filter(
            Order.created_at >= day_start_utc,
            Order.created_at < day_end_utc,
            Order.status.in_(("cancelled", "rejected")),
        ).count()

        if not opened:
            return "none", 0, closed_n, failed_n

        # Resolve A/B/C variant for each opened position via Watchlist join.
        keys = [(p.ticker, p.setup_type, p.opened_at) for p in opened]
        variations = resolve_variations_batch(session, keys)

    counts: dict[tuple[str, str], int] = {}
    for p in opened:
        label = SETUP_LABELS.get(p.setup_type, p.setup_type)
        variant = variations.get((p.ticker, p.setup_type, p.opened_at)) or "?"
        counts[(label, variant)] = counts.get((label, variant), 0) + 1

    parts = [f"{label} {variant}({n})" for (label, variant), n in sorted(counts.items())]
    return ", ".join(parts), len(opened), closed_n, failed_n
