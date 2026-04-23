#!/usr/bin/env python3
"""
One-off: append [bot-failure] tag to the 7 watchlist rows that day-2 confirm
failed to fetch a snapshot for on 2026-04-23.

Context: the day-2 confirm error paths now tag the Watchlist.notes field
with [bot-failure] so the dashboard can bucket these as "Cancelled" rather
than "Expired" (which now means legitimate day-2 price rejection). The
rows from 2026-04-23 predate that tagging change, so this script backfills
them.

Target rows (all stage="expired", scan_date=2026-04-22, stage_changed_at
on 2026-04-23 — exactly the set reported by the failed day-2 confirm jobs):
  ep_earnings: HCSG, VICR, MAS, GEV, MCRI
  ep_news:     LBTYB, TFX

Idempotent: rows already containing the tag are skipped.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/tag_bot_failures_20260423.py           # dry-run
    # Add --yes to apply:
    #   .venv/bin/python scripts/tag_bot_failures_20260423.py --yes
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import Watchlist, get_session, init_db

TAG = "[bot-failure]"

TARGETS: list[tuple[str, str]] = [
    ("HCSG", "ep_earnings"),
    ("VICR", "ep_earnings"),
    ("MAS",  "ep_earnings"),
    ("GEV",  "ep_earnings"),
    ("MCRI", "ep_earnings"),
    ("LBTYB", "ep_news"),
    ("TFX",  "ep_news"),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("tag_bot_failures")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Apply changes (otherwise dry-run)")
    args = parser.parse_args()

    config = load_config()
    engine = init_db(config["database"]["url"])

    logger.info("=" * 70)
    logger.info("BACKFILL %s tag — 2026-04-23 day-2 confirm failures", TAG)
    logger.info("=" * 70)

    to_apply: list[tuple[Watchlist, str, str]] = []
    with get_session(engine) as session:
        for ticker, setup_type in TARGETS:
            rows = session.query(Watchlist).filter(
                Watchlist.ticker == ticker,
                Watchlist.setup_type == setup_type,
                Watchlist.stage == "expired",
            ).all()
            if not rows:
                logger.warning("  %s (%s): no expired row found — skipping", ticker, setup_type)
                continue
            for row in rows:
                notes = row.notes or ""
                if TAG in notes:
                    logger.info("  %s (%s) id=%d: already tagged — skipping",
                                ticker, setup_type, row.id)
                    continue
                new_notes = f"{notes} {TAG}".strip()
                to_apply.append((row, notes, new_notes))
                logger.info("  %s (%s) id=%d: notes '%s' → '%s'",
                            ticker, setup_type, row.id, notes, new_notes)

    if not to_apply:
        logger.info("Nothing to do.")
        return 0

    logger.info("-" * 70)
    logger.info("Will update %d rows", len(to_apply))

    if not args.yes:
        logger.info("DRY RUN — re-run with --yes to apply")
        return 0

    now = datetime.utcnow()
    with get_session(engine) as session:
        for row, _, new_notes in to_apply:
            fresh = session.query(Watchlist).filter_by(id=row.id).first()
            if fresh is None:
                logger.warning("  id=%d: row vanished between plan and apply, skipping", row.id)
                continue
            fresh.notes = new_notes
            fresh.updated_at = now
        session.commit()

    logger.info("BACKFILL COMPLETE — tagged %d rows", len(to_apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
