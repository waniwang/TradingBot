"""Signals endpoint — today's fired signals."""

from __future__ import annotations

from datetime import datetime, date

from fastapi import APIRouter

from db.models import Signal, get_session
from api.deps import get_db_engine

router = APIRouter()


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

        return [
            {
                "id": s.id,
                "time": s.fired_at.strftime("%H:%M:%S"),
                "fired_at": s.fired_at.isoformat(),
                "ticker": s.ticker,
                "setup": s.setup_type.replace("_", " ").title(),
                "entry": s.entry_price,
                "stop": s.stop_price,
                "gap_pct": round(s.gap_pct, 1) if s.gap_pct else None,
                "acted": s.acted_on,
            }
            for s in signals
        ]
