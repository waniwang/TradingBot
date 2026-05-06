#!/usr/bin/env python3
"""
Collect every "trade the bot should have taken but didn't" into a single CSV
that downstream systems (Google Sheets, dashboards, audits) can read.

Two data sources, unified into one CSV via a `category` column:

  1. Watchlist rows tagged [bot-failure] — bugs that prevented a trade.
     category = "bot-bug"

  2. RiskSkip rows (block_reason=max_positions) — trade triggered but the
     position cap was full. The bot was working as configured; the row is
     surfaced so the operator can see the cost of the cap and decide whether
     to raise it. category = "max-positions"

What counts as a missed trade (category=bot-bug)
------------------------------------------------
A Watchlist row that was tagged [bot-failure] AND, when we replay it against
historical daily bars, would have confirmed and entered. Per-strategy:

  * EP Earnings/News A & B: rows that hit stage="expired" with [bot-failure]
    on gap day (rare — the price-data path through fetch_current_price was
    only used in day-2 confirm; A/B don't go through it). Included for
    completeness — if the tag gets reused for other failures later, this
    collector picks them up automatically.

  * EP Earnings/News C: rows where the day-2 confirm failed to fetch a price.
    We re-check using the historical daily bar for day-2: would current_price
    have exceeded gap_day_close? If yes → would_confirm=True → missed trade.

What counts as a risk skip (category=max-positions)
---------------------------------------------------
A RiskSkip row written by ep_earnings/ep_news job_execute when the position
cap was full at the moment of execute (3:50 PM ET). The trade had already
triggered all setup conditions — would_confirm is always "yes" for these.

Output
------
CSV at docs/missed_trades.csv with columns:
    date              ET date of the event
    failure_time      ISO timestamp of the event (UTC)
    ticker
    strategy          ep_earnings_a / ep_earnings_b / ep_earnings_c
                      ep_news_a / ep_news_b / ep_news_c
    intended_entry    the price the bot WOULD have entered at
    intended_stop     intended_entry * (1 - stop_pct/100)
    would_confirm     "yes" / "no" / "unknown" — only meaningful for bot-bug
    reason            short text describing why the bot didn't trade
    incident_commit   git commit that fixed the relevant bug (bot-bug only)
    category          "bot-bug" or "max-positions"

Idempotent: running twice rewrites the file with the same content (the data
is fully derived from the DB, no incremental state). The nightly cron is
just `run script → git add → git commit if changed → push`.

Usage (on server):
    cd /opt/trading-bot/trading-bot \
      && set -a && source .env && set +a \
      && .venv/bin/python scripts/collect_missed_trades.py \
         --output docs/missed_trades.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz
import yaml

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from db.models import RiskSkip, Watchlist, get_session, init_db
from executor.alpaca_client import AlpacaClient

TAG = "[bot-failure]"
ET = pytz.timezone("America/New_York")

# Map setup_type + ep_strategy → human-readable strategy label. Falls back to
# the raw setup_type if neither matches (defense against future strategy types).
STRATEGY_LABELS = {
    ("ep_earnings", "A"): "ep_earnings_a",
    ("ep_earnings", "B"): "ep_earnings_b",
    ("ep_earnings", "C"): "ep_earnings_c",
    ("ep_news", "A"): "ep_news_a",
    ("ep_news", "B"): "ep_news_b",
    ("ep_news", "C"): "ep_news_c",
}

# Documented incidents. Used to populate the incident_commit column so each
# row links back to the bug fix that prevents future occurrences. The date is
# the ET day of the failure batch.
INCIDENT_COMMITS = {
    date(2026, 4, 23): "756390a",  # First fetch_current_price hasattr bug hit
    date(2026, 4, 24): "756390a",  # Same bug, second day
    date(2026, 4, 27): "756390a",  # Same bug, third day; fix deployed at ~16:04 ET
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("collect_missed_trades")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def _strategy_label(setup_type: str, meta: dict) -> str:
    ep = (meta or {}).get("ep_strategy", "")
    return STRATEGY_LABELS.get((setup_type, ep), f"{setup_type}_{ep.lower()}" if ep else setup_type)


def _failure_et_date(row: Watchlist) -> date:
    """ET date of the day-2 confirm failure (= when stage flipped to expired).

    updated_at is the source of truth (stage_changed_at has no onupdate hook —
    see scripts/audit_day2_failures.py). Convert UTC → ET then take date().
    """
    if row.updated_at is None:
        return row.scan_date
    aware = pytz.UTC.localize(row.updated_at) if row.updated_at.tzinfo is None else row.updated_at
    return aware.astimezone(ET).date()


def _short_reason(row: Watchlist) -> str:
    """Extract a short human-readable reason from the row's notes field.

    Today the only [bot-failure] producer is fetch_current_price returning
    None in day-2 confirm. If/when other failure paths start using the tag,
    expand this mapper.
    """
    notes = row.notes or ""
    if TAG not in notes:
        return "tagged-as-failure (no extra context)"
    if "snapshot error" in notes.lower():
        return "Alpaca snapshot raised during day-2 confirm"
    return "fetch_current_price returned None during day-2 confirm"


def _historical_day2_close(client, ticker: str, day2_date: date) -> float | None:
    """Pull the daily bar for the day-2 date and return its close.

    get_daily_bars_batch returns DataFrames with `date` as a COLUMN (Alpaca
    path) or as a DatetimeIndex (yfinance fallback) — handle both.
    """
    import pandas as pd
    today = datetime.now(ET).date()
    days_back = (today - day2_date).days + 7  # padding for weekends/holidays
    bars_by_ticker = client.get_daily_bars_batch([ticker], days=max(days_back, 30))
    df = bars_by_ticker.get(ticker)
    if df is None or df.empty:
        return None

    if "date" in df.columns:
        dates = pd.to_datetime(df["date"]).dt.date
        match = df[dates == day2_date]
        if match.empty:
            return None
        return float(match.iloc[0]["close"])

    # DatetimeIndex form (yfinance fallback typically lands here)
    for idx in df.index:
        d = idx.date() if hasattr(idx, "date") else idx
        if d == day2_date:
            return float(df.loc[idx, "close"])
    return None


def _row_to_csv(row: Watchlist, client) -> dict | None:
    """Map a Watchlist row to a CSV-ready dict. Returns None if the row
    cannot be classified as a missed trade (e.g. couldn't fetch day-2 price)."""
    meta = row.meta or {}
    failure_date = _failure_et_date(row)
    gap_close = float(meta.get("gap_day_close", 0))
    stop_pct = float(meta.get("stop_loss_pct", 7.0))

    if gap_close <= 0:
        # Can't determine "would have confirmed" without a gap-day close —
        # most likely a non-day-2 [bot-failure] row that we don't yet know
        # how to classify. Emit it with would_confirm="unknown" so it shows
        # up in the tracker rather than getting silently dropped.
        return {
            "date": failure_date.isoformat(),
            "failure_time": (row.updated_at or datetime.utcnow()).isoformat() + "Z",
            "ticker": row.ticker,
            "strategy": _strategy_label(row.setup_type, meta),
            "intended_entry": "",
            "intended_stop": "",
            "would_confirm": "unknown",
            "reason": _short_reason(row),
            "incident_commit": INCIDENT_COMMITS.get(failure_date, ""),
            "category": "bot-bug",
        }

    day2_close = _historical_day2_close(client, row.ticker, failure_date)
    if day2_close is None:
        logger.warning("%s: no day-2 daily bar for %s — leaving prices blank",
                       row.ticker, failure_date)
        return {
            "date": failure_date.isoformat(),
            "failure_time": (row.updated_at or datetime.utcnow()).isoformat() + "Z",
            "ticker": row.ticker,
            "strategy": _strategy_label(row.setup_type, meta),
            "intended_entry": "",
            "intended_stop": "",
            "would_confirm": "unknown",
            "reason": _short_reason(row) + " (day-2 bar unavailable)",
            "incident_commit": INCIDENT_COMMITS.get(failure_date, ""),
            "category": "bot-bug",
        }

    would_confirm = day2_close > gap_close
    intended_stop = round(day2_close * (1 - stop_pct / 100), 2)

    return {
        "date": failure_date.isoformat(),
        "failure_time": (row.updated_at or datetime.utcnow()).isoformat() + "Z",
        "ticker": row.ticker,
        "strategy": _strategy_label(row.setup_type, meta),
        "intended_entry": f"{day2_close:.2f}",
        "intended_stop": f"{intended_stop:.2f}",
        "would_confirm": "yes" if would_confirm else "no",
        "reason": _short_reason(row),
        "incident_commit": INCIDENT_COMMITS.get(failure_date, ""),
        "category": "bot-bug",
    }


def _risk_skip_to_csv(row: RiskSkip) -> dict:
    """Map a RiskSkip row to a CSV-ready dict.

    For risk skips, would_confirm is always "yes" — the trade had already
    triggered all setup conditions (we only call can_enter() at the execute
    step, after day-2 confirm + signal eval). The block was the cap, not the
    setup. The reason field summarizes the cap state at skip time.
    """
    fake_meta = {"ep_strategy": row.ep_strategy} if row.ep_strategy else {}
    intended_entry = f"{row.intended_entry:.2f}" if row.intended_entry is not None else ""
    intended_stop = f"{row.intended_stop:.2f}" if row.intended_stop is not None else ""
    cap_note = row.notes or ""
    reason = f"Position cap full at execute time ({cap_note})" if cap_note else "Position cap full at execute time"
    return {
        "date": row.occurred_date.isoformat(),
        "failure_time": row.occurred_at.isoformat() + "Z",
        "ticker": row.ticker,
        "strategy": _strategy_label(row.setup_type, fake_meta),
        "intended_entry": intended_entry,
        "intended_stop": intended_stop,
        "would_confirm": "yes",
        "reason": reason,
        "incident_commit": "",
        "category": "max-positions",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "docs" / "missed_trades.csv"),
        help="Path for the CSV output (default: docs/missed_trades.csv).",
    )
    parser.add_argument(
        "--include-rejects",
        action="store_true",
        help="Also include rows where would_confirm=no (default: skip — those "
             "are not really 'missed trades', they would have been correctly "
             "rejected anyway).",
    )
    args = parser.parse_args()

    config = load_config()
    engine = init_db(config["database"]["url"])
    client = AlpacaClient(config)
    client.connect()

    logger.info("=" * 78)
    logger.info("COLLECT missed trades from Watchlist [bot-failure] tag")
    logger.info("Output: %s", args.output)
    logger.info("=" * 78)

    rows: list[dict] = []
    with get_session(engine) as session:
        candidates = (
            session.query(Watchlist)
            .filter(
                Watchlist.notes.ilike(f"%{TAG}%"),
            )
            .order_by(Watchlist.updated_at.asc(), Watchlist.ticker.asc())
            .all()
        )
        for row in candidates:
            csv_row = _row_to_csv(row, client)
            if csv_row is None:
                continue
            if not args.include_rejects and csv_row["would_confirm"] == "no":
                continue
            rows.append(csv_row)

        skip_rows = (
            session.query(RiskSkip)
            .order_by(RiskSkip.occurred_at.asc(), RiskSkip.ticker.asc())
            .all()
        )
        for skip in skip_rows:
            rows.append(_risk_skip_to_csv(skip))

    rows.sort(key=lambda r: (r["date"], r["failure_time"], r["ticker"]))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date", "failure_time", "ticker", "strategy",
        "intended_entry", "intended_stop", "would_confirm",
        "reason", "incident_commit", "category",
    ]
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    logger.info("-" * 78)
    logger.info("Wrote %d rows to %s", len(rows), output_path)
    yes_count = sum(1 for r in rows if r["would_confirm"] == "yes")
    bug_count = sum(1 for r in rows if r["category"] == "bot-bug")
    skip_count = sum(1 for r in rows if r["category"] == "max-positions")
    logger.info("  would-have-entered: %d", yes_count)
    logger.info("  bot-bug rows: %d", bug_count)
    logger.info("  max-positions rows: %d", skip_count)
    logger.info("  unknown / no day-2 bar: %d",
                sum(1 for r in rows if r["would_confirm"] == "unknown"))
    if not args.include_rejects:
        logger.info("  (would-have-rejected rows excluded — pass --include-rejects to keep)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
