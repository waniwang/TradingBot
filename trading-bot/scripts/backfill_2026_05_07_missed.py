#!/usr/bin/env python3
"""
One-shot backfill: insert RiskSkip rows for the 4 missed trades on
2026-05-07 that the live code couldn't record because the relevant fixes
(commits 39d6e70 stage-filter, 636de56 BP pre-flight) had not yet shipped.

Verification (ran 2026-05-07 evening): every ticker's 1-min bar low
touched the limit price between 15:38 and 15:59 ET, so all 4 would have
filled if the bugs hadn't dropped them. See conversation transcript /
verify_day output for that day.

  Ticker  Limit     First fill bar    Cause
  ------  --------  ----------------  ----------------------------------
  NBIX    $148.95   15:38 ET          BP exhausted (4× 403 retries)
  SSRM    $32.80    15:38 ET          Stage-filter bug (cancelled, no retry)
  FLEX    $134.72   15:43 ET          Stage-filter bug
  HL      $18.14    15:47 ET          Stage-filter bug

Idempotent: RiskSkip has UniqueConstraint on
(occurred_date, ticker, setup_type, ep_strategy, block_reason) so re-running
this script is a no-op on already-inserted rows.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/backfill_2026_05_07_missed.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytz
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import RiskSkip, get_session, init_db, record_risk_skip

ET = pytz.timezone("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("backfill_2026_05_07")


# ET timestamps from journald / orders table on 2026-05-07.
# `occurred_at` should be UTC; we localize ET → UTC at insert time.
BACKFILLS = [
    {
        "ticker": "NBIX",
        "ep_strategy": "C",
        "block_reason": "insufficient_bp",
        "intended_entry": 148.95,
        "intended_stop": 138.52,
        "open_position_count": 25,  # ~25 positions per server account check that evening
        "portfolio_value": 97344.13,
        "occurred_et": datetime(2026, 5, 7, 15, 37),
        "notes": (
            "cost=$4,170 > BP=$2,473 (4 retries 15:37-15:46, never filled; "
            "1m bar low touched $148.72 at 15:38 ET → would have filled) — "
            "backfilled (pre-636de56 BP pre-flight)"
        ),
    },
    {
        "ticker": "SSRM",
        "ep_strategy": "C",
        "block_reason": "stage_filter_drop",
        "intended_entry": 32.80,
        "intended_stop": 30.50,
        "open_position_count": None,
        "portfolio_value": None,
        "occurred_et": datetime(2026, 5, 7, 15, 38),
        "notes": (
            "OTO cancelled at 15:38 (limit=$32.80 didn't print in 60s); "
            "1m bar low=$32.68 at 15:38 ET — would have filled on retry. "
            "Bot dropped the row (stage='triggered' not in pre-39d6e70 filter) — "
            "backfilled"
        ),
    },
    {
        "ticker": "FLEX",
        "ep_strategy": "C",
        "block_reason": "stage_filter_drop",
        "intended_entry": 134.72,
        "intended_stop": 125.29,
        "open_position_count": None,
        "portfolio_value": None,
        "occurred_et": datetime(2026, 5, 7, 15, 38),
        "notes": (
            "OTO cancelled at 15:38 (limit=$134.72 didn't print in 60s); "
            "1m bar low=$132.07 at 15:43 ET — would have filled on retry. "
            "Bot dropped the row (stage='triggered' not in pre-39d6e70 filter) — "
            "backfilled"
        ),
    },
    {
        "ticker": "HL",
        "ep_strategy": "C",
        "block_reason": "stage_filter_drop",
        "intended_entry": 18.14,
        "intended_stop": 16.87,
        "open_position_count": None,
        "portfolio_value": None,
        "occurred_et": datetime(2026, 5, 7, 15, 38),
        "notes": (
            "OTO cancelled at 15:38 (limit=$18.14 didn't print in 60s); "
            "1m bar low=$17.97 at 15:47 ET — would have filled on retry. "
            "Bot dropped the row (stage='triggered' not in pre-39d6e70 filter) — "
            "backfilled"
        ),
    },
]


def main() -> int:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    engine = init_db(cfg["database"]["url"])

    inserted = 0
    skipped_existing = 0
    for entry in BACKFILLS:
        occurred_at_utc = ET.localize(entry["occurred_et"]).astimezone(pytz.UTC).replace(tzinfo=None)
        occurred_date = entry["occurred_et"].date()

        # Idempotency check via the table's UniqueConstraint — manually look up
        # before insert so we can log the skip rather than swallowing IntegrityError.
        with get_session(engine) as session:
            existing = session.query(RiskSkip).filter_by(
                occurred_date=occurred_date,
                ticker=entry["ticker"],
                setup_type="ep_earnings",
                ep_strategy=entry["ep_strategy"],
                block_reason=entry["block_reason"],
            ).first()
            if existing is not None:
                logger.info(
                    "SKIP existing: %s %s/%s %s (id=%s)",
                    entry["ticker"], "ep_earnings", entry["ep_strategy"],
                    entry["block_reason"], existing.id,
                )
                skipped_existing += 1
                continue

        # record_risk_skip itself uses UniqueConstraint — but it stamps
        # occurred_at = utcnow(). We want the historical timestamp, so insert
        # directly here and bypass the helper.
        with get_session(engine) as session:
            row = RiskSkip(
                occurred_at=occurred_at_utc,
                occurred_date=occurred_date,
                ticker=entry["ticker"],
                setup_type="ep_earnings",
                ep_strategy=entry["ep_strategy"],
                block_reason=entry["block_reason"],
                intended_entry=entry["intended_entry"],
                intended_stop=entry["intended_stop"],
                portfolio_value=entry["portfolio_value"],
                open_position_count=entry["open_position_count"],
                notes=entry["notes"],
            )
            session.add(row)
            session.commit()
            logger.info(
                "INSERTED: %s ep_earnings/%s %s @ %s (id=%s)",
                entry["ticker"], entry["ep_strategy"],
                entry["block_reason"], occurred_at_utc.isoformat(), row.id,
            )
            inserted += 1

    logger.info("=" * 70)
    logger.info("Backfill complete: %d inserted, %d already existed", inserted, skipped_existing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
