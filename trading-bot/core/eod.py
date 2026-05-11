"""Shared end-of-day helpers used by both the Alpaca bot (main.py) and the
IBKR bot (main_ib.py).

Originally these lived inline in main.py. They were extracted so the IB bot's
3:55 PM EOD job can produce the same Telegram summary shape as Alpaca's
without copy-pasting (and inevitably drifting from) the breakdown logic.

Public API:
    SETUP_LABELS                       — friendly labels for setup_type prefixes
    compute_eod_strategy_breakdown(..) — counts of opened/closed/failed by
                                         setup + A/B/C variant for trade_date
    compute_eod_r_totals(..)           — summed per-trade R-multiples for
                                         realized (closed today) + unrealized
                                         (currently open) positions
    fmt_r_signed(..)                   — "+2.50R" / "-1.30R" — used by both
                                         bots' EOD summary so the format is
                                         identical
    fmt_dollar_signed(..)              — "+$1,234.56" / "-$1,234.56" — ditto
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


def compute_eod_r_totals(
    trade_date,
    db_engine: Any,
    current_prices: dict[str, float] | None = None,
) -> tuple[float, float]:
    """Sum per-trade R-multiples for the EOD Telegram summary.

    R is defined as ``pnl / (|entry - initial_stop| × shares)`` per the
    convention already used in ``api/routes/performance.py::_r_multiple`` and
    ``api/routes/portfolio.py``. We return *sums* of per-position R (not
    averages) so the daily Telegram line reads like a portfolio-normalized
    P&L: e.g. "+2.50R" means risk-adjusted gain across all today's activity.

    Args:
        trade_date: ET date being summarized (e.g. ``date(2026, 5, 11)``).
        db_engine: SQLAlchemy engine for this bot's books (Alpaca →
            ``trading_bot.db``, IB → ``trading_bot_ib.db``).
        current_prices: ``ticker → last price`` map used for unrealized R.
            Missing/zero prices and positions with invalid risk are skipped.
            Passing ``None`` yields ``unrealized_r = 0.0``.

    Returns:
        ``(realized_r, unrealized_r)``. Positions with zero/invalid risk
        (entry == initial_stop) or non-positive shares are skipped — they
        cannot occur in production data but the guard keeps the metric
        defined when sandbox/test rows are present.
    """
    from db.models import Position, get_session

    day_start_et = ET.localize(datetime.combine(trade_date, dtime.min))
    day_end_et = day_start_et + timedelta(days=1)
    day_start_utc = day_start_et.astimezone(pytz.UTC).replace(tzinfo=None)
    day_end_utc = day_end_et.astimezone(pytz.UTC).replace(tzinfo=None)

    with get_session(db_engine) as session:
        closed = session.query(Position).filter(
            Position.is_open == False,  # noqa: E712 — SQLAlchemy filter
            Position.closed_at >= day_start_utc,
            Position.closed_at < day_end_utc,
        ).all()
        open_rows = session.query(Position).filter_by(is_open=True).all()

    realized_r = 0.0
    for p in closed:
        risk_per_share = abs(p.entry_price - p.initial_stop_price)
        if risk_per_share <= 0 or p.shares <= 0 or p.realized_pnl is None:
            continue
        realized_r += p.realized_pnl / (risk_per_share * p.shares)

    unrealized_r = 0.0
    if current_prices:
        for p in open_rows:
            price = current_prices.get(p.ticker)
            if price is None or price <= 0:
                continue
            risk_per_share = abs(p.entry_price - p.initial_stop_price)
            if risk_per_share <= 0 or p.shares <= 0:
                continue
            unrealized_r += p.unrealized_pnl(price) / (risk_per_share * p.shares)

    return realized_r, unrealized_r


def fmt_r_signed(r: float) -> str:
    """Format an R-multiple with an explicit sign, e.g. ``+2.50R`` / ``-1.30R``.

    Negative values already carry ``-`` from the format spec; positives get
    an explicit ``+`` so the Telegram summary is unambiguous at a glance.
    """
    return f"+{r:.2f}R" if r >= 0 else f"{r:.2f}R"


def fmt_dollar_signed(v: float) -> str:
    """Format a dollar amount with an explicit sign + thousands separator.

    Examples: ``+$1,234.56``, ``-$420.00``. Distinct from Python's default
    ``${v:,.2f}`` which would emit ``$-420.00`` (sign in the wrong place).
    """
    if v >= 0:
        return f"+${v:,.2f}"
    return f"-${abs(v):,.2f}"
