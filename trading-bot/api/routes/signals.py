"""Signals endpoint — today's fired signals + historical list for the History page."""

from __future__ import annotations

from datetime import datetime, date

from fastapi import APIRouter, Query

from db.models import Order, Signal, get_session
from api.deps import get_db_engine
from api.variation import resolve_variations_batch

router = APIRouter()


def _serialize(signals: list[Signal], session) -> list[dict]:
    """Shape Signal rows for the dashboard: variation + latest-order fill state."""
    variations = resolve_variations_batch(
        session, [(s.ticker, s.setup_type, s.fired_at) for s in signals]
    )

    # Latest Order per signal_id so the dashboard can show fill status.
    # acted_on=True only means the order was submitted — a cancelled or rejected
    # order also flips that bit, which masked today's MCRI case.
    signal_ids = [s.id for s in signals if s.id is not None]
    latest_orders: dict[int, Order] = {}
    if signal_ids:
        order_rows = (
            session.query(Order)
            .filter(Order.signal_id.in_(signal_ids))
            .order_by(Order.created_at.desc())
            .all()
        )
        for o in order_rows:
            # first hit wins because we ordered desc
            latest_orders.setdefault(o.signal_id, o)

    out = []
    for s in signals:
        order = latest_orders.get(s.id)
        out.append({
            "id": s.id,
            "time": s.fired_at.strftime("%H:%M:%S"),
            "fired_at": s.fired_at.isoformat(),
            "ticker": s.ticker,
            "setup": s.setup_type.replace("_", " ").title(),
            "entry": s.entry_price,
            "stop": s.stop_price,
            "gap_pct": round(s.gap_pct, 1) if s.gap_pct else None,
            "acted": s.acted_on,
            "variation": variations[(s.ticker, s.setup_type, s.fired_at)],
            "order_status": order.status if order else None,
            "filled_qty": order.filled_qty if order else None,
            "filled_avg_price": order.filled_avg_price if order else None,
            "order_qty": order.qty if order else None,
        })
    return out


@router.get("/signals/today")
def get_signals_today():
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


@router.get("/signals/history")
def get_signals_history(
    limit: int = Query(100, ge=1, le=500, description="Max rows to return"),
):
    """Most recent N signals across all dates, newest first.

    Mirrors /positions/closed?limit=N — feeds the History page's signal table.
    """
    engine = get_db_engine()
    with get_session(engine) as session:
        signals = (
            session.query(Signal)
            .order_by(Signal.fired_at.desc())
            .limit(limit)
            .all()
        )
        return _serialize(signals, session)
