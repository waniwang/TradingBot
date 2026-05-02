"""
Tests for the IB passive-executor pattern.

The IB bot does not run scanners. Instead it reads ready/triggered Watchlist
rows from the Alpaca DB (the "source DB") and executes orders on IBKR. This
file exercises:

1. ``executor.watchlist_source.read_ready_entries`` returns the right rows
   from a populated source DB (ready + triggered, not watching/expired).
2. ``ep_earnings`` and ``ep_news`` ``job_execute`` use the cross-DB reader
   when ``config["watchlist_source_db_url"]`` is set.
3. With the key unset, both plugins still read from the local engine —
   Alpaca-bot path is unchanged.
4. ``register_strategy_jobs(skip_jobs=...)`` skips the named jobs.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import pytz

from db.models import Base, Watchlist, get_engine, get_session
from strategies.ep_earnings.plugin import EPEarningsPlugin
from strategies.ep_news.plugin import EPNewsPlugin

ET = pytz.timezone("America/New_York")


def _today_et():
    return datetime.now(ET).date()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_alpaca_db():
    """Create an on-disk SQLite DB and return its sqlalchemy URL.

    On-disk (not :memory:) so the watchlist_source helper can open a
    second engine to it via the URL — the same way the real IB bot does
    against trading_bot.db.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    url = f"sqlite:///{path}"
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    yield url, engine
    # NamedTemporaryFile is gone after the test; SQLite handles cleanup.


@pytest.fixture
def ib_local_engine():
    """In-memory engine playing the role of trading_bot_ib.db.

    The IB bot's local engine still tracks Order/Position/Signal rows for
    idempotency. The passive-executor change only swaps where Watchlist is
    read from.
    """
    engine = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def mock_ib_client():
    """Stub IBClient — we only need the methods job_execute touches before
    placing an order, and we stop at the entry-resolution / risk-check
    boundary. The actual order-placement path is exercised in
    test_ep_earnings_day2_confirm.py against the Alpaca client; we don't
    re-test it here."""
    client = MagicMock()
    client.get_portfolio_value.return_value = 100_000.0
    client.is_market_open.return_value = True
    client.get_realtime_quote.side_effect = lambda t: {
        "ticker": t, "bid": 21.90, "ask": 22.00, "last_price": 21.95,
    }
    return client


def _seed_watchlist_row(
    engine, ticker, setup_type, stage, scan_date, ep_strategy="A",
):
    """Insert one Watchlist row with a minimal valid meta payload."""
    meta = {
        "ep_strategy": ep_strategy,
        "gap_pct": 12.5,
        "entry_price": 22.0,
        "stop_price": 20.5,
        "stop_loss_pct": 7.0,
        "max_hold_days": 50,
        "chg_open_pct": 1.2,
        "close_in_range": 75.0,
        "downside_from_open": 1.0,
        "prev_10d_change_pct": -15.0,
        "atr_pct": 3.0,
        "open_price": 21.5,
        "prev_close": 19.0,
        "market_cap": 1_500_000_000,
        "rvol": 2.5,
    }
    with get_session(engine) as session:
        wl = Watchlist(
            ticker=ticker,
            setup_type=setup_type,
            stage=stage,
            scan_date=scan_date,
            metadata_json=json.dumps(meta),
        )
        session.add(wl)
        session.commit()


# ---------------------------------------------------------------------------
# read_ready_entries
# ---------------------------------------------------------------------------


def test_read_ready_entries_returns_ready_and_triggered(tmp_alpaca_db):
    """ready and triggered rows should both be returned — the Alpaca bot
    flips ready→triggered when IT executes, but the IB bot still needs to
    see the row regardless of which broker fired first."""
    from executor.watchlist_source import read_ready_entries

    url, engine = tmp_alpaca_db
    today = _today_et()
    _seed_watchlist_row(engine, "AAA", "ep_earnings", "ready", today)
    _seed_watchlist_row(engine, "BBB", "ep_earnings", "triggered", today, ep_strategy="B")
    _seed_watchlist_row(engine, "CCC", "ep_earnings", "watching", today)
    _seed_watchlist_row(engine, "DDD", "ep_earnings", "expired", today)

    entries = read_ready_entries(url, "ep_earnings", today)
    tickers = {e["ticker"] for e in entries}
    assert tickers == {"AAA", "BBB"}, f"expected only ready+triggered, got {tickers}"
    assert all("ep_strategy" in e for e in entries)


