#!/usr/bin/env python3
"""
Detect phantom broker positions: tickers that Alpaca reports as open but
have no corresponding `Position(is_open=True)` row in our DB.

This is the missing safety net for the daemon-thread bug in
`core/execution.py::_await_fill_and_setup_stop`: if that thread dies after
the entry order fills but before the Position row is inserted, the broker
holds the shares but the bot has no record. Stop checks, partial exits,
and trailing logic never run, and the operator has no idea.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/phantom_positions_check.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import Order, Position, get_session, init_db
from executor.alpaca_client import AlpacaClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("phantom_positions_check")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def main() -> int:
    cfg = load_config()
    engine = init_db(cfg["database"]["url"])
    client = AlpacaClient(cfg, notify=None)
    client.connect()

    broker_positions = client.get_open_positions()
    broker_by_symbol = {p["symbol"]: p for p in broker_positions}
    logger.info("Alpaca reports %d open positions: %s",
                len(broker_positions), sorted(broker_by_symbol.keys()))

    with get_session(engine) as session:
        db_open = session.query(Position).filter(Position.is_open == True).all()
        db_open_by_ticker: dict[str, Position] = {}
        for p in db_open:
            db_open_by_ticker.setdefault(p.ticker, p)
        logger.info("DB reports %d open positions: %s",
                    len(db_open), sorted({p.ticker for p in db_open}))

        # Phantom: broker has it, DB doesn't
        phantoms = [sym for sym in broker_by_symbol if sym not in db_open_by_ticker]
        # Orphans: DB has it, broker doesn't (already-closed but DB lagging)
        orphans = [t for t in db_open_by_ticker if t not in broker_by_symbol]

        print()
        print("=" * 78)
        print(f"PHANTOM positions (broker has, DB missing): {len(phantoms)}")
        print("=" * 78)
        for sym in sorted(phantoms):
            bp = broker_by_symbol[sym]
            related_orders = (
                session.query(Order)
                .filter(Order.ticker == sym)
                .order_by(Order.created_at.desc())
                .limit(3)
                .all()
            )
            print(f"  {sym}: qty={bp['qty']} avg_entry=${bp['avg_entry_price']:.2f} "
                  f"current=${bp['current_price']:.2f} unrealized_pl=${bp['unrealized_pl']:+.2f}")
            for o in related_orders:
                print(f"    Order id={o.id} side={o.side} qty={o.qty} "
                      f"filled_qty={o.filled_qty} filled_avg=${(o.filled_avg_price or 0):.2f} "
                      f"status={o.status} created={o.created_at}")

        print()
        print("=" * 78)
        print(f"ORPHAN DB rows (DB open, broker closed): {len(orphans)}")
        print("=" * 78)
        for t in sorted(orphans):
            p = db_open_by_ticker[t]
            print(f"  {t}: id={p.id} setup={p.setup_type} side={p.side} "
                  f"shares={p.shares} entry=${p.entry_price:.2f} stop=${p.stop_price:.2f}")

        print()
        print(f"SUMMARY: {len(phantoms)} phantom(s), {len(orphans)} orphan(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
