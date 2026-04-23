#!/usr/bin/env python3
"""
One-off cleanup: flip stale `stage="ready"` watchlist rows to `stage="triggered"`
when an actual Position exists for that ticker.

Context: prior to 2026-04-23, `mark_triggered` flipped the wrong row when a
ticker had both a `stage="active"` candidate-pool row and a `stage="ready"`
execution row (SQLite's `.first()` returned the lower-PK row, which was the
`active` one). The `ready` row was left behind, cluttering the dashboard and
tripping `verify_day.py`'s drop check. Bug fixed in `watchlist_manager.py`
by ordering `stage.desc()` — this script repairs the rows already stale in prod.

Safety:
  - Only touches rows with an accompanying OPEN or FILLED Position
  - Prints a plan first and asks for confirmation (pass --yes to skip prompt)
  - No DELETE — only stage updates

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/cleanup_stale_ready_rows.py           # dry-run by default
    # Add --yes once you're happy with the plan:
    #   .venv/bin/python scripts/cleanup_stale_ready_rows.py --yes
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

from db.models import Position, Watchlist, get_session, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("cleanup_stale_ready")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true",
                        help="Apply the changes (otherwise dry-run)")
    args = parser.parse_args()

    config = load_config()
    engine = init_db(config["database"]["url"])

    with get_session(engine) as session:
        ready_rows = session.query(Watchlist).filter_by(stage="ready").all()
        logger.info("Found %d stage='ready' rows total", len(ready_rows))

        to_fix: list[tuple[Watchlist, Position]] = []
        keep: list[Watchlist] = []
        for wl in ready_rows:
            # Any open OR closed position for this ticker? Any entry = row is stale.
            pos = session.query(Position).filter_by(ticker=wl.ticker).order_by(
                Position.opened_at.desc()
            ).first()
            if pos is not None:
                to_fix.append((wl, pos))
            else:
                keep.append(wl)

    logger.info("=" * 70)
    logger.info("PLAN")
    logger.info("=" * 70)
    logger.info("To flip ready → triggered: %d rows", len(to_fix))
    for wl, pos in to_fix:
        opened = pos.opened_at.isoformat() if pos.opened_at else "?"
        logger.info("  %s (%s) scan_date=%s — position opened %s, is_open=%s",
                    wl.ticker, wl.setup_type, wl.scan_date, opened, pos.is_open)
    logger.info("To leave alone (no position): %d rows", len(keep))
    for wl in keep:
        logger.info("  %s (%s) scan_date=%s — genuinely un-entered, stays ready",
                    wl.ticker, wl.setup_type, wl.scan_date)

    if not to_fix:
        logger.info("Nothing to do.")
        return 0

    if not args.yes:
        logger.info("=" * 70)
        logger.info("DRY RUN — re-run with --yes to apply")
        logger.info("=" * 70)
        return 0

    # Apply
    now = datetime.utcnow()
    with get_session(engine) as session:
        for wl, _ in to_fix:
            row = session.query(Watchlist).filter_by(id=wl.id).first()
            if row is None:
                logger.warning("  %s: row vanished between plan and apply, skipping", wl.ticker)
                continue
            if row.stage != "ready":
                logger.warning("  %s: no longer stage='ready' (now %s), skipping",
                               row.ticker, row.stage)
                continue
            row.stage = "triggered"
            row.stage_changed_at = now
            row.updated_at = now
            logger.info("  %s: flipped ready → triggered", row.ticker)
        session.commit()

    logger.info("=" * 70)
    logger.info("CLEANUP COMPLETE — flipped %d rows", len(to_fix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
