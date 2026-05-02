"""
Crash-safety tests for EP Earnings Strategy A/B/C execution.

These lock in the contract that `job_execute` reads from the database, not from
in-memory state. A bot restart between scan/confirm and execute must not drop
trades. `test_full_lifecycle_survives_restart_between_confirm_and_execute` is
the regression guard for the April 9, 2026 Strategy C silent-drop incident.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import pytz

from db.models import Base, Watchlist, Signal, Position, get_engine, get_session
from risk.manager import RiskManager
from strategies.ep_earnings.plugin import EPEarningsPlugin

ET = pytz.timezone("America/New_York")


def _today_et():
    """Match the plugin's notion of 'today' so tests are timezone-stable."""
    return datetime.now(ET).date()


@pytest.fixture
def db_engine():
    engine = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.place_oto_order.return_value = "broker-id-123"
    client.get_portfolio_value.return_value = 100_000.0
    # resolve_execution_price (added 2026-04-22) fetches a live quote before
    # placing each order. Default stub returns a quote whose mid sits at or just
    # below the default seeded entry ($22.0) so the helper falls back to the
    # scan entry — keeps these tests focused on execute semantics, not price
    # refresh. Override per-test when testing the refresh path explicitly.
    client.get_realtime_quote.side_effect = lambda t: {
        "ticker": t, "bid": 21.90, "ask": 22.00, "last_price": 21.95,
    }
    return client


@pytest.fixture
def patch_main(db_engine):
    """Patch main module helpers so _execute_entry can run without a broker or threads."""
    import main as main_mod
    main_mod._db_engine = db_engine
    with patch("core.execution.is_trading_day", return_value=True), \
         patch("core.execution._compute_current_daily_pnl", return_value=0.0), \
         patch("core.execution._compute_current_weekly_pnl", return_value=0.0), \
         patch("core.execution._await_fill_and_setup_stop"), \
         patch.object(RiskManager, "check_trading_window", return_value=True):
        yield


def _config():
    return {
        "signals": {
            "ep_earnings_stop_loss_pct": 7.0,
            "ep_earnings_max_hold_days": 50,
            "ep_earnings_c_stop_loss_pct": 7.0,
            "ep_earnings_c_max_hold_days": 20,
            "ep_earnings_a_min_close_in_range": 50.0,
            "ep_earnings_a_max_downside_from_open": 3.0,
            "ep_earnings_a_prev_10d_min": -30.0,
            "ep_earnings_a_prev_10d_max": -10.0,
            "ep_earnings_b_min_close_in_range": 50.0,
            "ep_earnings_b_atr_pct_min": 2.0,
            "ep_earnings_b_atr_pct_max": 5.0,
            "ep_earnings_b_prev_10d_max": -10.0,
            "ep_earnings_c_prev_10d_max": -10.0,
        },
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 15.0,
            "max_positions": 4,
            "daily_loss_limit_pct": 5.0,
            "weekly_loss_limit_pct": 10.0,
        },
    }


def _seed_pending_c(db_engine, ticker="LEVI", scan_date=None, gap_day_close=20.0):
    """Seed a stage='watching' Strategy C row (what job_scan would have written)."""
    if scan_date is None:
        scan_date = _today_et() - timedelta(days=1)
    meta = {
        "ep_strategy": "C",
        "day2_confirm": True,
        "gap_day_close": gap_day_close,
        "gap_pct": 12.0,
        "stop_loss_pct": 7.0,
        "max_hold_days": 20,
        "chg_open_pct": -1.0,
        "close_in_range": 30,
        "downside_from_open": 4.0,
        "prev_10d_change_pct": -15.0,
        "atr_pct": 4.0,
        "open_price": 19.5,
        "prev_close": 18.0,
        "prev_high": 19.0,
        "market_cap": 2_000_000_000,
        "rvol": 2.0,
    }
    with get_session(db_engine) as session:
        wl = Watchlist(
            ticker=ticker,
            setup_type="ep_earnings",
            stage="watching",
            scan_date=scan_date,
            metadata_json=json.dumps(meta),
            notes="EP Earnings Strategy C — pending day-2 confirm",
        )
        session.add(wl)
        session.commit()
        return wl.id


