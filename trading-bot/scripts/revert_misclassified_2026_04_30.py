#!/usr/bin/env python3
"""
Roll back the false-CANCELLED rows from the 2026-04-30 classify-stale-rows
--yes incident.

The classifier had a query bug — Position lookup used the post-d7691dc
suffixed setup_type (`ep_earnings_a` etc.) only, missing pre-2026-04-24
Positions stored as plain `ep_earnings`/`ep_news`. Six real-trade rows got
flipped from `stage="triggered"` to `stage="expired"` with [bot-failure]
appended to notes. Bot behavior was unaffected (Positions still open and
tracked), but the dashboard mis-buckets them as Cancelled.

Strategy: walk every Watchlist row that today's run updated
(updated_at on 2026-04-30, stage=expired, "[bot-failure]" in notes), feed
through the FIXED `_classify`, and if it now says TRADED, restore
stage="triggered" + strip the `[bot-failure]` tag from notes.

Read-only by default. Pass --yes to apply.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/revert_misclassified_2026_04_30.py
    # Apply:
    #   .venv/bin/python scripts/revert_misclassified_2026_04_30.py --yes
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
from scripts.classify_stale_rows import _classify, BOT_FAILURE_TAG

ET = pytz.timezone("America/New_York")
INCIDENT_DATE = date(2026, 4, 30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("revert_2026_04_30")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def _strip_tag(notes: str | None, tag: str) -> str:
    if not notes:
        return ""
    return notes.replace(tag, "").strip().replace("  ", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true",
                        help="Apply per-row updates (otherwise dry-run).")
    args = parser.parse_args()

    config = load_config()
    engine = init_db(config["database"]["url"])

    incident_start = datetime.combine(INCIDENT_DATE, datetime.min.time())
    incident_end = incident_start + timedelta(days=1)

    logger.info("=" * 78)
    logger.info("REVERT misclassified rows from %s  (mode=%s)",
                INCIDENT_DATE, "APPLY" if args.yes else "DRY RUN")
    logger.info("=" * 78)

    plan: list[tuple[int, str, str, str]] = []  # (id, ticker, old_notes, new_notes)
    with get_session(engine) as session:
        rows = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type.in_(["ep_earnings", "ep_news"]),
                Watchlist.stage == "expired",
                Watchlist.updated_at >= incident_start,
                Watchlist.updated_at < incident_end,
                Watchlist.notes.like(f"%{BOT_FAILURE_TAG}%"),
            )
            .order_by(Watchlist.scan_date.desc(), Watchlist.ticker.asc())
            .all()
        )
        logger.info("Found %d row(s) flipped to expired with [bot-failure] today.", len(rows))

        for row in rows:
            verdict, expl, _tag = _classify(row, session)
            status = f"  {row.ticker:<8} {row.setup_type:<12} scan={row.scan_date} ep={(row.meta or {}).get('ep_strategy', '—'):<3} → {verdict}"
            if verdict != "TRADED":
                logger.info("%s  (leave alone — %s)", status, expl)
                continue
            new_notes = _strip_tag(row.notes, BOT_FAILURE_TAG)
            logger.info("%s  REVERT (%s)", status, expl)
            plan.append((row.id, row.ticker, row.notes or "", new_notes))

    if not plan:
        logger.info("Nothing to revert.")
        return 0

    logger.info("-" * 78)
    logger.info("Plan: revert %d row(s) — restore stage='triggered' + strip [bot-failure].", len(plan))

    if not args.yes:
        logger.info("DRY RUN — re-run with --yes to apply.")
        return 0

    now = datetime.utcnow()
    with get_session(engine) as session:
        for row_id, ticker, _old_notes, new_notes in plan:
            fresh = session.query(Watchlist).filter_by(id=row_id).first()
            if fresh is None:
                logger.warning("  id=%d vanished, skipping", row_id)
                continue
            if fresh.stage != "expired":
                logger.warning("  %s no longer expired (now %s), skipping", ticker, fresh.stage)
                continue
            fresh.stage = "triggered"
            fresh.stage_changed_at = now
            fresh.updated_at = now
            fresh.notes = new_notes or None
        session.commit()

    logger.info("APPLIED — reverted %d row(s).", len(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
