"""Pipeline timeline endpoints — shows daily job schedule and execution history."""

from __future__ import annotations

import json
from datetime import datetime, date, timezone
from pathlib import Path

from fastapi import APIRouter, Query

from api.constants import PIPELINE_SCHEDULE, JOB_LABELS
from db.models import JobExecution, get_engine, get_session

router = APIRouter()

STATUS_FILE = Path(__file__).parent.parent.parent / "bot_status.json"


def _today_et() -> date:
    """Return today's date in US/Eastern."""
    import pytz
    return datetime.now(pytz.timezone("America/New_York")).date()


def _read_status() -> dict:
    """Read bot_status.json for current phase/next job info."""
    if not STATUS_FILE.exists():
        return {}
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


@router.get("/pipeline")
def get_pipeline(date: str | None = Query(None, description="YYYY-MM-DD, defaults to today ET")):
    """Return today's pipeline schedule merged with execution history."""
    if date:
        try:
            trade_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            trade_date = _today_et()
    else:
        trade_date = _today_et()

    # Static schedule
    schedule = [
        {"job_id": job_id, "label": label, "time": time_str, "category": cat}
        for job_id, label, time_str, cat in PIPELINE_SCHEDULE
    ]

    # Execution history from DB
    engine = get_engine()
    executions = []
    with get_session(engine) as session:
        rows = (
            session.query(JobExecution)
            .filter(JobExecution.trade_date == trade_date)
            .order_by(JobExecution.started_at)
            .all()
        )
        for r in rows:
            executions.append({
                "id": r.id,
                "job_id": r.job_id,
                "label": r.job_label,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_seconds": r.duration_seconds,
                "result_summary": r.result_summary,
                "error": r.error[:200] if r.error else None,
            })

    # Current state from bot_status.json
    raw = _read_status()
    phase = raw.get("phase", "unknown")

    # Next job info
    next_job = None
    next_job_raw = raw.get("next_job")
    next_time = raw.get("next_job_time")
    if next_job_raw and next_job_raw not in ("heartbeat", "reconcile_positions"):
        countdown = None
        if next_time:
            try:
                next_dt = datetime.fromisoformat(next_time)
                delta = (next_dt - datetime.now(timezone.utc).astimezone(next_dt.tzinfo)).total_seconds()
                countdown = max(0, int(delta))
            except Exception:
                pass
        next_job = {
            "job_id": next_job_raw,
            "label": JOB_LABELS.get(next_job_raw, next_job_raw),
            "time": next_time,
            "countdown_seconds": countdown,
        }

    return {
        "trade_date": str(trade_date),
        "schedule": schedule,
        "executions": executions,
        "current_phase": phase,
        "next_job": next_job,
    }


@router.get("/pipeline/history")
def get_pipeline_history(days: int = Query(5, ge=1, le=30)):
    """Return job execution history for the last N trading days."""
    engine = get_engine()
    with get_session(engine) as session:
        rows = (
            session.query(JobExecution)
            .order_by(JobExecution.trade_date.desc(), JobExecution.started_at)
            .limit(days * 15)  # ~9 jobs per day, with margin
            .all()
        )

    # Group by trade_date
    by_date: dict[str, list] = {}
    for r in rows:
        key = str(r.trade_date)
        by_date.setdefault(key, []).append({
            "id": r.id,
            "job_id": r.job_id,
            "label": r.job_label,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_seconds": r.duration_seconds,
            "result_summary": r.result_summary,
        })

    # Only return the most recent N unique dates
    sorted_dates = sorted(by_date.keys(), reverse=True)[:days]
    return {
        "days": [{"date": d, "executions": by_date[d]} for d in sorted_dates]
    }
