"""Pipeline timeline endpoints — shows daily job schedule and execution history."""

from __future__ import annotations

import json
import os
import logging
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Query

from fastapi import HTTPException

from api.constants import (
    PIPELINE_SCHEDULE,
    JOB_LABELS,
    PHASE_META,
    PHASE_ORDER,
    job_to_strategy,
    is_job_active,
)
from api.deps import get_enabled_strategies
from api.variation import resolve_variations_batch
from db.models import (
    JobExecution,
    Watchlist,
    Signal,
    Order,
    Position,
    DailyPnl,
    get_engine,
    get_session,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# BOT_STATUS_FILE lets the IBKR API instance read bot_status_ib.json instead of
# the shared Alpaca bot_status.json. Falls back to bot_status.json.
STATUS_FILE = Path(__file__).parent.parent.parent / os.environ.get(
    "BOT_STATUS_FILE", "bot_status.json"
)

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
            exec_dict["error"] = (
                "This job was still marked as 'running' after 10 minutes. "
                "The bot process likely crashed or was stopped before the job could complete."
            )
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
        "error": r.error if include_error else None,
        "failure_reason": None,
    }
    return _apply_stale_transform(d)


def _active_schedule() -> list[dict]:
    """PIPELINE_SCHEDULE filtered to jobs needed by currently enabled strategies.

    Always-on jobs (intraday_monitor, eod_tasks) are included unconditionally.
    Multi-owner jobs (premarket_scan, subscribe_watchlist) are included if ANY
    of their owners is enabled.
    """
    enabled = set(get_enabled_strategies())
    return [job for job in PIPELINE_SCHEDULE if is_job_active(job["job_id"], enabled)]