def test_read_ready_entries_filters_by_setup_type(tmp_alpaca_db):
    from executor.watchlist_source import read_ready_entries

    url, engine = tmp_alpaca_db
    today = _today_et()
    _seed_watchlist_row(engine, "AAA", "ep_earnings", "ready", today)
    _seed_watchlist_row(engine, "BBB", "ep_news", "ready", today)
    _seed_watchlist_row(engine, "CCC", "breakout", "ready", today)

    earnings = read_ready_entries(url, "ep_earnings", today)
    news = read_ready_entries(url, "ep_news", today)
    assert {e["ticker"] for e in earnings} == {"AAA"}
    assert {e["ticker"] for e in news} == {"BBB"}


def test_read_ready_entries_includes_prior_dates(tmp_alpaca_db):
    """C candidates from the previous trading day's scan are promoted to ready
    by today's day-2-confirm job; their scan_date is prev-trading-day but
    stage=ready today. The filter must accept C variant rows whose scan_date
    matches the previous TRADING day (handles weekends/holidays), and A
    variant rows whose scan_date is today."""
    from executor.watchlist_source import read_ready_entries
    from core.trading_calendar import previous_trading_day

    url, engine = tmp_alpaca_db
    today = _today_et()
    prev_trading = previous_trading_day(today)
    _seed_watchlist_row(engine, "OLD", "ep_earnings", "ready", prev_trading, ep_strategy="C")
    _seed_watchlist_row(engine, "NEW", "ep_earnings", "ready", today, ep_strategy="A")

    entries = read_ready_entries(url, "ep_earnings", today)
    assert {e["ticker"] for e in entries} == {"OLD", "NEW"}


def test_read_ready_entries_rejects_stale_rows(tmp_alpaca_db):
    """Regression for the 2026-04-30 incident: ``triggered`` rows that
    Alpaca processed days ago must NOT be returned. Without this filter
    the IB bot picks up week-old rows on every cron tick and places fresh
    orders at stale entry prices, because IB's local-DB idempotency cannot
    detect rows it never saw the first time around."""
    from executor.watchlist_source import read_ready_entries
    from core.trading_calendar import previous_trading_day

    url, engine = tmp_alpaca_db
    today = _today_et()
    prev_trading = previous_trading_day(today)
    # In window: today (A) and prev trading day (C confirmed today).
    _seed_watchlist_row(engine, "FRESH_A", "ep_earnings", "ready", today, ep_strategy="A")
    _seed_watchlist_row(engine, "FRESH_C", "ep_earnings", "triggered", prev_trading, ep_strategy="C")
    # Out of window: 5+ days old, regardless of stage.
    _seed_watchlist_row(engine, "STALE5", "ep_earnings", "ready", today - timedelta(days=5), ep_strategy="A")
    _seed_watchlist_row(engine, "STALE7", "ep_earnings", "triggered", today - timedelta(days=7), ep_strategy="A")
    _seed_watchlist_row(engine, "STALE30", "ep_earnings", "triggered", today - timedelta(days=30), ep_strategy="B")

    entries = read_ready_entries(url, "ep_earnings", today)
    tickers = {e["ticker"] for e in entries}
    assert tickers == {"FRESH_A", "FRESH_C"}, (
        f"stale rows leaked through filter: got {tickers}"
    )


def test_read_ready_entries_max_age_days_is_sql_prefilter_only(tmp_alpaca_db):
    """``max_age_days`` is the SQL pre-filter window — it controls which rows
    are loaded from disk. The AUTHORITATIVE filter is the per-variant
    scan_date check (A/B == today; C == previous trading day). So even with
    ``max_age_days=30`` an A row scanned days ago must NOT be returned —
    only ``max_age_days=0`` proves we exclude on SQL alone, and only A/B
    rows on today + C rows on previous trading day are admitted regardless
    of how large the window is.
    """
    from executor.watchlist_source import read_ready_entries
    from core.trading_calendar import previous_trading_day

    url, engine = tmp_alpaca_db
    today = _today_et()
    prev_trading = previous_trading_day(today)
    _seed_watchlist_row(engine, "T0_TODAY_A", "ep_earnings", "ready", today, ep_strategy="A")
    _seed_watchlist_row(engine, "T1_PREV_C", "ep_earnings", "ready", prev_trading, ep_strategy="C")
    # Stale A scanned 2 days ago — invalid for A even with max_age_days=30.
    _seed_watchlist_row(engine, "T2_STALE_A", "ep_earnings", "ready", today - timedelta(days=2), ep_strategy="A")

    # max_age_days=0 → only today's rows reach the per-variant filter; prev_trading is excluded by SQL.
    only_today = read_ready_entries(url, "ep_earnings", today, max_age_days=0)
    assert {e["ticker"] for e in only_today} == {"T0_TODAY_A"}

    # Wide window → SQL admits all 3, but per-variant rejects T2_STALE_A.
    wide = read_ready_entries(url, "ep_earnings", today, max_age_days=30)
    assert {e["ticker"] for e in wide} == {"T0_TODAY_A", "T1_PREV_C"}, (
        "per-variant filter must reject A rows whose scan_date != today, "
        "regardless of the SQL pre-filter window"
    )


