#!/usr/bin/env python3
"""
One-off cleanup: flip stale Watchlist rows in stage IN (ready, triggered)
with scan_date older than ``--max-age-days`` to stage="expired".

Why: the IBKR passive executor reads rows from the Alpaca DB filtered on
stage IN (ready, triggered). Until 2026-04-30 the cross-DB reader had no
recency filter, so old `triggered` rows from prior days kept getting picked
up on every cron tick — IBKR's local-DB idempotency cannot detect rows it
never recorded the first time around. The reader is now bounded to
``today - 4 days``, but rows older than that are still sitting in the DB
polluting any future query that drops the bound. This script mops them up.

Safety:
  - Dry-run by default. Pass ``--yes`` to apply.
  - Only touches stage IN (ready, triggered).
  - Adds an audit tag to ``notes`` so the dashboard / verify_day can bucket
    these as "Cancelled" rather than "Expired" (which means legitimate
    day-2 rejection).

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/expire_stale_triggered_rows.py
    # Apply:
    #   .venv/bin/python scripts/expire_stale_triggered_rows.py --yes
    # Custom window:
    #   .venv/bin/python scripts/expire_stale_triggered_rows.py --max-age-days 7
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import Watchlist, get_session, init_db

ET = pytz.timezone("America/New_York")
TAG = "[stale-cleanup]"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("expire_stale_triggered")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true",
                        help="Apply the changes (otherwise dry-run).")
    parser.add_argument(
        "--max-age-days", type=int, default=4,
        help="Calendar-day staleness cap. Rows with scan_date < today-this "
             "will be expired. Default 4 (Friday→Tuesday weekend buffer).",
    )
    parser.add_argument(
        "--include-setup", action="append", default=[],
        help="Setup type(s) to include. Repeat for multiple. Default: "
             "ep_earnings + ep_news (the ones IBKR reads).",
    )
    args = parser.parse_args()

    setups = args.include_setup or ["ep_earnings", "ep_news"]
    config = load_config()
    engine = init_db(config["database"]["url"])
    today = datetime.now(ET).date()
    cutoff = today - timedelta(days=args.max_age_days)

    logger.info("=" * 78)
    logger.info("EXPIRE stale triggered/ready Watchlist rows")
    logger.info("Today (ET): %s   |   cutoff: scan_date < %s   |   setups: %s",
                today, cutoff, setups)
    logger.info("Mode: %s", "APPLY" if args.yes else "DRY RUN")
    logger.info("=" * 78)

    with get_session(engine) as session:
        rows = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type.in_(setups),
                Watchlist.stage.in_(["ready", "triggered"]),
                Watchlist.scan_date < cutoff,
            )
            .order_by(Watchlist.scan_date.desc(), Watchlist.ticker.asc())
            .all()
        )

        if not rows:
            logger.info("Nothing to do — Watchlist is clean.")
            return 0

        logger.info("Found %d stale row(s):", len(rows))
        logger.info("%-8s %-18s %-10s %-12s %-9s %s",
                    "ticker", "setup", "scan_date", "current_stage", "ep", "notes_before")
        for w in rows:
            logger.info("%-8s %-18s %-10s %-12s %-9s %s",
                        w.ticker, w.setup_type, w.scan_date.isoformat(),
                        w.stage, (w.meta or {}).get("ep_strategy", "—"),
                        (w.notes or "")[:40])

        if not args.yes:
            logger.info("-" * 78)
            logger.info("DRY RUN — re-run with --yes to apply")
            return 0

        # Apply
        now = datetime.utcnow()
        for w in rows:
            w.stage = "expired"
            w.stage_changed_at = now
            w.updated_at = now
            existing_notes = (w.notes or "").strip()
            w.notes = f"{existing_notes} {TAG}".strip()
        session.commit()

    logger.info("-" * 78)
    logger.info("EXPIRED %d rows. Notes tagged with %s.", len(rows), TAG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