def _seed_ready(db_engine, ticker="AU", ep_strategy="C", scan_date=None,
                entry_price=22.0, stop_price=20.46):
    """Seed a stage='ready' row directly — bypasses scan/confirm to test execute alone."""
    if scan_date is None:
        scan_date = _today_et() - timedelta(days=1)
    meta = {
        "ep_strategy": ep_strategy,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "stop_loss_pct": 7.0,
        "max_hold_days": 20 if ep_strategy == "C" else 50,
        "gap_pct": 10.0,
        "chg_open_pct": 1.0,
        "close_in_range": 60,
        "downside_from_open": 1.0,
        "prev_10d_change_pct": -12.0,
        "atr_pct": 3.5,
        "rvol": 2.0,
    }
    with get_session(db_engine) as session:
        wl = Watchlist(
            ticker=ticker, setup_type="ep_earnings", stage="ready",
            scan_date=scan_date,
            metadata_json=json.dumps(meta),
        )
        session.add(wl)
        session.commit()
        return wl.id


def _snap(price):
    # executor/alpaca_client.py::get_snapshots returns a flat dict per ticker
    # (not the raw Alpaca SDK object). See test_fetch_current_price.py for the
    # full key set; we only need latest_price for day-2 confirm.
    return {"latest_price": price}


# ---------------------------------------------------------------------------
# Day-2 confirm tests
# ---------------------------------------------------------------------------

class TestDay2Confirm:
    def test_promotes_to_ready_when_price_above_gap_close(self, db_engine, mock_client, patch_main):
        plugin = EPEarningsPlugin()
        _seed_pending_c(db_engine, ticker="LEVI", gap_day_close=20.0)
        mock_client.get_snapshots.return_value = {"LEVI": _snap(21.5)}

        plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=lambda m: None)

        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="LEVI").first()
            assert wl.stage == "ready"
            meta = wl.meta
            assert meta["ep_strategy"] == "C"
            assert meta["entry_price"] == 21.5
            # match plugin's stop calc: round(price * (1 - stop_pct/100), 2)
            assert meta["stop_price"] == round(21.5 * (1 - 7.0 / 100), 2)
            assert meta["day1_return_pct"] == 7.5

    def test_expires_when_price_below_gap_close(self, db_engine, mock_client, patch_main):
        plugin = EPEarningsPlugin()
        _seed_pending_c(db_engine, ticker="LEVI", gap_day_close=20.0)
        mock_client.get_snapshots.return_value = {"LEVI": _snap(19.5)}

        plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=lambda m: None)

        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="LEVI").first()
            assert wl.stage == "expired"
            assert "entry_price" not in wl.meta

    def test_total_snapshot_failure_raises_and_expires(self, db_engine, mock_client, patch_main):
        """Every ticker fails → raise so _track_job alerts on Telegram. Stage is also
        flipped to expired so a stale 'watching' row doesn't get retried in a wrong
        day-2 window tomorrow."""
        plugin = EPEarningsPlugin()
        _seed_pending_c(db_engine, ticker="LEVI", gap_day_close=20.0)
        mock_client.get_snapshots.side_effect = RuntimeError("API down")
        sent: list[str] = []

        with pytest.raises(RuntimeError, match="DAY-2 CONFIRM"):
            plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=sent.append)

        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="LEVI").first()
            assert wl.stage == "expired"
        assert any("LEVI" in m and "snapshot error" in m for m in sent), sent

    def test_partial_failure_notifies_but_continues(self, db_engine, mock_client, patch_main):
        """One ticker fails, another succeeds → no raise (not a systemic outage),
        but the failure is surfaced via notify. CLAUDE.md: never silent."""
        plugin = EPEarningsPlugin()
        _seed_pending_c(db_engine, ticker="LEVI", gap_day_close=20.0)
        _seed_pending_c(db_engine, ticker="GOOD", gap_day_close=10.0)
        call_results = {"LEVI": RuntimeError("transient"), "GOOD": {"GOOD": _snap(11.0)}}

        def _snap_side_effect(tickers):
            result = call_results[tickers[0]]
            if isinstance(result, Exception):
                raise result
            return result

        mock_client.get_snapshots.side_effect = _snap_side_effect
        sent: list[str] = []

        plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=sent.append)

        with get_session(db_engine) as session:
            levi = session.query(Watchlist).filter_by(ticker="LEVI").first()
            good = session.query(Watchlist).filter_by(ticker="GOOD").first()
            assert levi.stage == "expired"
            assert good.stage == "ready"
        assert any("LEVI" in m for m in sent), sent

    def test_skips_today_pending(self, db_engine, mock_client, patch_main):
        """A Strategy C row scanned today is not eligible for day-2 confirm until tomorrow."""
        plugin = EPEarningsPlugin()
        _seed_pending_c(db_engine, ticker="LEVI", scan_date=_today_et(), gap_day_close=20.0)

        result = plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=lambda m: None)

        mock_client.get_snapshots.assert_not_called()
        assert "No pending" in (result or "")

    def test_no_pending_returns_early(self, db_engine, mock_client, patch_main):
        plugin = EPEarningsPlugin()
        result = plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=lambda m: None)
        assert "No pending" in (result or "")
        mock_client.get_snapshots.assert_not_called()


