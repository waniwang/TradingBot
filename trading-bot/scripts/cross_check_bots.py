#!/usr/bin/env python3
"""
Side-by-side comparison of every (ticker, strategy) the Alpaca and IBKR
bots TRIED, FILLED, or CANCELLED on a given ET date.

Purpose: catch divergence between the two bots — anything either tried that
the other didn't, plus differences in fill outcomes. Was the impetus on
2026-05-01: IB bot opened positions on tickers Alpaca didn't attempt
(stale-row leakage), and Alpaca got wash-trade-rejected on tickers IB
filled cleanly. Without a unified view it's painful to spot.

Output sections:
  1. ALPACA — every attempt today, per (ticker, setup_type)
  2. IBKR   — same for IB
  3. UNION — every (ticker, setup_type) appearing in either, side by side
  4. DIVERGENCES — only the rows where Alpaca and IB disagree

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && DATABASE_IB_URL=sqlite:////opt/trading-bot/trading-bot/trading_bot_ib.db \
         .venv/bin/python scripts/cross_check_bots.py
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

from db.models import Order, Position, Signal, get_session, init_db

ET = pytz.timezone("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("cross_check_bots")


def _et_day_bounds_utc(target_et: date) -> tuple[datetime, datetime]:
    start_et = ET.localize(datetime.combine(target_et, datetime.min.time()))
    end_et = ET.localize(datetime.combine(target_et + timedelta(days=1), datetime.min.time()))
    return start_et.astimezone(pytz.UTC).replace(tzinfo=None), end_et.astimezone(pytz.UTC).replace(tzinfo=None)


def _normalize_setup(setup_type: str) -> str:
    """Strip the _a/_b/_c suffix so we can compare across the two DBs.

    Alpaca's Position.setup_type may be the legacy ``ep_earnings`` form
    (pre-2026-04-24) or the per-variant ``ep_earnings_a/b/c`` form. IBKR's
    is always the per-variant form. We compare on the normalized name plus
    the variant separately."""
    parts = setup_type.rsplit("_", 1)
    if len(parts) == 2 and parts[1].lower() in ("a", "b", "c"):
        return parts[0]
    return setup_type


def _variant_from_setup(setup_type: str) -> str | None:
    parts = setup_type.rsplit("_", 1)
    if len(parts) == 2 and parts[1].lower() in ("a", "b", "c"):
        return parts[1].upper()
    return None


def _collect_bot_activity(engine, day_start_utc, day_end_utc, label: str) -> dict:
    """Build {(ticker, base_setup, variant): {orders: [...], position: Position|None, notes: str}}.

    Pulls every Order created today + every Position opened today from the
    given engine, grouped by (ticker, base_setup_type, variant). Variant is
    ``A/B/C`` extracted from the Order's linked Signal/Position setup_type.
    """
    activity: dict[tuple[str, str, str | None], dict] = {}

    with get_session(engine) as session:
        orders = (
            session.query(Order)
            .filter(Order.created_at >= day_start_utc, Order.created_at < day_end_utc)
            .order_by(Order.created_at.asc())
            .all()
        )
        positions = (
            session.query(Position)
            .filter(Position.opened_at >= day_start_utc, Position.opened_at < day_end_utc)
            .order_by(Position.opened_at.asc())
            .all()
        )
        # Pre-fetch signals for variant lookup
        signal_by_id: dict[int, Signal] = {}
        signal_ids = {o.signal_id for o in orders if o.signal_id}
        if signal_ids:
            for s in session.query(Signal).filter(Signal.id.in_(signal_ids)).all():
                signal_by_id[s.id] = s

    # Index orders by (ticker, base_setup, variant)
    for o in orders:
        # Try to recover setup from linked Signal first; fall back to Order.ticker only
        setup_full = ""
        if o.signal_id and o.signal_id in signal_by_id:
            setup_full = signal_by_id[o.signal_id].setup_type or ""
        base_setup = _normalize_setup(setup_full) if setup_full else "?"
        variant = _variant_from_setup(setup_full)
        key = (o.ticker, base_setup, variant)
        a = activity.setdefault(key, {"orders": [], "position": None, "label": label})
        a["orders"].append(o)

    # Index positions by (ticker, base_setup, variant)
    for p in positions:
        base_setup = _normalize_setup(p.setup_type)
        variant = _variant_from_setup(p.setup_type)
        key = (p.ticker, base_setup, variant)
        a = activity.setdefault(key, {"orders": [], "position": None, "label": label})
        a["position"] = p

    return activity


def _summarize(act: dict) -> str:
    """Produce a one-line outcome string for a (ticker, setup, variant) entry.
    Examples: "1 filled / pos open" or "1 cancelled (no pos)" or "3 cancelled, 1 filled / pos open".
    """
    if not act:
        return "—"
    orders = act["orders"]
    pos = act["position"]

    counts: dict[str, int] = {}
    for o in orders:
        counts[o.status] = counts.get(o.status, 0) + 1
    parts = [f"{n} {s}" for s, n in sorted(counts.items())]
    order_summary = ", ".join(parts) if parts else "no orders"

    if pos is not None:
        pos_part = f" / pos {'open' if pos.is_open else 'closed'}"
    elif any(o.status == "filled" for o in orders):
        pos_part = " / no pos record"  # filled order without Position = bookkeeping issue
    else:
        pos_part = " / no pos"

    return order_summary + pos_part


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=datetime.now(ET).date(),
        help="ET date to inspect (default: today).",
    )
    args = parser.parse_args()

    alpaca_url = os.environ.get(
        "DATABASE_URL",
        "sqlite:////opt/trading-bot/trading-bot/trading_bot.db",
    )
    ib_url = os.environ.get(
        "DATABASE_IB_URL",
        "sqlite:////opt/trading-bot/trading-bot/trading_bot_ib.db",
    )
    # Both DBs share schema (db.models is identical for the two bots), so we
    # can use init_db on each independently — they don't share state.
    alpaca_engine = init_db(alpaca_url)
    ib_engine = create_engine(ib_url)
    # Make sure IB DB is reachable; init_db would create tables which we
    # explicitly DON'T want against the prod IB DB.
    from sqlalchemy.orm import sessionmaker
    _IbSession = sessionmaker(bind=ib_engine)

    # The IB engine needs the same get_session contract as Alpaca's; the
    # _collect_bot_activity helper accepts an engine that the global
    # get_session can drive. db.models.get_session is a contextmanager that
    # opens a Session on the passed engine — works for any engine.
    start_utc, end_utc = _et_day_bounds_utc(args.date)

    logger.info("=" * 102)
    logger.info("CROSS-CHECK Alpaca vs IBKR — ET date %s", args.date)
    logger.info("Alpaca DB: %s", alpaca_url)
    logger.info("IB DB:     %s", ib_url)
    logger.info("=" * 102)

    alpaca_act = _collect_bot_activity(alpaca_engine, start_utc, end_utc, "ALPACA")
    ib_act = _collect_bot_activity(ib_engine, start_utc, end_utc, "IBKR")

    all_keys = sorted(set(alpaca_act.keys()) | set(ib_act.keys()))

    logger.info("")
    logger.info(">>> UNION (every ticker either bot touched today)")
    logger.info("%-8s %-12s %-3s %-40s %-40s %s",
                "ticker", "setup", "var", "alpaca", "ibkr", "verdict")
    logger.info("-" * 102)

    diverged: list[tuple] = []
    alpaca_only: list[tuple] = []
    ib_only: list[tuple] = []
    matched: list[tuple] = []

    for key in all_keys:
        ticker, base_setup, variant = key
        a = alpaca_act.get(key, {})
        i = ib_act.get(key, {})
        a_summary = _summarize(a) if a else "(not attempted)"
        i_summary = _summarize(i) if i else "(not attempted)"

        # Verdict: compare order outcomes per side
        verdict = "—"
        if a and not i:
            verdict = "ALPACA-ONLY"
            alpaca_only.append((key, a_summary))
        elif i and not a:
            verdict = "IBKR-ONLY"
            ib_only.append((key, i_summary))
        else:
            # Both touched. Compare fill outcomes.
            a_filled = any(o.status == "filled" for o in a.get("orders", [])) or (a.get("position") is not None)
            i_filled = any(o.status == "filled" for o in i.get("orders", [])) or (i.get("position") is not None)
            if a_filled and i_filled:
                verdict = "both-filled"
                matched.append(key)
            elif (not a_filled) and (not i_filled):
                verdict = "both-cancelled"
                matched.append(key)
            else:
                verdict = "DIVERGED"
                diverged.append((key, a_summary, i_summary))

        logger.info("%-8s %-12s %-3s %-40s %-40s %s",
                    ticker, base_setup[:12], variant or "—",
                    a_summary[:40], i_summary[:40], verdict)

    logger.info("-" * 102)
    logger.info("Totals: %d unique (ticker, setup) | matched=%d | diverged=%d | alpaca-only=%d | ibkr-only=%d",
                len(all_keys), len(matched), len(diverged), len(alpaca_only), len(ib_only))

    # ---------------- Divergence detail ----------------
    if alpaca_only:
        logger.info("")
        logger.info(">>> ALPACA-ONLY (Alpaca attempted, IB did NOT — IB bot missed these)")
        for (ticker, base_setup, variant), summary in alpaca_only:
            logger.info("  %s %s/%s — %s", ticker, base_setup, variant or "—", summary)

    if ib_only:
        logger.info("")
        logger.info(">>> IBKR-ONLY (IB attempted, Alpaca did NOT — likely stale row leakage)")
        for (ticker, base_setup, variant), summary in ib_only:
            logger.info("  %s %s/%s — %s", ticker, base_setup, variant or "—", summary)

    if diverged:
        logger.info("")
        logger.info(">>> DIVERGED (both attempted, different outcomes — wash-trade, fill timing, etc.)")
        logger.info("  %-8s %-12s %-3s %-40s %s", "ticker", "setup", "var", "alpaca", "ibkr")
        for (ticker, base_setup, variant), a_summary, i_summary in diverged:
            logger.info("  %-8s %-12s %-3s %-40s %s",
                        ticker, base_setup[:12], variant or "—",
                        a_summary[:40], i_summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
