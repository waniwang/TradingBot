"""Bot status endpoint."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

STATUS_FILE = Path(__file__).parent.parent.parent / "bot_status.json"

JOB_LABELS = {
    "nightly_watchlist_scan": "Nightly watchlist scan",
    "premarket_scan": "Pre-market scan",
    "subscribe_watchlist": "Subscribe watchlist",
    "intraday_monitor": "Intraday monitor start",
    "eod_tasks": "End-of-day tasks",
    "reconcile_positions": "Reconcile positions",
    "heartbeat": "Heartbeat",
}


@router.get("/status")
def get_status():
    if not STATUS_FILE.exists():
        return {
            "running": False,
            "phase": "unknown",
            "phase_label": "Unknown",
            "phase_description": "Status file not found",
            "next_job": None,
            "next_job_label": None,
            "next_job_time": None,
            "countdown_seconds": None,
            "environment": "paper",
            "progress": None,
        }

    try:
        with open(STATUS_FILE) as f:
            raw = json.load(f)
    except Exception:
        return {"running": False, "phase": "error"}

    phase = raw.get("phase", "unknown")
    phase_map = {
        "idle": ("Idle", "Market closed"),
        "nightly_scan": ("Nightly Scan", "Building breakout watchlist"),
        "premarket_scan": ("Pre-market Scan", "Finding EP gappers & promoting breakouts"),
        "watchlist_ready": ("Watchlist Ready", "Waiting for market open"),
        "observing": ("Observing", "Monitoring watchlist for signals"),
        "trading": ("Trading", "Signal detected - managing order"),
        "end_of_day": ("End of Day", "Running EOD tasks"),
    }
    label, desc = phase_map.get(phase, (phase.replace("_", " ").title(), ""))

    # Heartbeat liveness
    heartbeat = raw.get("last_heartbeat")
    bot_running = False
    if heartbeat:
        try:
            hb_dt = datetime.fromisoformat(heartbeat)
            age = (datetime.now(timezone.utc) - hb_dt.astimezone(timezone.utc)).total_seconds()
            bot_running = age < 120
        except Exception:
            pass

    # Countdown to next job
    next_time = raw.get("next_job_time")
    countdown = None
    if next_time:
        try:
            next_dt = datetime.fromisoformat(next_time)
            delta = (next_dt - datetime.now(timezone.utc).astimezone(next_dt.tzinfo)).total_seconds()
            countdown = max(0, int(delta))
        except Exception:
            pass

    next_job_raw = raw.get("next_job")
    return {
        "running": bot_running,
        "phase": phase,
        "phase_label": label,
        "phase_description": desc,
        "environment": raw.get("environment", "paper"),
        "next_job": next_job_raw,
        "next_job_label": JOB_LABELS.get(next_job_raw, next_job_raw),
        "next_job_time": next_time,
        "countdown_seconds": countdown,
        "progress": raw.get("progress"),
    }