def test_read_ready_entries_skips_rows_without_ep_strategy(tmp_alpaca_db, caplog):
    from executor.watchlist_source import read_ready_entries

    url, engine = tmp_alpaca_db
    today = _today_et()
    # Insert a malformed row by writing meta without ep_strategy
    with get_session(engine) as session:
        wl = Watchlist(
            ticker="MAL",
            setup_type="ep_earnings",
            stage="ready",
            scan_date=today,
            metadata_json=json.dumps({"gap_pct": 10.0}),  # no ep_strategy
        )
        session.add(wl)
        session.commit()

    entries = read_ready_entries(url, "ep_earnings", today)
    assert entries == [], "malformed row must be filtered out"


# ---------------------------------------------------------------------------
# Plugin job_execute uses the cross-DB reader when configured
# ---------------------------------------------------------------------------


def test_ep_earnings_job_execute_reads_from_source_db(
    tmp_alpaca_db, ib_local_engine, mock_ib_client,
):
    """When config has watchlist_source_db_url set, job_execute must read
    from the Alpaca DB, not the IB local DB. Seed the Alpaca DB with a
    row and leave the IB DB empty — job_execute should still find the entry."""
    url, alpaca_engine = tmp_alpaca_db
    today = _today_et()
    _seed_watchlist_row(alpaca_engine, "AAA", "ep_earnings", "ready", today, ep_strategy="A")

    config = {
        "watchlist_source_db_url": url,
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_positions": 4,
            "max_position_pct": 15.0,
            "daily_loss_limit_pct": 3.0,
            "weekly_loss_limit_pct": 5.0,
        },
    }

    with patch("core.execution.is_trading_day", return_value=True), \
         patch("strategies.ep_earnings.plugin.EPEarningsPlugin._persist_entry"), \
         patch("core.execution._execute_entry") as mock_exec, \
         patch("core.execution.resolve_execution_price",
               return_value=(22.0, 20.5, "scan-mid")):
        plugin = EPEarningsPlugin()
        result = plugin.job_execute(config, mock_ib_client, ib_local_engine, lambda m: None)

    assert mock_exec.called, "job_execute should have called _execute_entry"
    args, kwargs = mock_exec.call_args
    assert args[0] == "AAA", "ticker mismatch"
    assert "1/1 entered" in result


def test_ep_news_job_execute_reads_from_source_db(
    tmp_alpaca_db, ib_local_engine, mock_ib_client,
):
    """Mirror test for ep_news plugin."""
    url, alpaca_engine = tmp_alpaca_db
    today = _today_et()
    _seed_watchlist_row(alpaca_engine, "BBB", "ep_news", "ready", today, ep_strategy="A")

    config = {
        "watchlist_source_db_url": url,
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_positions": 4,
            "max_position_pct": 15.0,
            "daily_loss_limit_pct": 3.0,
            "weekly_loss_limit_pct": 5.0,
        },
    }

    with patch("core.execution.is_trading_day", return_value=True), \
         patch("core.execution._execute_entry") as mock_exec, \
         patch("core.execution.resolve_execution_price",
               return_value=(22.0, 20.5, "scan-mid")):
        plugin = EPNewsPlugin()
        result = plugin.job_execute(config, mock_ib_client, ib_local_engine, lambda m: None)

    assert mock_exec.called
    args, kwargs = mock_exec.call_args
    assert args[0] == "BBB"
    assert "1/1 entered" in result


