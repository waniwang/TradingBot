#!/usr/bin/env python3
"""
For every Position opened by the IBKR bot on a given ET date, find the
matching Watchlist row in the ALPACA DB (the source of truth for "what to
trade") and report its scan_date + stage + stage_changed_at.

Settles the question: did today's IBKR entries come from today's scans, or
from stale Alpaca Watchlist rows accumulated over prior days?

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source config.ib.local.yaml && set +a \
      && BOT_CONFIG=config.ib.local.yaml \
         WATCHLIST_SOURCE_DB_URL=sqlite:////opt/trading-bot/trading-bot/trading_bot.db \
         .venv/bin/python scripts/ib_source_check.py
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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import Position, Watchlist, get_session, init_db

ET = pytz.timezone("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("ib_source_check")


def _et_day_bounds_utc(target_et: date) -> tuple[datetime, datetime]:
    start_et = ET.localize(datetime.combine(target_et, datetime.min.time()))
    end_et = ET.localize(datetime.combine(target_et + timedelta(days=1), datetime.min.time()))
    return start_et.astimezone(pytz.UTC).replace(tzinfo=None), end_et.astimezone(pytz.UTC).replace(tzinfo=None)


def _setup_pieces(setup_type: str) -> tuple[str, str | None]:
    """Map IBKR setup_type (e.g. 'ep_earnings_a') to (alpaca_setup_type, ep_strategy_letter)."""
    parts = setup_type.rsplit("_", 1)
    if len(parts) == 2 and parts[1].lower() in ("a", "b", "c"):
        return parts[0], parts[1].upper()
    return setup_type, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=datetime.now(ET).date(),
        help="ET date to inspect (default: today).",
    )
    args = parser.parse_args()

    # IB DB (positions opened by IBKR bot today)
    ib_url = os.environ.get(
        "DATABASE_IB_URL",
        "sqlite:////opt/trading-bot/trading-bot/trading_bot_ib.db",
    )
    # Alpaca DB (source of Watchlist rows)
    alpaca_url = os.environ.get(
        "WATCHLIST_SOURCE_DB_URL",
        "sqlite:////opt/trading-bot/trading-bot/trading_bot.db",
    )

    ib_engine = init_db(ib_url)
    alpaca_engine = create_engine(alpaca_url)
    AlpacaSession = sessionmaker(bind=alpaca_engine)

    start_utc, end_utc = _et_day_bounds_utc(args.date)
    logger.info("=" * 92)
    logger.info("CROSS-CHECK IBKR positions opened on %s vs source Alpaca Watchlist rows", args.date)
    logger.info("IB DB:     %s", ib_url)
    logger.info("Alpaca DB: %s", alpaca_url)
    logger.info("=" * 92)

    with get_session(ib_engine) as ib_session:
        opened_today = (
            ib_session.query(Position)
            .filter(Position.opened_at >= start_utc, Position.opened_at < end_utc)
            .order_by(Position.opened_at.asc())
            .all()
        )

    if not opened_today:
        logger.info("No IBKR positions opened on %s. Nothing to cross-check.", args.date)
        return 0

    logger.info("%-8s %-18s %-10s %-12s %-9s %-12s %s",
                "ticker", "ib_setup", "scan_date", "stage", "ep", "stage_chg_et", "note")
    logger.info("-" * 92)

    fresh_count = 0
    stale_count = 0
    missing_count = 0

    with AlpacaSession() as alpaca_session:
        for pos in opened_today:
            alpaca_setup, ep_letter = _setup_pieces(pos.setup_type)
            # Find candidate Watchlist rows for this ticker+setup. Latest first.
            candidates = (
                alpaca_session.query(Watchlist)
                .filter(
                    Watchlist.ticker == pos.ticker,
                    Watchlist.setup_type == alpaca_setup,
                )
                .order_by(Watchlist.stage_changed_at.desc(), Watchlist.id.desc())
                .all()
            )
            # Pick the row whose meta.ep_strategy matches (when applicable),
            # falling back to the most recent row for that ticker+setup.
            match = None
            if ep_letter is not None:
                for w in candidates:
                    if (w.meta or {}).get("ep_strategy") == ep_letter:
                        match = w
                        break
            if match is None and candidates:
                match = candidates[0]

            if match is None:
                logger.info("%-8s %-18s %-10s %-12s %-9s %-12s NO MATCH IN ALPACA WATCHLIST",
                            pos.ticker, pos.setup_type, "?", "?", ep_letter or "?", "?")
                missing_count += 1
                continue

            stage_chg_et = ""
            if match.stage_changed_at:
                aware = pytz.UTC.localize(match.stage_changed_at) if match.stage_changed_at.tzinfo is None else match.stage_changed_at
                stage_chg_et = aware.astimezone(ET).strftime("%m-%d %H:%M")

            scan_date_str = match.scan_date.isoformat() if match.scan_date else "?"

            # Classify: a "fresh" entry means the source Watchlist row's
            # scan_date is today or yesterday (yesterday = day-2 for C). A
            # "stale" entry means the source row was scanned more than 1
            # trading day ago — that's a backfill of a row that should have
            # been processed (or expired) earlier.
            today = args.date
            yesterday = today - timedelta(days=1)
            note = ""
            if match.scan_date in (today, yesterday):
                fresh_count += 1
                note = "fresh"
            else:
                stale_count += 1
                age = (today - match.scan_date).days if match.scan_date else "?"
                note = f"STALE (scan {age}d ago)"

            logger.info("%-8s %-18s %-10s %-12s %-9s %-12s %s",
                        pos.ticker, pos.setup_type, scan_date_str,
                        match.stage, ep_letter or "—", stage_chg_et, note)

    logger.info("-" * 92)
    logger.info("Summary: %d fresh (today/yesterday scan), %d STALE (>1d old), %d no match",
                fresh_count, stale_count, missing_count)

    if stale_count > 0:
        logger.warning("")
        logger.warning("⚠  STALE entries detected. The IBKR bot reads stage IN (ready, triggered)")
        logger.warning("   from the Alpaca DB and only checks idempotency against its OWN")
        logger.warning("   Position/Order tables — so if a row stayed `triggered` from days ago")
        logger.warning("   and IBKR's local tables have no record of it, today's cron will pick")
        logger.warning("   it up and place a fresh order at a stale entry price.")

    # Also report any Alpaca Watchlist rows currently sitting in stage IN
    # (ready, triggered) with old scan_dates — these are future stale-entry
    # candidates that today's run did NOT pick up but tomorrow's might.
    logger.info("")
    logger.info("=" * 92)
    logger.info("ALPACA WATCHLIST: rows in (ready, triggered) older than %d days", 1)
    logger.info("=" * 92)
    cutoff = args.date - timedelta(days=1)
    with AlpacaSession() as alpaca_session:
        stale_rows = (
            alpaca_session.query(Watchlist)
            .filter(
                Watchlist.stage.in_(["ready", "triggered"]),
                Watchlist.scan_date < cutoff,
            )
            .order_by(Watchlist.scan_date.desc(), Watchlist.ticker.asc())
            .all()
        )
    if not stale_rows:
        logger.info("None — Alpaca Watchlist is clean.")
    else:
        logger.info("Found %d stale row(s):", len(stale_rows))
        logger.info("%-8s %-18s %-10s %-12s %s",
                    "ticker", "setup", "scan_date", "stage", "ep_strategy")
        for w in stale_rows:
            logger.info("%-8s %-18s %-10s %-12s %s",
                        w.ticker, w.setup_type, w.scan_date.isoformat(),
                        w.stage, (w.meta or {}).get("ep_strategy", "—"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
