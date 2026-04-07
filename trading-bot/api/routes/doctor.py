"""Bot health diagnostics — detects crash loops, stale heartbeats, missing jobs."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter

from db.models import JobExecution, get_engine, get_session

router = APIRouter()

STATUS_FILE = Path(__file__).parent.parent.parent / "bot_status.json"


def _check_heartbeat() -> dict:
    """Check if bot_status.json heartbeat is recent."""
    if not STATUS_FILE.exists():
        return {"ok": False, "message": "No status file — bot may have never started"}

    try:
        with open(STATUS_FILE) as f:
            raw = json.load(f)
    except Exception:
        return {"ok": False, "message": "Status file unreadable"}

    heartbeat = raw.get("last_heartbeat")
    if not heartbeat:
        return {"ok": False, "message": "No heartbeat recorded"}

    try:
        hb_dt = datetime.fromisoformat(heartbeat)
        age_seconds = (datetime.now(timezone.utc) - hb_dt.astimezone(timezone.utc)).total_seconds()
        if age_seconds < 120:
            return {"ok": True, "message": f"Heartbeat {int(age_seconds)}s ago", "age_seconds": int(age_seconds)}
        else:
            mins = int(age_seconds // 60)
            return {"ok": False, "message": f"Heartbeat stale — last seen {mins}m ago", "age_seconds": int(age_seconds)}
    except Exception as e:
        return {"ok": False, "message": f"Cannot parse heartbeat: {e}"}


def _check_systemd() -> dict:
    """Check systemd service status for trading-bot."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "trading-bot"],
            capture_output=True, text=True, timeout=5,
        )
        active = result.stdout.strip()
        if active == "active":
            return {"ok": True, "message": "Service active"}
        elif active == "activating":
            # Could be auto-restart during crash loop
            result2 = subprocess.run(
                ["systemctl", "show", "trading-bot", "--property=NRestarts", "--value"],
                capture_output=True, text=True, timeout=5,
            )
            restarts = result2.stdout.strip()
            return {"ok": False, "message": f"Service restarting (crash loop?) — {restarts} restarts"}
        else:
            return {"ok": False, "message": f"Service {active}"}
    except FileNotFoundError:
        return {"ok": True, "message": "systemd not available (dev environment)"}
    except Exception as e:
        return {"ok": False, "message": f"Cannot check service: {e}"}


def _check_recent_jobs(engine) -> dict:
    """Check if jobs have executed recently on trading days."""
    import pytz
    ET = pytz.timezone("America/New_York")
    today = datetime.now(ET).date()

    try:
        with get_session(engine) as session:
            # Stale running jobs
            stale_running = (
                session.query(JobExecution)
                .filter(JobExecution.status == "running")
                .count()
            )

            # Last successful job
            last_success = (
                session.query(JobExecution)
                .filter(JobExecution.status == "success")
                .order_by(JobExecution.started_at.desc())
                .first()
            )

            # Jobs today
            today_count = (
                session.query(JobExecution)
                .filter(JobExecution.trade_date == today)
                .count()
            )

            # Recent failures
            recent_failures = (
                session.query(JobExecution)
                .filter(
                    JobExecution.status == "failed",
                    JobExecution.trade_date >= today - timedelta(days=2),
                )
                .count()
            )

        issues = []
        if stale_running > 0:
            issues.append(f"{stale_running} job(s) stuck in 'running' state")
        if last_success:
            days_since = (today - last_success.trade_date).days
            if days_since > 1:
                issues.append(f"Last successful job was {days_since} days ago ({last_success.trade_date})")
        else:
            issues.append("No successful jobs found in database")
        if today_count == 0 and today.weekday() < 5:
            issues.append("No jobs have run today (weekday)")
        if recent_failures > 3:
            issues.append(f"{recent_failures} failed jobs in last 2 days")

        if issues:
            return {"ok": False, "message": "; ".join(issues),
                    "today_jobs": today_count, "stale_running": stale_running,
                    "recent_failures": recent_failures}
        return {"ok": True, "message": f"{today_count} jobs today, no issues",
                "today_jobs": today_count, "stale_running": 0,
                "recent_failures": recent_failures}
    except Exception as e:
        return {"ok": False, "message": f"Database error: {e}"}


@router.get("/doctor")
def doctor():
    """Comprehensive health check for the trading bot.

    Returns individual check results and an overall health status.
    Can be polled by external monitoring (e.g. cron + curl) to detect outages.
    """
    import os
    engine = get_engine(os.environ.get("DATABASE_URL", "sqlite:///trading_bot.db"))

    heartbeat = _check_heartbeat()
    systemd = _check_systemd()
    jobs = _check_recent_jobs(engine)

    checks = {
        "heartbeat": heartbeat,
        "systemd": systemd,
        "jobs": jobs,
    }

    all_ok = all(c["ok"] for c in checks.values())
    critical = not heartbeat["ok"] and not systemd["ok"]

    if critical:
        overall = "critical"
        summary = "Bot is DOWN — not running and no recent heartbeat"
    elif all_ok:
        overall = "healthy"
        summary = "All checks passed"
    else:
        failing = [k for k, v in checks.items() if not v["ok"]]
        overall = "degraded"
        summary = f"Issues detected: {', '.join(failing)}"

    return {
        "status": overall,
        "summary": summary,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