def test_job_execute_without_source_db_falls_back_to_local(
    ib_local_engine, mock_ib_client,
):
    """Without watchlist_source_db_url, both plugins fall back to reading
    from the local db_engine — preserving existing Alpaca-bot behavior."""
    today = _today_et()
    # Seed the LOCAL engine, not a source DB
    _seed_watchlist_row(ib_local_engine, "LCL", "ep_earnings", "ready", today, ep_strategy="A")

    config = {
        # No watchlist_source_db_url → fall back to local
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_positions": 4,
            "max_position_pct": 15.0,
            "daily_loss_limit_pct": 3.0,
            "weekly_loss_limit_pct": 5.0,
        },
    }

    with patch("core.execution.is_trading_day", return_value=True), \
         patch("strategies.ep_earnings.plugin.EPEarningsPlugin._persist_entry"), \
         patch("core.execution._execute_entry") as mock_exec, \
         patch("core.execution.resolve_execution_price",
               return_value=(22.0, 20.5, "scan-mid")):
        plugin = EPEarningsPlugin()
        plugin.job_execute(config, mock_ib_client, ib_local_engine, lambda m: None)

    assert mock_exec.called
    args, _ = mock_exec.call_args
    assert args[0] == "LCL"


def test_ib_idempotency_uses_local_db_not_source(
    tmp_alpaca_db, ib_local_engine, mock_ib_client,
):
    """The cross-DB read must not extend to the idempotency check — IB's
    'have I already executed this today?' check has to query the IB DB
    (where IB's own Order/Position rows live), not the Alpaca DB. Seed an
    open Position with the same setup_type in the IB DB and confirm the
    plugin skips the entry even though the Alpaca row says ready."""
    from db.models import Position

    url, alpaca_engine = tmp_alpaca_db
    today = _today_et()
    _seed_watchlist_row(alpaca_engine, "DUP", "ep_earnings", "ready", today, ep_strategy="A")

    # Pre-seed an open IB Position to trip the idempotency guard
    with get_session(ib_local_engine) as session:
        pos = Position(
            ticker="DUP",
            setup_type="ep_earnings_a",
            side="long",
            shares=100,
            entry_price=22.0,
            stop_price=20.5,
            initial_stop_price=20.5,
            opened_at=datetime.utcnow(),
            is_open=True,
        )
        session.add(pos)
        session.commit()

    config = {
        "watchlist_source_db_url": url,
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_positions": 4,
            "max_position_pct": 15.0,
            "daily_loss_limit_pct": 3.0,
            "weekly_loss_limit_pct": 5.0,
        },
    }

    with patch("core.execution.is_trading_day", return_value=True), \
         patch("core.execution._execute_entry") as mock_exec, \
         patch("core.execution.resolve_execution_price",
               return_value=(22.0, 20.5, "scan-mid")):
        plugin = EPEarningsPlugin()
        result = plugin.job_execute(config, mock_ib_client, ib_local_engine, lambda m: None)

    assert not mock_exec.called, (
        "Idempotency guard must skip when the IB-local Position already exists, "
        "even though the Alpaca-source watchlist still says ready."
    )
    assert "0/1 entered" in result


# ---------------------------------------------------------------------------
# Scheduler skip_jobs param
# ---------------------------------------------------------------------------


def test_register_strategy_jobs_skips_named_jobs():
    """skip_jobs must filter out the named entries while leaving the rest."""
    from core.scheduler import register_strategy_jobs

    scheduler = MagicMock()
    plugin = EPEarningsPlugin()
    plugins = {"ep_earnings": plugin}

    register_strategy_jobs(
        scheduler, plugins, config={}, client=None, db_engine=None,
        notify=lambda m: None,
        skip_jobs=("ep_earnings_scan", "ep_earnings_day2_confirm"),
    )

    registered_ids = [
        kw.get("id") for _, kw in scheduler.add_job.call_args_list
    ]
    assert "ep_earnings_scan" not in registered_ids
    assert "ep_earnings_day2_confirm" not in registered_ids
    assert "ep_earnings_execute" in registered_ids


def test_register_strategy_jobs_default_registers_everything():
    """Default skip_jobs=() preserves Alpaca-bot behavior (all jobs registered)."""
    from core.scheduler import register_strategy_jobs

    scheduler = MagicMock()
    plugin = EPEarningsPlugin()
    plugins = {"ep_earnings": plugin}

    register_strategy_jobs(
        scheduler, plugins, config={}, client=None, db_engine=None,
        notify=lambda m: None,
    )

    registered_ids = {
        kw.get("id") for _, kw in scheduler.add_job.call_args_list
    }
    expected = {entry.job_id for entry in plugin.schedule}
    assert registered_ids == expected
