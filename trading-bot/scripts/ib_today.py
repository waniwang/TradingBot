#!/usr/bin/env python3
"""
Show today's IBKR bot activity: every Order placed, every Position opened/
closed, on trading_bot_ib.db. The Alpaca DB is the source of truth for
WHAT to trade; the IB DB is where IBKR-side execution records live.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source config.ib.local.yaml && set +a \
      && BOT_CONFIG=config.ib.local.yaml .venv/bin/python scripts/ib_today.py

Or with an explicit DB url:
    DATABASE_IB_URL=sqlite:////opt/trading-bot/trading-bot/trading_bot_ib.db \
      .venv/bin/python scripts/ib_today.py
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

from db.models import Order, Position, get_session, init_db

ET = pytz.timezone("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("ib_today")


def load_ib_config() -> dict:
    """Load IB-flavored config. Falls back to env DATABASE_IB_URL if no
    config.ib.local.yaml present."""
    candidate = ROOT / "config.ib.local.yaml"
    if candidate.exists():
        with candidate.open() as f:
            cfg = yaml.safe_load(f)
    else:
        cfg = {"database_ib": {}}
    if os.environ.get("DATABASE_IB_URL"):
        cfg.setdefault("database_ib", {})["url"] = os.environ["DATABASE_IB_URL"]
    if "url" not in cfg.get("database_ib", {}):
        # Last-resort default: the conventional file location on the server.
        cfg.setdefault("database_ib", {})["url"] = "sqlite:////opt/trading-bot/trading-bot/trading_bot_ib.db"
    return cfg


def _et_day_bounds_utc(target_et: date) -> tuple[datetime, datetime]:
    start_et = ET.localize(datetime.combine(target_et, datetime.min.time()))
    end_et = ET.localize(datetime.combine(target_et + timedelta(days=1), datetime.min.time()))
    return start_et.astimezone(pytz.UTC).replace(tzinfo=None), end_et.astimezone(pytz.UTC).replace(tzinfo=None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=datetime.now(ET).date(),
        help="ET date to inspect (default: today).",
    )
    args = parser.parse_args()

    cfg = load_ib_config()
    engine = init_db(cfg["database_ib"]["url"])
    start_utc, end_utc = _et_day_bounds_utc(args.date)

    logger.info("=" * 78)
    logger.info("IBKR bot activity — ET date %s", args.date)
    logger.info("DB: %s", cfg["database_ib"]["url"])
    logger.info("UTC window: %s → %s", start_utc, end_utc)
    logger.info("=" * 78)

    with get_session(engine) as session:
        orders = (
            session.query(Order)
            .filter(Order.created_at >= start_utc, Order.created_at < end_utc)
            .order_by(Order.created_at.asc())
            .all()
        )
        opened_today = (
            session.query(Position)
            .filter(Position.opened_at >= start_utc, Position.opened_at < end_utc)
            .order_by(Position.opened_at.asc())
            .all()
        )
        closed_today = (
            session.query(Position)
            .filter(Position.closed_at >= start_utc, Position.closed_at < end_utc)
            .order_by(Position.closed_at.asc())
            .all()
        )
        currently_open = (
            session.query(Position)
            .filter(Position.is_open == True)
            .order_by(Position.opened_at.asc())
            .all()
        )

    # ---------- Orders ----------
    logger.info("")
    logger.info(">>> ORDERS PLACED TODAY: %d", len(orders))
    if orders:
        logger.info("%-8s %-12s %-6s %-6s %-9s %-9s %-12s",
                    "ticker", "side", "qty", "type", "price", "status", "time_et")
        for o in orders:
            time_et = pytz.UTC.localize(o.created_at).astimezone(ET).strftime("%H:%M:%S")
            logger.info("%-8s %-12s %-6d %-6s %-9.2f %-9s %-12s",
                        o.ticker, o.side, o.qty, o.order_type,
                        o.price or 0, o.status, time_et)

    # ---------- Positions opened ----------
    logger.info("")
    logger.info(">>> POSITIONS OPENED TODAY: %d", len(opened_today))
    if opened_today:
        logger.info("%-8s %-18s %-6s %-6s %-9s %-9s %-12s",
                    "ticker", "setup", "side", "qty", "entry", "stop", "time_et")
        for p in opened_today:
            time_et = pytz.UTC.localize(p.opened_at).astimezone(ET).strftime("%H:%M:%S")
            logger.info("%-8s %-18s %-6s %-6d %-9.2f %-9.2f %-12s",
                        p.ticker, p.setup_type, p.side, p.shares,
                        p.entry_price, p.stop_price, time_et)

    # ---------- Positions closed ----------
    logger.info("")
    logger.info(">>> POSITIONS CLOSED TODAY: %d", len(closed_today))
    if closed_today:
        logger.info("%-8s %-18s %-9s %-9s %-9s %-15s %-12s",
                    "ticker", "setup", "entry", "exit", "pnl", "reason", "time_et")
        for p in closed_today:
            time_et = pytz.UTC.localize(p.closed_at).astimezone(ET).strftime("%H:%M:%S") if p.closed_at else "?"
            logger.info("%-8s %-18s %-9.2f %-9.2f %-9.2f %-15s %-12s",
                        p.ticker, p.setup_type, p.entry_price,
                        p.exit_price or 0, p.realized_pnl or 0,
                        (p.exit_reason or "?")[:14], time_et)

    # ---------- Currently open ----------
    logger.info("")
    logger.info(">>> CURRENTLY OPEN POSITIONS: %d", len(currently_open))
    if currently_open:
        logger.info("%-8s %-18s %-6d %-9s %-9s %-9s %-5s",
                    "ticker", "setup", 0, "entry", "stop", "opened_d", "days")
        for p in currently_open:
            days = (date.today() - p.opened_at.date()).days
            logger.info("%-8s %-18s %-6d %-9.2f %-9.2f %-9s %-5d",
                        p.ticker, p.setup_type, p.shares, p.entry_price,
                        p.stop_price, p.opened_at.date().isoformat(), days)

    logger.info("")
    logger.info("=" * 78)
    logger.info("Summary: %d orders, %d opened, %d closed today; %d currently open",
                len(orders), len(opened_today), len(closed_today), len(currently_open))
    return 0


if __name__ == "__main__":
    sys.exit(main())
