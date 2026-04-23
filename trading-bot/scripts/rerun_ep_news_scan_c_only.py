#!/usr/bin/env python3
"""
One-off recovery: rerun EP news scan for today and persist ONLY Strategy C
candidates (as stage="watching") for tomorrow's day-2 confirm.

Use this when the scheduled `ep_news_scan` job fails on its 15:05 ET run and
you want to salvage the day-2 path for tomorrow's execute. A/B candidates are
intentionally NOT persisted — their same-day 15:50 execute window is gone
once the market closes, so staging them would risk a stale entry at tomorrow's
prices.

Safety:
  - Only writes stage="watching" rows (C-pending), never stage="ready"
  - Skips tickers that already have a watchlist row for today (idempotent)
  - Prints a full summary; does not silently modify state

Usage (on the Linode server):
    cd /opt/trading-bot/trading-bot \
      && .venv/bin/python scripts/rerun_ep_news_scan_c_only.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pytz
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import Watchlist, get_session, init_db
from executor.alpaca_client import AlpacaClient
from strategies.ep_news.plugin import EPNewsPlugin
from strategies.ep_news.scanner import scan_ep_news
from strategies.ep_news.strategy import evaluate_ep_news_strategies

ET = pytz.timezone("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("rerun_ep_news_c")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    # Match main.py's env-var overrides so this runs with the same auth
    if os.environ.get("ALPACA_API_KEY"):
        cfg.setdefault("alpaca", {})["api_key"] = os.environ["ALPACA_API_KEY"]
    if os.environ.get("ALPACA_SECRET_KEY"):
        cfg.setdefault("alpaca", {})["secret_key"] = os.environ["ALPACA_SECRET_KEY"]
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]

    # Merge per-strategy config.yaml (scanner filters, etc.)
    strategies_dir = ROOT / "strategies"
    cfg.setdefault("strategies", {})
    for d in sorted(strategies_dir.iterdir()):
        if d.is_dir() and (d / "config.yaml").exists():
            with open(d / "config.yaml") as sf:
                cfg["strategies"].setdefault(d.name, {}).update(yaml.safe_load(sf) or {})

    return cfg


def main() -> int:
    config = load_config()
    client = AlpacaClient(config)
    client.connect()
    engine = init_db(config["database"]["url"])

    today = datetime.now(ET).date()
    logger.info("=" * 60)
    logger.info("EP NEWS SCAN RECOVERY — %s (C-pending only)", today)
    logger.info("=" * 60)

    candidates = scan_ep_news(config, client)
    if not candidates:
        logger.info("No candidates found — nothing to persist.")
        return 0

    logger.info("Scanner returned %d candidates: %s",
                len(candidates), ", ".join(c["ticker"] for c in candidates))

    tickers = [c["ticker"] for c in candidates]
    daily_bars = client.get_daily_bars_batch(tickers, days=300)
    logger.info("Daily bars fetched for %d/%d tickers", len(daily_bars), len(tickers))

    entries, rejections = evaluate_ep_news_strategies(candidates, daily_bars, config)
    pending_c = [e for e in entries if e.get("day2_confirm")]
    immediate_ab = [e for e in entries if not e.get("day2_confirm")]

    logger.info("Evaluation: %d entries — A/B (skipped): %d, C-pending (to persist): %d",
                len(entries), len(immediate_ab), len(pending_c))

    for r in rejections:
        if r.get("is_data_error"):
            logger.warning("  DATA ERROR %s: %s", r["ticker"], r["reason"])

    if immediate_ab:
        logger.info("A/B entries NOT persisted (same-day execute window closed):")
        for e in immediate_ab:
            logger.info("  %s (%s): gap %.1f%%",
                        e["ticker"], e["ep_strategy"], e["gap_pct"])

    if not pending_c:
        logger.info("No Strategy C candidates in this rerun — nothing to persist.")
        return 0

    plugin = EPNewsPlugin()
    persisted = 0
    skipped = 0
    for entry in pending_c:
        with get_session(engine) as session:
            existing = session.query(Watchlist).filter_by(
                ticker=entry["ticker"],
                setup_type="ep_news",
                scan_date=today,
            ).first()
        if existing:
            logger.info("  %s: watchlist row already exists for %s — skipping",
                        entry["ticker"], today)
            skipped += 1
            continue
        plugin._persist_pending_day2(entry, today, engine)
        logger.info("  %s: persisted C-pending (gap %.1f%%, gap close $%.2f)",
                    entry["ticker"], entry["gap_pct"], entry["gap_day_close"])
        persisted += 1

    logger.info("=" * 60)
    logger.info("RECOVERY COMPLETE")
    logger.info("  Persisted:                        %d", persisted)
    logger.info("  Skipped (duplicate row for today): %d", skipped)
    logger.info("  A/B not persisted (by design):    %d", len(immediate_ab))
    logger.info("Tomorrow's ep_news_day2_confirm at 15:45 ET will pick these up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
