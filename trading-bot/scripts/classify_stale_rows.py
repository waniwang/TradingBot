#!/usr/bin/env python3
"""
For every stale ``stage IN (ready, triggered)`` Watchlist row older than the
cutoff, look up the matching Order/Position in the Alpaca DB and classify:

  TRADED     — Alpaca placed an order and a Position exists (open or closed).
               These are real trades. Stage="triggered" is accurate; the
               dashboard already shows them in the Filled tab. LEAVE ALONE.

  CANCELLED  — An Order exists but no Position (order was cancelled/rejected/
               never filled) OR the Order is missing entirely. From the
               strategy's perspective the bot tried but didn't get filled —
               this is a real bot/broker failure. Tag with [bot-failure] so
               the dashboard buckets it as Cancelled.

  EXPIRED    — No ep_strategy in meta (orphan rows from the legacy "two
               rows per signal" persistence pattern). Invisible to the
               dashboard anyway. Safe to mark expired with [stale-cleanup].

Read-only by default. Pass --yes to apply per-row updates.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/classify_stale_rows.py
    # Apply:
    #   .venv/bin/python scripts/classify_stale_rows.py --yes
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

from db.models import Order, Position, Watchlist, get_session, init_db

ET = pytz.timezone("America/New_York")
BOT_FAILURE_TAG = "[bot-failure]"
STALE_TAG = "[stale-cleanup]"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("classify_stale")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def _classify(row: Watchlist, session) -> tuple[str, str, str | None]:
    """Return (verdict, explanation, tag_to_add).

    verdict ∈ {"TRADED", "CANCELLED", "EXPIRED"}.
    tag_to_add is the [tag] string to append to notes on apply, or None.
    """
    meta = row.meta or {}
    ep = meta.get("ep_strategy")

    if not ep:
        return ("EXPIRED",
                "no ep_strategy in meta (orphan candidate row, dashboard ignores)",
                STALE_TAG)

    # Match Position by either the post-d7691dc per-variant naming
    # (`ep_earnings_a` / `ep_earnings_b` / `ep_earnings_c`) or the legacy
    # plain `ep_earnings` / `ep_news` form. d7691dc landed 2026-04-24, so any
    # Position written before that date uses the legacy form. Looking up only
    # the suffixed name caused the 2026-04-30 false-CANCELLED incident:
    # 6 real-trade rows from 2026-04-22..24 were mis-tagged [bot-failure]
    # because their Positions were stored as `ep_earnings`, not `ep_earnings_a`.
    setup_with_strategy = f"{row.setup_type}_{ep.lower()}"
    candidate_setup_types = [setup_with_strategy, row.setup_type]
    pos = (
        session.query(Position)
        .filter(
            Position.ticker == row.ticker,
            Position.setup_type.in_(candidate_setup_types),
        )
        .order_by(Position.opened_at.desc())
        .first()
    )
    if pos:
        opened = pos.opened_at.date().isoformat() if pos.opened_at else "?"
        is_open = pos.is_open
        exit_reason = pos.exit_reason or ""
        msg = f"Position #{pos.id} opened {opened}, is_open={is_open}"
        if exit_reason:
            msg += f", exit={exit_reason}"
        return ("TRADED", msg, None)

    # No position — was an order even placed?
    order = (
        session.query(Order)
        .filter_by(ticker=row.ticker)
        .order_by(Order.created_at.desc())
        .first()
    )
    if order:
        return ("CANCELLED",
                f"Order #{order.id} status={order.status} but no Position (didn't fill)",
                BOT_FAILURE_TAG)
    return ("CANCELLED", "no Order, no Position — row marked triggered but nothing executed", BOT_FAILURE_TAG)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true",
                        help="Apply per-row updates (otherwise dry-run).")
    parser.add_argument("--max-age-days", type=int, default=4)
    args = parser.parse_args()

    config = load_config()
    engine = init_db(config["database"]["url"])
    today = datetime.now(ET).date()
    cutoff = today - timedelta(days=args.max_age_days)

    logger.info("=" * 92)
    logger.info("CLASSIFY stale Watchlist rows  (today=%s, cutoff=%s, mode=%s)",
                today, cutoff, "APPLY" if args.yes else "DRY RUN")
    logger.info("=" * 92)

    plan: list[tuple[Watchlist, str, str, str | None]] = []
    with get_session(engine) as session:
        rows = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type.in_(["ep_earnings", "ep_news"]),
                Watchlist.stage.in_(["ready", "triggered"]),
                Watchlist.scan_date < cutoff,
            )
            .order_by(Watchlist.scan_date.desc(), Watchlist.ticker.asc())
            .all()
        )
        for r in rows:
            verdict, expl, tag = _classify(r, session)
            plan.append((r, verdict, expl, tag))

    if not plan:
        logger.info("Nothing to classify — Watchlist clean.")
        return 0

    # Print plan
    counts = {"TRADED": 0, "CANCELLED": 0, "EXPIRED": 0}
    logger.info("%-8s %-12s %-10s %-3s %-12s %s",
                "ticker", "setup", "scan_date", "ep", "verdict", "explanation")
    logger.info("-" * 92)
    for r, verdict, expl, _tag in plan:
        counts[verdict] += 1
        logger.info("%-8s %-12s %-10s %-3s %-12s %s",
                    r.ticker, r.setup_type, r.scan_date.isoformat(),
                    (r.meta or {}).get("ep_strategy") or "—",
                    verdict, expl)
    logger.info("-" * 92)
    logger.info("Plan: %d TRADED (leave alone)  |  %d CANCELLED (bot-failure tag)  |  %d EXPIRED (orphan)",
                counts["TRADED"], counts["CANCELLED"], counts["EXPIRED"])

    if not args.yes:
        logger.info("DRY RUN — re-run with --yes to apply.")
        return 0

    # Apply: only update CANCELLED and EXPIRED — leave TRADED rows alone.
    now = datetime.utcnow()
    updated = 0
    with get_session(engine) as session:
        for r, verdict, _expl, tag in plan:
            if verdict == "TRADED":
                continue
            fresh = session.query(Watchlist).filter_by(id=r.id).first()
            if fresh is None:
                logger.warning("  id=%d vanished, skipping", r.id)
                continue
            if fresh.stage not in ("ready", "triggered"):
                logger.warning("  %s no longer ready/triggered (now %s), skipping",
                               fresh.ticker, fresh.stage)
                continue
            existing = (fresh.notes or "").strip()
            if tag and tag not in existing:
                fresh.notes = f"{existing} {tag}".strip()
            fresh.stage = "expired"
            fresh.stage_changed_at = now
            fresh.updated_at = now
            updated += 1
        session.commit()

    logger.info("APPLIED — updated %d row(s). TRADED rows left at stage='triggered'.", updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
