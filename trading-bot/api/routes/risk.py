"""Risk exposure endpoint — daily/weekly P&L vs limits."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from fastapi import APIRouter

from db.models import Position, get_engine, get_session

router = APIRouter()


def _safe_pnl_sum(positions) -> float:
    total = 0.0
    for p in positions:
        val = p.realized_pnl
        if val is None or math.isnan(val):
            continue
        total += val
    return total


@router.get("/risk")
def get_risk():
    engine = get_engine()
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())

    with get_session(engine) as session:
        # Daily closed P&L
        daily_closed = (
            session.query(Position)
            .filter(
                Position.is_open == False,
                Position.closed_at >= datetime.combine(today, datetime.min.time()),
            )
            .all()
        )
        daily_pnl = _safe_pnl_sum(daily_closed)

        # Weekly closed P&L
        weekly_closed = (
            session.query(Position)
            .filter(
                Position.is_open == False,
                Position.closed_at >= datetime.combine(week_start, datetime.min.time()),
            )
            .all()
        )
        weekly_pnl = _safe_pnl_sum(weekly_closed)

        # Open positions count
        open_count = session.query(Position).filter(Position.is_open == True).count()

    # Read limits from config or use defaults
    # These match the defaults in config.yaml: daily -3%, weekly -5%, max 4 positions
    daily_limit_pct = -3.0
    weekly_limit_pct = -5.0
    max_positions = 4

    return {
        "daily_pnl": round(daily_pnl, 2),
        "daily_limit_pct": daily_limit_pct,
        "weekly_pnl": round(weekly_pnl, 2),
        "weekly_limit_pct": weekly_limit_pct,
        "open_positions": open_count,
        "max_positions": max_positions,
    }
