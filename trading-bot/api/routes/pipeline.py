"""Pipeline timeline endpoints — shows daily job schedule and execution history."""

from __future__ import annotations

import json
import os
import logging
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Query

from api.constants import PIPELINE_SCHEDULE, JOB_LABELS, PHASE_META, PHASE_ORDER
from db.models import JobExecution, get_engine, get_session

router = APIRouter()
logger = logging.getLogger(__name__)

STATUS_FILE = Path(__file__).parent.parent.parent / "bot_status.json"

STALE_THRESHOLD_SECONDS = 10 * 60  # 10 minutes

# Cache trading-day lookups to avoid repeated Alpaca API calls
_trading_day_cache: dict[str, bool] = {}


def _today_et() -> date:
    """Return today's date in US/Eastern."""
    import pytz
    return datetime.now(pytz.timezone("America/New_York")).date()


def _is_trading_day(target_date: date) -> bool:
    """Check if target_date is a trading day via Alpaca calendar API."""
    key = str(target_date)
    if key in _trading_day_cache:
        return _trading_day_cache[key]

    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetCalendarRequest

        client = TradingClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_SECRET_KEY", ""),
            paper=os.environ.get("TRADING_MODE", "paper") == "paper",
        )
        req = GetCalendarRequest(start=target_date, end=target_date)
        cal = client.get_calendar(req)
        result = len(cal) > 0
    except Exception:
        # Fallback: weekday check (misses holidays)
        result = target_date.weekday() < 5

    _trading_day_cache[key] = result
    return result


def _last_trading_date(session) -> date | None:
    """Most recent date with any job execution."""
    row = (
        session.query(JobExecution.trade_date)
        .order_by(JobExecution.trade_date.desc())
        .first()
    )
    return row[0] if row else None


