"""Register strategy-declared cron jobs with APScheduler."""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def _tracked_strategy_job(job_id, handler, config, client, db_engine, notify):
    """Wrapper that tracks strategy plugin job execution."""
    # Import here to avoid circular imports (main imports core.scheduler)
    from db.models import JobExecution, get_session
    import pytz

    ET = pytz.timezone("America/New_York")
    now = datetime.now(ET)
    row_id = None

    # Map job_id to a human-readable label
    label_map = {
        "breakout_nightly_scan": "Nightly Breakout Scan",
        "ep_earnings_scan": "EP Earnings Scan",
        "ep_earnings_execute": "EP Earnings Execute",
        "ep_news_scan": "EP News Scan",
        "ep_news_execute": "EP News Execute",
    }
    label = label_map.get(job_id, job_id)

    try:
        with get_session(db_engine) as session:
            row = JobExecution(
                job_id=job_id, job_label=label,
                started_at=now, status="running", trade_date=now.date(),
            )
            session.add(row)
            session.commit()
            row_id = row.id
    except Exception as e:
        logger.debug("Failed to insert job_execution for %s: %s", job_id, e)

    summary = None
    error_text = None
    status = "success"
    try:
        result = handler(config, client, db_engine, notify)
        if isinstance(result, str):
            summary = result
        elif isinstance(result, dict):
            # Breakout nightly scan returns a summary dict
            ready = result.get("ready", 0)
            watching = result.get("watching", 0)
            new = result.get("new", 0)
            summary = f"Ready: {ready}, Watching: {watching}, New: {new}"
    except Exception as exc:
        import traceback as _tb
        status = "failed"
        error_text = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))[:2000]
        raise
    finally:
        if row_id is not None:
            finished = datetime.now(ET)
            try:
                with get_session(db_engine) as session:
                    row = session.get(JobExecution, row_id)
                    if row:
                        row.finished_at = finished
                        row.duration_seconds = (finished.replace(tzinfo=None) - row.started_at).total_seconds()
                        row.status = status
                        row.result_summary = (summary or "")[:500] or None
                        row.error = error_text
                        session.commit()
            except Exception as e:
                logger.debug("Failed to update job_execution for %s: %s", job_id, e)


def register_strategy_jobs(scheduler, plugins, config, client, db_engine, notify):
    """
    Register each plugin's schedule entries with APScheduler.

    Each ScheduleEntry.handler is called with (config, client, db_engine, notify).
    Jobs are wrapped with tracking to persist execution history.
    """
    for plugin in plugins.values():
        for entry in plugin.schedule:
            scheduler.add_job(
                func=_tracked_strategy_job,
                trigger=CronTrigger(**entry.cron),
                id=entry.job_id,
                args=[entry.job_id, entry.handler, config, client, db_engine, notify],
                replace_existing=True,
            )
            logger.info(
                "Registered job '%s' for strategy '%s'",
                entry.job_id,
                plugin.name,
            )
