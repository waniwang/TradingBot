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

    # Read limits live from config.yaml so the dashboard reflects whatever
    # is currently configured. A value of 0 (or <=0) means the limit is
    # disabled — return null so the frontend can render "—" instead of
    # showing a stale or meaningless threshold.
    from api.deps import get_config
    cfg = get_config()
    risk_cfg = cfg.get("risk", {})
    daily_raw = float(risk_cfg.get("daily_loss_limit_pct", 0) or 0)
    weekly_raw = float(risk_cfg.get("weekly_loss_limit_pct", 0) or 0)
    max_pos_raw = int(risk_cfg.get("max_positions", 0) or 0)

    daily_limit_pct = -daily_raw if daily_raw > 0 else None
    weekly_limit_pct = -weekly_raw if weekly_raw > 0 else None
    max_positions = max_pos_raw if max_pos_raw > 0 else None

    return {
        "daily_pnl": round(daily_pnl, 2),
        "daily_limit_pct": daily_limit_pct,
        "weekly_pnl": round(weekly_pnl, 2),
        "weekly_limit_pct": weekly_limit_pct,
        "open_positions": open_count,
        "max_positions": max_positions,
    }
