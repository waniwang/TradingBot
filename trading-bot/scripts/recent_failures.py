#!/usr/bin/env python3
"""
Dump recent failed JobExecution rows so we can answer "what was the 1 recent
failure" without server access. The /api/doctor endpoint only returns a count;
this script returns the rows themselves.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/recent_failures.py [--days 2]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import JobExecution, get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("recent_failures")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=2,
                        help="Lookback window in days (default 2 — matches /api/doctor)")
    args = parser.parse_args()

    config = load_config()
    engine = init_db(config["database"]["url"])

    cutoff = datetime.utcnow() - timedelta(days=args.days)

    with get_session(engine) as session:
        failures = (
            session.query(JobExecution)
            .filter(
                JobExecution.status == "failed",
                JobExecution.started_at >= cutoff,
            )
            .order_by(JobExecution.started_at.desc())
            .all()
        )

    logger.info("=" * 78)
    logger.info("RECENT FAILURES — last %d days (since %s UTC)", args.days, cutoff.isoformat())
    logger.info("=" * 78)

    if not failures:
        logger.info("No failed JobExecution rows. /api/doctor count is stale or comes from a different table.")
        return 0

    logger.info("Found %d failure(s):", len(failures))
    for f in failures:
        logger.info("-" * 78)
        logger.info("  job_id      : %s", f.job_id)
        logger.info("  started_at  : %s UTC", f.started_at.isoformat() if f.started_at else "?")
        logger.info("  finished_at : %s UTC", f.finished_at.isoformat() if f.finished_at else "?")
        logger.info("  trade_date  : %s", f.trade_date)
        logger.info("  status      : %s", f.status)
        logger.info("  result      : %s", f.result_summary or "(none)")
        if f.error:
            # Multi-line error: indent for readability.
            for line in (f.error or "").splitlines():
                logger.info("  | %s", line)
        else:
            logger.info("  error       : (none)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
