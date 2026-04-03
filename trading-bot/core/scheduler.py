"""Register strategy-declared cron jobs with APScheduler."""

from __future__ import annotations

import logging

from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def register_strategy_jobs(scheduler, plugins, config, client, db_engine, notify):
    """
    Register each plugin's schedule entries with APScheduler.

    Each ScheduleEntry.handler is called with (config, client, db_engine, notify).
    """
    for plugin in plugins.values():
        for entry in plugin.schedule:
            scheduler.add_job(
                func=entry.handler,
                trigger=CronTrigger(**entry.cron),
                id=entry.job_id,
                args=[config, client, db_engine, notify],
                replace_existing=True,
            )
            logger.info(
                "Registered job '%s' for strategy '%s'",
                entry.job_id,
                plugin.name,
            )
