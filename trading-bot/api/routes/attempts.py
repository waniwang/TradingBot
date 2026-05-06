"""Trade Attempts endpoint — single source of truth for the History + Today views.

Each row is one attempt by the bot to enter a position. Joins Signal + Order +
Position so the dashboard never has to mentally JOIN across tables. The
"outcome" field collapses the underlying status combinations into a small set
of operator-meaningful labels.

  filled_open      — order filled, position is currently open
  filled_closed    — order filled, position has been closed
  submitted        — order is working at the broker
  did_not_fill     — order was cancelled (limit didn't print, bot timed out, etc.)
  broker_rejected  — broker refused the order

We deliberately do NOT distinguish Alpaca's "expired" GTC status today, because
the bot only places day orders that it cancels itself on timeout. If GTC stop
orders ever get their own Order rows, add an "expired" outcome.
"""

from __future__ import annotations

from datetime import datetime, date

from fastapi import APIRouter, Query

from db.models import Order, Position, Signal, get_session
from api.deps import get_db_engine
from api.variation import resolve_variations_batch


router = APIRouter()


# Order.status → outcome (when no Position row exists)
_NO_POSITION_OUTCOME = {
    "pending": "submitted",
    "submitted": "submitted",
    "partially_filled": "submitted",
    "cancelled": "did_not_fill",
    "rejected": "broker_rejected",
    # "filled" with no Position is a transient race during fill-confirm; render
    # as submitted until the Position row catches up.
    "filled": "submitted",
}


def _classify(order: Order | None, position: Position | None) -> str:
    """Map (Order, Position) → outcome string."""
    if position is not None:
        return "filled_open" if position.is_open else "filled_closed"
    if order is None:
        # Signal exists but no Order — shouldn't happen on the live path, but
        # we render it defensively rather than 500 the page.
        return "submitted"
    return _NO_POSITION_OUTCOME.get(order.status, "submitted")


def _detail_for(outcome: str, order: Order | None, position: Position | None) -> str | None:
    """Short human-readable explainer shown in the table's Detail column."""
    if outcome == "filled_closed" and position is not None and position.exit_reason:
        return position.exit_reason.replace("_", " ")
    if outcome == "filled_open" and position is not None:
        return "running"
    if outcome == "did_not_fill":
        return "limit not reached"
    if outcome == "broker_rejected":
        return "broker refused"
    if outcome == "submitted":
        return "working at broker"
    return None


def _serialize(signals: list[Signal], session) -> list[dict]:
    """Shape Signal rows into Trade Attempts. One row per Signal."""
    if not signals:
        return []

    variations = resolve_variations_batch(
        session, [(s.ticker, s.setup_type, s.fired_at) for s in signals]
    )

    # Latest Order per signal_id so we can show fill state.
    signal_ids = [s.id for s in signals if s.id is not None]
    latest_orders: dict[int, Order] = {}
    if signal_ids:
        for o in (
            session.query(Order)
            .filter(Order.signal_id.in_(signal_ids))
            .order_by(Order.created_at.desc())
            .all()
        ):
            latest_orders.setdefault(o.signal_id, o)

    # Position lookup by entry_order_id. A Position is created from a
    # filled order, so the Order.id → Position mapping is unambiguous.
    order_ids = [o.id for o in latest_orders.values()]
    positions_by_order: dict[int, Position] = {}
    if order_ids:
        for p in (
            session.query(Position)
            .filter(Position.entry_order_id.in_(order_ids))
            .all()
        ):
            if p.entry_order_id is not None:
                positions_by_order[p.entry_order_id] = p

    out = []
    for s in signals:
        order = latest_orders.get(s.id)
        position = positions_by_order.get(order.id) if order else None
        outcome = _classify(order, position)

        # P&L only has meaning for filled trades. For open positions we can't
        # cheaply compute unrealized here without a broker fetch — leave null
        # and let the open-positions table handle it.
        pnl: float | None = None
        if outcome == "filled_closed" and position is not None:
            pnl = position.realized_pnl

        out.append({
            "id": s.id,
            "fired_at": s.fired_at.isoformat(),
            "ticker": s.ticker,
            "setup": s.setup_type.replace("_", " ").title(),
            "setup_raw": s.setup_type,
            "variation": variations[(s.ticker, s.setup_type, s.fired_at)],
            "entry_intended": s.entry_price,
            "stop": s.stop_price,
            "gap_pct": round(s.gap_pct, 1) if s.gap_pct else None,
            "entry_actual": order.filled_avg_price if order else None,
            "exit": position.exit_price if position else None,
            "pnl": pnl,
            "days": position.days_held if position else None,
            "outcome": outcome,
            "detail": _detail_for(outcome, order, position),
            # Raw fields kept for debugging / power-user tooltips
            "order_status": order.status if order else None,
            "order_qty": order.qty if order else None,
            "filled_qty": order.filled_qty if order else None,
        })
    return out


@router.get("/attempts/today")
def get_attempts_today():
    """Today's trade attempts — feeds the Overview page's 'Today's Attempts'."""
    engine = get_db_engine()
    today_start = datetime.combine(date.today(), datetime.min.time())

    with get_session(engine) as session:
        signals = (
            session.query(Signal)
            .filter(Signal.fired_at >= today_start)
            .order_by(Signal.fired_at.desc())
            .all()
        )
        return _serialize(signals, session)


@router.get("/attempts")
def get_attempts(
    limit: int = Query(100, ge=1, le=500, description="Max rows to return"),
):
    """Recent trade attempts across all dates — feeds the History page."""
    engine = get_db_engine()
    with get_session(engine) as session:
        signals = (
            session.query(Signal)
            .order_by(Signal.fired_at.desc())
            .limit(limit)
            .all()
        )
        return _serialize(signals, session)