def _read_status() -> dict:
    """Read bot_status.json for current phase/next job info."""
    if not STATUS_FILE.exists():
        return {}
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _apply_stale_transform(exec_dict: dict) -> dict:
    """Convert running -> failed(timeout) for executions older than threshold."""
    if exec_dict["status"] != "running" or not exec_dict["started_at"]:
        return exec_dict
    try:
        started = datetime.fromisoformat(exec_dict["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()
        if age > STALE_THRESHOLD_SECONDS:
            exec_dict["status"] = "failed"
            exec_dict["failure_reason"] = "timeout"
    except Exception:
        pass
    return exec_dict


def _serialize_execution(r: JobExecution, include_error: bool = True) -> dict:
    """Serialize a JobExecution row to a dict."""
    d = {
        "id": r.id,
        "job_id": r.job_id,
        "label": r.job_label,
        "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "duration_seconds": r.duration_seconds,
        "result_summary": r.result_summary,
        "error": (r.error[:200] if r.error else None) if include_error else None,
        "failure_reason": None,
    }
    return _apply_stale_transform(d)


def _build_schedule_dicts() -> list[dict]:
    """Build the static schedule as a list of dicts for the API response."""
    return [
        {
            "job_id": job["job_id"],
            "label": job["label"],
            "time": job["time"],
            "category": job["category"],
            "phase": job["phase"],
            "description": job["description"],
            "display_day_offset": job["display_day_offset"],
        }
        for job in PIPELINE_SCHEDULE
    ]


def _merge_schedule_with_executions(
    executions: list[dict],
    trade_date: date,
    is_past: bool = False,
) -> list[dict]:
    """Merge static schedule with execution data.

    Returns one row per scheduled job (always 9), with execution data filled in
    where available. Missing jobs get status 'missed' (past) or 'upcoming' (today/future).
    """
    exec_map = {e["job_id"]: e for e in executions}
    merged = []

    for job in PIPELINE_SCHEDULE:
        job_id = job["job_id"]
        exec_data = exec_map.get(job_id)

        if exec_data:
            row = {
                "job_id": job_id,
                "label": job["label"],
                "phase": job["phase"],
                "description": job["description"],
                "scheduled_time": job["time"],
                "category": job["category"],
                "display_day_offset": job["display_day_offset"],
                "status": exec_data["status"],
                "failure_reason": exec_data.get("failure_reason"),
                "started_at": exec_data.get("started_at"),
                "finished_at": exec_data.get("finished_at"),
                "duration_seconds": exec_data.get("duration_seconds"),
                "result_summary": exec_data.get("result_summary"),
                "error": exec_data.get("error"),
            }
        else:
            row = {
                "job_id": job_id,
                "label": job["label"],
                "phase": job["phase"],
                "description": job["description"],
                "scheduled_time": job["time"],
                "category": job["category"],
                "display_day_offset": job["display_day_offset"],
                "status": "missed" if is_past else "upcoming",
                "failure_reason": None,
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "result_summary": None,
                "error": None,
            }
        merged.append(row)

    return merged


def _compute_day_summary(jobs: list[dict]) -> str:
    """Compute a summary status for a day based on its merged jobs."""
    statuses = [j["status"] for j in jobs]
    if all(s == "success" for s in statuses):
        return "all_passed"
    if any(s == "failed" for s in statuses):
        return "failures"
    if any(s in ("missed", "skipped") for s in statuses):
        return "some_issues"
    if any(s in ("running", "upcoming") for s in statuses):
        return "in_progress"
    return "no_data"


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

    trading_day = _is_trading_day(trade_date)

    # Static schedule with new fields
    schedule = _build_schedule_dicts()

    # Execution history from DB
    engine = get_engine()
    last_trade_date = None
    executions = []

    with get_session(engine) as session:
        rows = (
            session.query(JobExecution)
            .filter(JobExecution.trade_date == trade_date)
            .order_by(JobExecution.started_at)
            .all()
        )
        for r in rows:
            executions.append(_serialize_execution(r, include_error=True))

        # Get last trading date for non-trading day display
        if not trading_day:
            last_trade_date = _last_trading_date(session)

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
        "is_trading_day": trading_day,
        "last_trading_date": str(last_trade_date) if last_trade_date else None,
        "schedule": schedule,
        "executions": executions,
        "current_phase": phase,
        "next_job": next_job,
        "phases": PHASE_META,
        "phase_order": PHASE_ORDER,
    }


@router.get("/pipeline/history")
def get_pipeline_history(days: int = Query(14, ge=1, le=30)):
    """Return job execution history for the last N trading days, merged with schedule."""
    engine = get_engine()
    with get_session(engine) as session:
        # Only query scheduled pipeline jobs (exclude recurring heartbeat/reconcile)
        scheduled_job_ids = [job["job_id"] for job in PIPELINE_SCHEDULE]
        rows = (
            session.query(JobExecution)
            .filter(JobExecution.job_id.in_(scheduled_job_ids))
            .order_by(JobExecution.trade_date.desc(), JobExecution.started_at)
            .limit(days * 10)
            .all()
        )

        # Group executions by trade_date
        by_date: dict[str, list[dict]] = {}
        all_executions: list[dict] = []

        for r in rows:
            key = str(r.trade_date)
            exec_dict = _serialize_execution(r, include_error=True)
            by_date.setdefault(key, []).append(exec_dict)
            all_executions.append({
                **exec_dict,
                "date": key,
            })

        # Only return the most recent N unique dates
        sorted_dates = sorted(by_date.keys(), reverse=True)[:days]

        result_days = []
        for d in sorted_dates:
            date_obj = datetime.strptime(d, "%Y-%m-%d").date()
            is_past = date_obj < _today_et()
            merged_jobs = _merge_schedule_with_executions(
                by_date[d], date_obj, is_past=is_past
            )
            summary = _compute_day_summary(merged_jobs)
            result_days.append({
                "date": d,
                "is_trading_day": _is_trading_day(date_obj),
                "summary": summary,
                "jobs": merged_jobs,
            })

    return {
        "days": result_days,
        "recent_executions": all_executions[:20],
    }