# ---------------------------------------------------------------------------
# DB-driven execute tests (these are the regression guards)
# ---------------------------------------------------------------------------

class TestExecuteIsDBDriven:
    def test_execute_reads_ready_rows_from_db_not_memory(self, db_engine, mock_client, patch_main):
        """A fresh plugin instance with no in-memory state must still execute pending DB rows.

        Locks in the fix for the April 9, 2026 Strategy C silent drop.
        """
        _seed_ready(db_engine, ticker="AU", ep_strategy="C", entry_price=22.0, stop_price=20.46)
        plugin = EPEarningsPlugin()  # fresh instance — no memory of prior scan/confirm

        plugin.job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

        mock_client.place_oto_order.assert_called_once()
        args = mock_client.place_oto_order.call_args.args
        assert args[0] == "AU"
        assert args[1] == "buy"
        assert args[3] == 22.0  # entry price from meta

        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="AU").first()
            assert wl.stage == "triggered"
            sig = session.query(Signal).filter_by(ticker="AU").first()
            assert sig is not None
            assert sig.setup_type == "ep_earnings_c"

    def test_execute_skips_when_open_position_exists(self, db_engine, mock_client, patch_main):
        _seed_ready(db_engine, ticker="AU", ep_strategy="C")
        with get_session(db_engine) as session:
            session.add(Position(
                ticker="AU", setup_type="ep_earnings_c", side="long",
                shares=100, entry_price=22.0,
                stop_price=20.46, initial_stop_price=20.46,
                opened_at=datetime.utcnow(), is_open=True,
            ))
            session.commit()

        EPEarningsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)
        mock_client.place_oto_order.assert_not_called()

    def test_execute_handles_both_today_ab_and_yesterday_c_in_one_pass(
        self, db_engine, mock_client, patch_main,
    ):
        _seed_ready(db_engine, ticker="AB1", ep_strategy="A", scan_date=_today_et())
        _seed_ready(db_engine, ticker="C1", ep_strategy="C",
                    scan_date=_today_et() - timedelta(days=1))

        EPEarningsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

        assert mock_client.place_oto_order.call_count == 2
        ordered = {c.args[0] for c in mock_client.place_oto_order.call_args_list}
        assert ordered == {"AB1", "C1"}

    def test_execute_no_ready_rows_returns_early(self, db_engine, mock_client, patch_main):
        result = EPEarningsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)
        assert "No entries" in (result or "")
        mock_client.place_oto_order.assert_not_called()

    def test_replay_does_not_double_submit(self, db_engine, mock_client, patch_main):
        """Guard against `job_execute` running twice before the first run's
        `mark_triggered` lands. The first run places the order and writes an Order
        row; if the row stays `ready` (e.g. mark_triggered failed or we crashed
        between the two writes), the second run must detect the recent Order and
        skip rather than double-submit."""
        from db.models import Order
        _seed_ready(db_engine, ticker="AU", ep_strategy="A", scan_date=_today_et())

        # Simulate prior run: Order already submitted; Watchlist not yet flipped
        # to `triggered` (the exact window we're protecting against).
        with get_session(db_engine) as session:
            session.add(Order(
                ticker="AU", side="buy", order_type="limit", qty=100, price=22.0,
                status="submitted", broker_order_id="prior-run-id",
                created_at=datetime.utcnow(),
            ))
            session.commit()

        EPEarningsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

        mock_client.place_oto_order.assert_not_called()

    def test_execute_marks_row_triggered_after_order(self, db_engine, mock_client, patch_main):
        _seed_ready(db_engine, ticker="AU", ep_strategy="A", scan_date=_today_et())

        EPEarningsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="AU").first()
            assert wl.stage == "triggered"

    def test_execute_groups_ab_for_same_ticker(self, db_engine, mock_client, patch_main):
        """If a ticker passes both A and B, only one order is placed."""
        _seed_ready(db_engine, ticker="DUO", ep_strategy="A", scan_date=_today_et())
        _seed_ready(db_engine, ticker="DUO", ep_strategy="B", scan_date=_today_et())

        EPEarningsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

        assert mock_client.place_oto_order.call_count == 1