def _build_schedule_dicts() -> list[dict]:
    """Build the static schedule as a list of dicts for the API response."""
    return [
        {
            "job_id": job["job_id"],
            "label": job["label"],
            "time": job["time"],
            "end_time": job.get("end_time"),
            "category": job["category"],
            "phase": job["phase"],
            "description": job["description"],
            "display_day_offset": job["display_day_offset"],
            "strategy": job_to_strategy(job["job_id"]),
        }
        for job in _active_schedule()
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

    for job in _active_schedule():
        job_id = job["job_id"]
        exec_data = exec_map.get(job_id)

        strategy = job_to_strategy(job_id)

        base = {
            "job_id": job_id,
            "label": job["label"],
            "phase": job["phase"],
            "description": job["description"],
            "scheduled_time": job["time"],
            "end_time": job.get("end_time"),
            "category": job["category"],
            "display_day_offset": job["display_day_offset"],
            "strategy": strategy,
        }
        if exec_data:
            row = {
                **base,
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
                **base,
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
    """Compute a summary status for a day based on its merged jobs.

    Missed jobs are treated as failures — a scheduled job that never produced a
    JobExecution row is indistinguishable from a failure to the operator.
    """
    statuses = [j["status"] for j in jobs]
    if all(s == "success" for s in statuses):
        return "all_passed"
    if any(s in ("failed", "missed") for s in statuses):
        return "failures"
    if any(s == "skipped" for s in statuses):
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
        scheduled_job_ids = [job["job_id"] for job in _active_schedule()]
        rows = (
            session.query(JobExecution)
            .filter(JobExecution.job_id.in_(scheduled_job_ids))
            .order_by(JobExecution.trade_date.desc(), JobExecution.started_at)
            .limit(days * 40)  # 11 scheduled jobs + up to 10× retries per execute job
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


# ---------------------------------------------------------------------------
# Job detail enrichment
# ---------------------------------------------------------------------------

# Map job_id -> the setup_type / strategy name used in Watchlist, Position, etc.
_JOB_STRATEGY_SETUP = {
    "breakout_nightly_scan": "breakout",
    "ep_earnings_scan": "ep_earnings",
    "ep_news_scan": "ep_news",
    "ep_earnings_execute": "ep_earnings",
    "ep_news_execute": "ep_news",
}


def _et_day_range_utc(trade_date: date) -> tuple[datetime, datetime]:
    """Return (start_utc, end_utc) covering the ET trading day for trade_date."""
    import pytz
    et = pytz.timezone("America/New_York")
    start_et = et.localize(datetime.combine(trade_date, datetime.min.time()))
    end_et = start_et + timedelta(days=1)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def _serialize_watchlist(row: Watchlist) -> dict:
    meta = row.meta or {}
    # Pull a handful of useful metadata fields when present
    entry_price = meta.get("entry_price") or meta.get("close") or meta.get("price")
    gap_pct = meta.get("gap_pct") or meta.get("gap")
    rvol = meta.get("rvol")
    mcap = meta.get("market_cap") or meta.get("mcap")
    # ep_strategy (A/B/C) lives directly on the Watchlist row's meta
    variation = meta.get("ep_strategy")
    return {
        "ticker": row.ticker,
        "setup_type": row.setup_type,
        "stage": row.stage,
        "entry_price": entry_price,
        "gap_pct": gap_pct,
        "rvol": rvol,
        "market_cap": mcap,
        "notes": row.notes,
        "variation": variation,
    }


def _serialize_signal(sig: Signal, order: Order | None, variation: str | None) -> dict:
    return {
        "ticker": sig.ticker,
        "setup_type": sig.setup_type,
        "entry_price": sig.entry_price,
        "stop_price": sig.stop_price,
        "gap_pct": sig.gap_pct,
        "acted_on": sig.acted_on,
        "fired_at": sig.fired_at.isoformat() if sig.fired_at else None,
        "variation": variation,
        "order": (
            {
                "id": order.id,
                "side": order.side,
                "qty": order.qty,
                "price": order.price,
                "status": order.status,
                "filled_qty": order.filled_qty,
                "filled_avg_price": order.filled_avg_price,
            }
            if order
            else None
        ),
    }


def _serialize_position_closed(p: Position, variation: str | None) -> dict:
    return {
        "ticker": p.ticker,
        "setup_type": p.setup_type,
        "side": p.side,
        "shares": p.shares,
        "entry_price": p.entry_price,
        "exit_price": p.exit_price,
        "exit_reason": p.exit_reason,
        "realized_pnl": p.realized_pnl,
        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        "variation": variation,
    }


def _enrich_scan_job(session, job_id: str, trade_date: date) -> dict:
    """Enrichment for scan jobs: return Watchlist entries for the trade_date."""
    q = session.query(Watchlist).filter(Watchlist.scan_date == trade_date)

    setup_type = _JOB_STRATEGY_SETUP.get(job_id)
    if setup_type:
        q = q.filter(Watchlist.setup_type == setup_type)
    else:
        # premarket_scan / subscribe_watchlist: all stage=active for that day
        q = q.filter(Watchlist.stage.in_(("active", "triggered")))

    rows = q.order_by(Watchlist.setup_type, Watchlist.ticker).all()

    tickers = [_serialize_watchlist(r) for r in rows]
    breakdown: dict[str, int] = {}
    for r in rows:
        breakdown[r.setup_type] = breakdown.get(r.setup_type, 0) + 1

    return {"tickers": tickers, "strategy_breakdown": breakdown}


def _enrich_execute_job(session, job_id: str, trade_date: date) -> dict:
    """Enrichment for execute jobs: return signals + their orders."""
    setup_type = _JOB_STRATEGY_SETUP.get(job_id)
    start_utc, end_utc = _et_day_range_utc(trade_date)

    q = session.query(Signal).filter(
        Signal.fired_at >= start_utc.replace(tzinfo=None),
        Signal.fired_at < end_utc.replace(tzinfo=None),
    )
    if setup_type:
        q = q.filter(Signal.setup_type == setup_type)

    signals = q.order_by(Signal.fired_at).all()

    variations = resolve_variations_batch(
        session, [(s.ticker, s.setup_type, s.fired_at) for s in signals]
    )

    out = []
    for sig in signals:
        order = sig.orders[0] if sig.orders else None
        variation = variations[(sig.ticker, sig.setup_type, sig.fired_at)]
        out.append(_serialize_signal(sig, order, variation))

    entered = sum(1 for s in signals if s.acted_on)
    return {
        "signals": out,
        "entered_count": entered,
        "signal_count": len(signals),
    }


def _enrich_monitor_job(session, trade_date: date) -> dict:
    """Enrichment for intraday_monitor / eod_tasks: positions closed that day."""
    start_utc, end_utc = _et_day_range_utc(trade_date)
    rows = (
        session.query(Position)
        .filter(
            Position.is_open == False,  # noqa: E712
            Position.closed_at >= start_utc.replace(tzinfo=None),
            Position.closed_at < end_utc.replace(tzinfo=None),
        )
        .order_by(Position.closed_at)
        .all()
    )
    variations = resolve_variations_batch(
        session, [(p.ticker, p.setup_type, p.opened_at) for p in rows]
    )
    positions = [
        _serialize_position_closed(p, variations[(p.ticker, p.setup_type, p.opened_at)])
        for p in rows
    ]

    daily = (
        session.query(DailyPnl)
        .filter(DailyPnl.trade_date == trade_date)
        .first()
    )
    daily_pnl = None
    if daily:
        daily_pnl = {
            "realized_pnl": daily.realized_pnl,
            "unrealized_pnl": daily.unrealized_pnl,
            "total_pnl": daily.total_pnl,
            "portfolio_value": daily.portfolio_value,
            "num_trades": daily.num_trades,
            "num_winners": daily.num_winners,
            "num_losers": daily.num_losers,
        }
    return {"positions_closed": positions, "daily_pnl": daily_pnl}


def _schedule_meta(job_id: str) -> dict | None:
    for entry in PIPELINE_SCHEDULE:
        if entry["job_id"] == job_id:
            return entry
    return None


@router.get("/pipeline/job-detail")
def get_pipeline_job_detail(
    job_id: str = Query(..., description="Job identifier (e.g., premarket_scan)"),
    trade_date: str = Query(..., description="YYYY-MM-DD trade date"),
):
    """Return enriched detail for a single job run: tickers, signals, positions closed."""
    meta = _schedule_meta(job_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")

    try:
        trade_date_obj = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="trade_date must be YYYY-MM-DD")

    engine = get_engine()
    with get_session(engine) as session:
        exec_row = (
            session.query(JobExecution)
            .filter(
                JobExecution.job_id == job_id,
                JobExecution.trade_date == trade_date_obj,
            )
            .order_by(JobExecution.started_at.desc())
            .first()
        )
        execution = _serialize_execution(exec_row, include_error=True) if exec_row else None

        category = meta["category"]
        enrichment: dict = {}
        if category == "scan":
            enrichment = _enrich_scan_job(session, job_id, trade_date_obj)
        elif category == "trade":
            enrichment = _enrich_execute_job(session, job_id, trade_date_obj)
        elif category == "monitor" or job_id == "eod_tasks":
            enrichment = _enrich_monitor_job(session, trade_date_obj)
        elif job_id == "subscribe_watchlist":
            enrichment = _enrich_scan_job(session, job_id, trade_date_obj)

    return {
        "job_id": job_id,
        "label": meta["label"],
        "phase": meta["phase"],
        "category": category,
        "description": meta["description"],
        "scheduled_time": meta["time"],
        "strategy": job_to_strategy(job_id),
        "trade_date": str(trade_date_obj),
        "execution": execution,
        **enrichment,
    }
