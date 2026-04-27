#!/usr/bin/env python3
"""
Audit today's day-2 confirm price-data failures: which expired rows would
have confirmed (current price > gap_day_close) and triggered an order had
fetch_current_price not been broken?

Background: from 2026-04-23 through 2026-04-27, fetch_current_price was
checking hasattr(snap, "latest_trade") on the dict returned by
executor/alpaca_client.py::get_snapshots, so every day-2 confirm path
returned None and tagged the row [bot-failure]. The bug is fixed in
core/execution.py as of commit 756390a (2026-04-27 ~16:04 ET, deployed
~16:04 ET) — but Monday's 3:45 PM confirm had already run on the broken
code and dropped today's Strategy C candidates.

This script reads today's [bot-failure]-tagged rows, fetches their
current price via the (now-fixed) fetch_current_price, and reports which
ones would have confirmed. It is read-only: no DB writes, no orders.

Output is informational only — by the time you run this, the strategy's
3:50 PM ET entry window is closed. The point is to estimate the cost of
the missed day so you know whether to backfill any rows manually or to
skip them and wait for the next cycle.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/audit_day2_failures.py
    # Optional: --date YYYY-MM-DD to audit a different day (default: today ET).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pytz
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.execution import fetch_current_price
from db.models import Watchlist, get_session, init_db
from executor.alpaca_client import AlpacaClient

TAG = "[bot-failure]"
ET = pytz.timezone("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("audit_day2")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def _et_day_bounds_utc(target_et: date) -> tuple[datetime, datetime]:
    """Return UTC datetimes covering target_et 00:00–24:00 ET."""
    start_et = ET.localize(datetime.combine(target_et, dtime.min))
    end_et = ET.localize(datetime.combine(target_et + timedelta(days=1), dtime.min))
    return start_et.astimezone(pytz.UTC).replace(tzinfo=None), end_et.astimezone(pytz.UTC).replace(tzinfo=None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=datetime.now(ET).date(),
        help="ET date to audit (default: today)",
    )
    args = parser.parse_args()

    config = load_config()
    engine = init_db(config["database"]["url"])
    client = AlpacaClient(config)
    # AlpacaClient.__init__ does not connect — _data/_trade stay None until
    # .connect() runs. Without this call, get_snapshots blows up on
    # "'NoneType' object has no attribute 'get_stock_snapshot'".
    client.connect()

    start_utc, end_utc = _et_day_bounds_utc(args.date)
    logger.info("=" * 78)
    logger.info("AUDIT day-2 confirm [bot-failure] rows — ET date %s", args.date)
    logger.info("UTC window: %s → %s", start_utc, end_utc)
    logger.info("=" * 78)

    # Filter by updated_at, not stage_changed_at: the plugin code flips
    # stage="expired" without explicitly bumping stage_changed_at, and the
    # column only has a default-on-insert (no onupdate hook), so its value
    # stays at the original scan-time timestamp. updated_at has onupdate=
    # datetime.utcnow and reliably reflects the day-2 confirm save.
    rows: list[Watchlist] = []
    with get_session(engine) as session:
        candidates = session.query(Watchlist).filter(
            Watchlist.stage == "expired",
            Watchlist.setup_type.in_(["ep_earnings", "ep_news"]),
            Watchlist.updated_at >= start_utc,
            Watchlist.updated_at < end_utc,
        ).all()
        for row in candidates:
            if TAG in (row.notes or ""):
                rows.append(row)

    if not rows:
        logger.info("No [bot-failure] rows found for %s. Nothing to audit.", args.date)
        return 0

    logger.info("Found %d [bot-failure] rows from %s day-2 confirm", len(rows), args.date)
    logger.info("-" * 78)
    logger.info(
        "%-8s %-12s %-10s %-10s %-10s %-8s %s",
        "ticker", "setup", "gap_close", "now_price", "1D_chg%", "would?", "stop_if_confirmed",
    )
    logger.info("-" * 78)

    would_confirm = 0
    would_reject = 0
    fetch_errors = 0

    for row in rows:
        meta = row.meta or {}
        gap_close = float(meta.get("gap_day_close", 0))
        stop_pct = float(meta.get("stop_loss_pct", 7.0))

        if gap_close <= 0:
            logger.info(
                "%-8s %-12s %-10s — missing gap_day_close in meta, skipping",
                row.ticker, row.setup_type, "?",
            )
            continue

        try:
            now_price = fetch_current_price(client, row.ticker, attempts=2, sleep_secs=1.0)
        except Exception as e:
            logger.warning("%-8s fetch failed: %s", row.ticker, e)
            fetch_errors += 1
            continue

        if now_price is None:
            logger.info(
                "%-8s %-12s %-10.2f no_price  —          —        FETCH-MISS",
                row.ticker, row.setup_type, gap_close,
            )
            fetch_errors += 1
            continue

        chg_pct = (now_price - gap_close) / gap_close * 100
        confirmed = now_price > gap_close
        stop_if_confirmed = round(now_price * (1 - stop_pct / 100), 2)

        if confirmed:
            would_confirm += 1
            verdict = "CONFIRM"
        else:
            would_reject += 1
            verdict = "reject"

        stop_str = f"${stop_if_confirmed:.2f} (-{stop_pct:.0f}%)"
        logger.info(
            "%-8s %-12s %-10.2f %-10.2f %+9.2f%% %-8s %s",
            row.ticker, row.setup_type, gap_close, now_price, chg_pct, verdict, stop_str,
        )

    logger.info("-" * 78)
    logger.info(
        "Summary: %d would-confirm, %d would-reject, %d fetch errors (of %d total)",
        would_confirm, would_reject, fetch_errors, len(rows),
    )

    if would_confirm:
        logger.info("")
        logger.info("⚠  %d positions were missed today due to the price-data bug.", would_confirm)
        logger.info("   Strategy entry was 3:50 PM ET — that window is closed.")
        logger.info("   Consider whether to enter at next-day open or skip the cycle.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