# ---------------------------------------------------------------------------
# End-to-end regression test
# ---------------------------------------------------------------------------

def test_full_lifecycle_survives_restart_between_confirm_and_execute(
    db_engine, mock_client, patch_main,
):
    """End-to-end: persist pending → confirm → simulate restart → execute.

    This is the test that would have caught April 9's silent drop. The plugin
    instance is intentionally discarded between confirm and execute to prove
    that no state survives in memory — everything must come from the DB.
    """
    plugin1 = EPEarningsPlugin()
    plugin1._persist_pending_day2(
        {
            "ticker": "STAA",
            "ep_strategy": "C",
            "day2_confirm": True,
            "gap_day_close": 30.0,
            "gap_pct": 12.0,
            "stop_loss_pct": 7.0,
            "max_hold_days": 20,
            "chg_open_pct": -1.0,
            "close_in_range": 30,
            "downside_from_open": 4.0,
            "prev_10d_change_pct": -15.0,
            "atr_pct": 4.0,
            "open_price": 29.5,
            "prev_close": 28.0,
            "prev_high": 29.0,
            "market_cap": 1_500_000_000,
            "rvol": 2.0,
        },
        scan_date=_today_et() - timedelta(days=1),
        db_engine=db_engine,
    )

    mock_client.get_snapshots.return_value = {"STAA": _snap(33.0)}
    plugin1.job_day2_confirm(_config(), mock_client, db_engine, notify=lambda m: None)

    with get_session(db_engine) as session:
        wl = session.query(Watchlist).filter_by(ticker="STAA").first()
        assert wl.stage == "ready"

    # SIMULATE BOT RESTART — discard plugin1, build a fresh plugin2
    del plugin1
    plugin2 = EPEarningsPlugin()
    plugin2.job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

    mock_client.place_oto_order.assert_called_once()
    with get_session(db_engine) as session:
        wl = session.query(Watchlist).filter_by(ticker="STAA").first()
        assert wl.stage == "triggered"
        sig = session.query(Signal).filter_by(ticker="STAA").first()
        assert sig is not None
        assert sig.setup_type == "ep_earnings_c"
