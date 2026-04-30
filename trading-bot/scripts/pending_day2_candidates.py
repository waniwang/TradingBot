#!/usr/bin/env python3
"""
List Strategy C candidates currently sitting in stage="watching", waiting for
the 3:45 PM ET day-2 confirm. For each one, fetch the current price and show
whether it's ABOVE or BELOW its gap-day close — i.e. predict whether it'll
confirm at 3:45 PM (price > gap_day_close → confirm → enter at 3:50 PM).

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/pending_day2_candidates.py [--scan-date YYYY-MM-DD]

Default scan-date: yesterday ET (the gap day for today's day-2 confirm).
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

from core.execution import fetch_current_price
from db.models import Watchlist, get_session, init_db
from executor.alpaca_client import AlpacaClient

ET = pytz.timezone("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("pending_day2")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    yesterday_et = (datetime.now(ET) - timedelta(days=1)).date()
    parser.add_argument(
        "--scan-date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=yesterday_et,
        help=f"ET scan_date to inspect (default: yesterday = {yesterday_et}).",
    )
    args = parser.parse_args()

    config = load_config()
    engine = init_db(config["database"]["url"])
    client = AlpacaClient(config)
    client.connect()

    logger.info("=" * 78)
    logger.info("PENDING day-2 candidates — scan_date %s, stage=watching", args.scan_date)
    logger.info("=" * 78)

    with get_session(engine) as session:
        rows = (
            session.query(Watchlist)
            .filter(
                Watchlist.scan_date == args.scan_date,
                Watchlist.stage == "watching",
                Watchlist.setup_type.in_(["ep_earnings", "ep_news"]),
            )
            .order_by(Watchlist.setup_type.asc(), Watchlist.ticker.asc())
            .all()
        )

    if not rows:
        logger.info("No pending C candidates for %s. (Either no earnings/news gaps "
                    "qualified at the 3:00 PM scan, or day-2 confirm already ran "
                    "and flipped them all to ready/expired.)", args.scan_date)
        return 0

    logger.info("Found %d pending candidate(s):", len(rows))
    logger.info("-" * 78)
    logger.info("%-8s %-12s %-10s %-10s %-9s %s",
                "ticker", "setup", "gap_close", "now_price", "1D_chg%", "predict")
    logger.info("-" * 78)

    would_confirm = 0
    would_reject = 0

    for r in rows:
        meta = r.meta or {}
        gap_close = float(meta.get("gap_day_close", 0))
        if gap_close <= 0:
            logger.warning("%-8s %-12s missing gap_day_close in meta", r.ticker, r.setup_type)
            continue

        try:
            now_price = fetch_current_price(client, r.ticker, attempts=2, sleep_secs=1.0)
        except Exception as e:
            logger.warning("%-8s %-12s fetch failed: %s", r.ticker, r.setup_type, e)
            continue

        if now_price is None:
            logger.warning("%-8s %-12s no price available", r.ticker, r.setup_type)
            continue

        chg = (now_price - gap_close) / gap_close * 100
        verdict = "CONFIRM" if now_price > gap_close else "reject"
        if verdict == "CONFIRM":
            would_confirm += 1
        else:
            would_reject += 1

        logger.info("%-8s %-12s %-10.2f %-10.2f %+8.2f%% %s",
                    r.ticker, r.setup_type, gap_close, now_price, chg, verdict)

    logger.info("-" * 78)
    logger.info("Summary: %d would CONFIRM, %d would reject, %d total",
                would_confirm, would_reject, len(rows))
    logger.info("(Live read — final verdict comes at 3:45 PM ET when day-2 confirm runs.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
