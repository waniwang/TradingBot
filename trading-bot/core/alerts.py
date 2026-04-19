"""Shared job-failure alerting.

Every scheduled job's outer wrapper (`main.py::_track_job` and
`core/scheduler.py::_tracked_strategy_job`) must call `notify_job_failure` when
it catches an unhandled exception. That guarantees:

  1. The operator sees a Telegram "JOB FAILED" alert for every failed job.
  2. If Telegram itself fails, the gap is loud in the logs (ERROR level) and
     is tagged on the JobExecution row via the caller's session update.

Handlers themselves must NOT swallow exceptions silently — see CLAUDE.md.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def notify_job_failure(
    label: str,
    exc: BaseException,
    notify_fn: Optional[Callable[[str], None]],
    job_id: str = "",
) -> bool:
    """Send "JOB FAILED" via notify_fn, with escalation if notify itself fails.

    Returns True if Telegram send succeeded; False if notify was missing or
    itself failed. The caller is responsible for tagging the JobExecution row
    (e.g. appending "[notify_failed]" to result_summary) based on the return.
    """
    short = str(exc)[:200]
    if notify_fn is None:
        logger.error(
            "notify_fn is None — operator will not see JOB FAILED alert for %s: %s",
            job_id or label, short,
        )
        return False
    try:
        notify_fn(f"JOB FAILED: {label}\n{short}")
        return True
    except Exception as notify_err:
        logger.error(
            "Telegram notify FAILED for %s — operator may be unaware of failure: %s",
            job_id or label, notify_err, exc_info=True,
        )
        return False
