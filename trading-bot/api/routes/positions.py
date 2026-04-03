"""Positions endpoints — open and closed."""

from __future__ import annotations

from datetime import datetime, date

from fastapi import APIRouter

from db.models import Position, get_session
from api.deps import get_db_engine, get_alpaca

router = APIRouter()


@router.get("/positions")
def get_open_positions():
    engine = get_db_engine()
    alpaca = get_alpaca()

    with get_session(engine) as session:
        positions = session.query(Position).filter_by(is_open=True).all()

        result = []
        for p in positions:
            remaining = p.shares - p.partial_exit_shares
            try:
                bar = alpaca.get_latest_bar(p.ticker)
                current = bar["last_price"]
            except Exception:
                current = p.entry_price

            result.append({
                "id": p.id,
                "ticker": p.ticker,
                "setup": p.setup_type.replace("_", " ").title(),
                "side": p.side.upper(),
                "shares": remaining,
                "entry": p.entry_price,
                "stop": p.stop_price,
                "current": current,
                "gain_pct": round(p.gain_pct(current), 2),
                "unrealized_pnl": round(p.unrealized_pnl(current), 2),
                "days": p.days_held,
                "partial": p.partial_exit_done,
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            })

    return result


@router.get("/positions/closed")
def get_closed_positions(limit: int = 50):
    engine = get_db_engine()

    with get_session(engine) as session:
        positions = (
            session.query(Position)
            .filter_by(is_open=False)
            .order_by(Position.closed_at.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                "id": p.id,
                "date": p.closed_at.isoformat() if p.closed_at else None,
                "ticker": p.ticker,
                "setup": p.setup_type.replace("_", " ").title(),
                "side": p.side.upper(),
                "entry": p.entry_price,
                "exit": p.exit_price,
                "pnl": p.realized_pnl or 0.0,
                "days": p.days_held,
                "reason": (p.exit_reason or "").replace("_", " "),
            }
            for p in positions
        ]
