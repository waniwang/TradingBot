"""
Cross-DB watchlist reader for the IB passive executor.

The IB bot does not run its own scanner. Instead it reads `Watchlist` rows
that the Alpaca bot's scanner+day-2-confirm jobs have already vetted into
stage="ready" (or stage="triggered" if Alpaca already executed them). This
keeps the strategy/scanner code single-sourced — every scanner improvement
shipped to the Alpaca pipeline automatically applies to IB execution.

Design notes:
- Opens a SEPARATE SQLAlchemy engine against the Alpaca DB. We never reuse
  the IB bot's `db_engine`, since that points at trading_bot_ib.db.
- Read-only access pattern. We never UPDATE the Alpaca Watchlist row from
  the IB process — the Alpaca bot owns the row's lifecycle (ready→triggered→
  expired). The IB bot tracks its own execution state via the local
  Order/Position tables in trading_bot_ib.db.
- The `triggered` filter inclusion is critical: the Alpaca bot flips
  ready→triggered the moment IT executes, but the IB bot may not have
  run its own execute loop yet. Including `triggered` ensures IB still
  sees the row regardless of which broker's execute fires first.
- Engines are cached per URL so we don't re-open the file on every fire.
"""

from __future__ import annotations

import logging
from datetime import date as Date, timedelta
from threading import Lock
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Watchlist

logger = logging.getLogger(__name__)

# Stages that represent "Alpaca's scanner+day2 said this is a real trade idea
# for today." We include both "ready" (vetted, not yet executed by Alpaca) and
# "triggered" (vetted, already executed by Alpaca). For IB, both mean: place
# the order on IBKR using this entry/stop payload, subject to IB-side
# idempotency checks against trading_bot_ib.db.
_VALID_STAGES = ("ready", "triggered")

# Maximum age (in CALENDAR days) of a Watchlist row's scan_date relative to
# `today`. Strategy intent: A/B fire same-day (scan_date == today), C fires
# on day-2 (scan_date == today - 1 trading day). 4 calendar days covers the
# common Friday→Monday weekend gap plus a holiday buffer. Anything older is
# stale — almost certainly a row Alpaca processed days ago whose `triggered`
# state was never cleared, and which IBKR's local-DB idempotency cannot
# detect because IBKR's tables have no record of it. Hardcoding the bound
# here is the only defense against IBKR re-firing those rows on every cron
# tick into the indefinite future.
_MAX_SCAN_AGE_DAYS = 4

_engine_cache: dict[str, Any] = {}
_cache_lock = Lock()


def _get_engine(db_url: str):
    """Return a cached SQLAlchemy engine for ``db_url``.

    SQLite reader-side: we leave the engine in default mode. The Alpaca writer
    process is responsible for putting the DB in WAL mode (one-time PRAGMA at
    init); once that's done, concurrent reads from this process are safe.
    """
    with _cache_lock:
        engine = _engine_cache.get(db_url)
        if engine is None:
            engine = create_engine(db_url, future=True)
            _engine_cache[db_url] = engine
        return engine


def read_ready_entries(
    alpaca_db_url: str,
    setup_type: str,
    today: Date,
    max_age_days: int = _MAX_SCAN_AGE_DAYS,
) -> list[dict]:
    """
    Read ready/triggered Watchlist rows from the Alpaca DB for ``today``.

    Args:
        alpaca_db_url: SQLAlchemy URL pointing at the Alpaca DB (e.g.
            "sqlite:////opt/trading-bot/trading-bot/trading_bot.db").
        setup_type: "ep_earnings" or "ep_news".
        today: Date the IB bot considers "today" in ET. The filter accepts
            rows whose ``scan_date`` is in ``[today - max_age_days, today]``.
            This both keeps yesterday's day-2 C candidates (scan_date ==
            yesterday) and rejects ``triggered`` rows from prior weeks that
            Alpaca never cleaned up.
        max_age_days: Calendar-day staleness cap. Defaults to 4 days
            (Friday → Tuesday with a holiday buffer). Override only in tests.

    Returns:
        List of dicts shaped like the existing job_execute entries:
        ``{"ticker": str, "ep_strategy": "A"|"B"|"C", "entry_price": float,
        "stop_price": float, ...}``. Rows whose meta lacks ``ep_strategy``
        are silently skipped (the Alpaca scanner wouldn't have promoted such
        a row to ready, but defensive parity with the local-DB path).
    """
    engine = _get_engine(alpaca_db_url)
    entries: list[dict] = []
    earliest = today - timedelta(days=max_age_days)
    stale_skipped = 0

    # Use a plain session — read-only intent, no commit.
    with Session(engine) as session:
        rows = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type == setup_type,
                Watchlist.stage.in_(_VALID_STAGES),
                Watchlist.scan_date >= earliest,
                Watchlist.scan_date <= today,
            )
            .all()
        )
        # Defense-in-depth: also count any rows that would have matched the
        # old (no-lower-bound) filter so the operator sees the count gap in
        # the logs and can decide whether to run cleanup.
        stale_count = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type == setup_type,
                Watchlist.stage.in_(_VALID_STAGES),
                Watchlist.scan_date < earliest,
            )
            .count()
        )
        stale_skipped = stale_count

        for wl in rows:
            meta = wl.meta or {}
            if not meta.get("ep_strategy"):
                logger.warning(
                    "watchlist_source: ready row %s (%s) has no ep_strategy — skipping",
                    wl.ticker, setup_type,
                )
                continue
            entries.append({"ticker": wl.ticker, **meta})

    logger.info(
        "watchlist_source: %d ready/triggered %s rows in [%s, %s] from %s "
        "(skipped %d stale rows older than %d days)",
        len(entries), setup_type, earliest, today, alpaca_db_url,
        stale_skipped, max_age_days,
    )
    if stale_skipped > 0:
        logger.warning(
            "watchlist_source: %d stale %s rows ignored — run "
            "scripts/expire_stale_triggered_rows.py to clean them up.",
            stale_skipped, setup_type,
        )
    return entries
