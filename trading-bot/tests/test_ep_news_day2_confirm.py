"""
Crash-safety tests for EP News Strategy A/B/C execution.

Mirror of `test_ep_earnings_day2_confirm.py` for the EP news plugin. Locks in
that `job_execute` reads ready rows from the database, not from in-memory
state, so a bot restart between scan/confirm and execute does not drop trades.
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
from strategies.ep_news.plugin import EPNewsPlugin

ET = pytz.timezone("America/New_York")


def _today_et():
    return datetime.now(ET).date()


@pytest.fixture
def db_engine():
    engine = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.place_limit_order.return_value = "broker-id-news"
    client.get_portfolio_value.return_value = 100_000.0
    return client


@pytest.fixture
def patch_main(db_engine):
    import main as main_mod
    main_mod._db_engine = db_engine
    with patch("main.is_trading_day", return_value=True), \
         patch("main._compute_current_daily_pnl", return_value=0.0), \
         patch("main._compute_current_weekly_pnl", return_value=0.0), \
         patch("main._await_fill_and_setup_stop"), \
         patch.object(RiskManager, "check_trading_window", return_value=True):
        yield


def _config():
    return {
        "signals": {
            "ep_news_stop_loss_pct": 7.0,
            "ep_news_max_hold_days": 50,
            "ep_news_c_stop_loss_pct": 7.0,
            "ep_news_c_max_hold_days": 20,
        },
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 15.0,
            "max_positions": 4,
            "daily_loss_limit_pct": 5.0,
            "weekly_loss_limit_pct": 10.0,
        },
    }


def _seed_pending_c(db_engine, ticker="SDCO", scan_date=None, gap_day_close=15.0):
    if scan_date is None:
        scan_date = _today_et() - timedelta(days=1)
    meta = {
        "ep_strategy": "C",
        "day2_confirm": True,
        "gap_day_close": gap_day_close,
        "gap_pct": 14.0,
        "stop_loss_pct": 7.0,
        "max_hold_days": 20,
        "chg_open_pct": -0.5,
        "close_in_range": 35,
        "downside_from_open": 3.0,
        "prev_10d_change_pct": -18.0,
        "atr_pct": 4.5,
        "open_price": 14.5,
        "prev_close": 13.0,
        "prev_high": 14.0,
        "market_cap": 1_200_000_000,
        "rvol": 3.0,
    }
    with get_session(db_engine) as session:
        wl = Watchlist(
            ticker=ticker, setup_type="ep_news", stage="watching",
            scan_date=scan_date,
            metadata_json=json.dumps(meta),
            notes="EP News Strategy C — pending day-2 confirm",
        )
        session.add(wl)
        session.commit()
        return wl.id


def _seed_ready(db_engine, ticker="AU", ep_strategy="C", scan_date=None,
                entry_price=22.0, stop_price=20.46, today_volume=5_000_000):
    if scan_date is None:
        scan_date = _today_et() - timedelta(days=1)
    meta = {
        "ep_strategy": ep_strategy,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "stop_loss_pct": 7.0,
        "max_hold_days": 20 if ep_strategy == "C" else 50,
        "gap_pct": 11.0,
        "chg_open_pct": 1.0,
        "close_in_range": 60,
        "downside_from_open": 1.0,
        "prev_10d_change_pct": -12.0,
        "atr_pct": 3.5,
        "rvol": 2.0,
        "today_volume": today_volume,
    }
    with get_session(db_engine) as session:
        wl = Watchlist(
            ticker=ticker, setup_type="ep_news", stage="ready",
            scan_date=scan_date,
            metadata_json=json.dumps(meta),
        )
        session.add(wl)
        session.commit()
        return wl.id


def _snap(price):
    return SimpleNamespace(latest_trade=SimpleNamespace(price=price), minute_bar=None)


# ---------------------------------------------------------------------------
# Day-2 confirm
# ---------------------------------------------------------------------------

class TestDay2Confirm:
    def test_promotes_to_ready_when_price_above_gap_close(self, db_engine, mock_client, patch_main):
        plugin = EPNewsPlugin()
        _seed_pending_c(db_engine, ticker="SDCO", gap_day_close=15.0)
        mock_client.get_snapshots.return_value = {"SDCO": _snap(16.5)}

        plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=lambda m: None)

        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="SDCO").first()
            assert wl.stage == "ready"
            meta = wl.meta
            assert meta["ep_strategy"] == "C"
            assert meta["entry_price"] == 16.5
            assert meta["stop_price"] == round(16.5 * (1 - 7.0 / 100), 2)
            assert meta["day1_return_pct"] == 10.0

    def test_expires_when_price_below_gap_close(self, db_engine, mock_client, patch_main):
        plugin = EPNewsPlugin()
        _seed_pending_c(db_engine, ticker="SDCO", gap_day_close=15.0)
        mock_client.get_snapshots.return_value = {"SDCO": _snap(14.0)}

        plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=lambda m: None)

        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="SDCO").first()
            assert wl.stage == "expired"
            assert "entry_price" not in wl.meta

    def test_total_snapshot_failure_raises_and_expires(self, db_engine, mock_client, patch_main):
        """Every ticker fails → raise so _track_job alerts on Telegram. Stage is also
        flipped to expired so a stale 'watching' row doesn't get retried in a wrong
        day-2 window tomorrow."""
        plugin = EPNewsPlugin()
        _seed_pending_c(db_engine, ticker="SDCO")
        mock_client.get_snapshots.side_effect = RuntimeError("API down")
        sent: list[str] = []

        with pytest.raises(RuntimeError, match="DAY-2 CONFIRM"):
            plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=sent.append)

        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="SDCO").first()
            assert wl.stage == "expired"
        assert any("SDCO" in m and "snapshot error" in m for m in sent), sent

    def test_partial_failure_notifies_but_continues(self, db_engine, mock_client, patch_main):
        """One ticker fails, another succeeds → no raise (not a systemic outage),
        but the failure is surfaced via notify. CLAUDE.md: never silent."""
        plugin = EPNewsPlugin()
        _seed_pending_c(db_engine, ticker="SDCO", gap_day_close=15.0)
        _seed_pending_c(db_engine, ticker="GOOD", gap_day_close=10.0)
        call_results = {"SDCO": RuntimeError("transient"), "GOOD": {"GOOD": _snap(11.0)}}

        def _snap_side_effect(tickers):
            result = call_results[tickers[0]]
            if isinstance(result, Exception):
                raise result
            return result

        mock_client.get_snapshots.side_effect = _snap_side_effect
        sent: list[str] = []

        plugin.job_day2_confirm(_config(), mock_client, db_engine, notify=sent.append)

        with get_session(db_engine) as session:
            sdco = session.query(Watchlist).filter_by(ticker="SDCO").first()
            good = session.query(Watchlist).filter_by(ticker="GOOD").first()
            assert sdco.stage == "expired"
            assert good.stage == "ready"
        assert any("SDCO" in m for m in sent), sent


# ---------------------------------------------------------------------------
# DB-driven execute
# ---------------------------------------------------------------------------

class TestExecuteIsDBDriven:
    def test_execute_reads_ready_rows_from_db_not_memory(self, db_engine, mock_client, patch_main):
        _seed_ready(db_engine, ticker="AU", ep_strategy="C", entry_price=22.0)
        EPNewsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

        mock_client.place_limit_order.assert_called_once()
        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(ticker="AU").first()
            assert wl.stage == "triggered"
            sig = session.query(Signal).filter_by(ticker="AU").first()
            assert sig.setup_type == "ep_news_c"

    def test_execute_skips_when_open_position_exists(self, db_engine, mock_client, patch_main):
        _seed_ready(db_engine, ticker="AU", ep_strategy="C")
        with get_session(db_engine) as session:
            session.add(Position(
                ticker="AU", setup_type="ep_news_c", side="long",
                shares=100, entry_price=22.0,
                stop_price=20.46, initial_stop_price=20.46,
                opened_at=datetime.utcnow(), is_open=True,
            ))
            session.commit()

        EPNewsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)
        mock_client.place_limit_order.assert_not_called()

    def test_execute_no_ready_rows_returns_early(self, db_engine, mock_client, patch_main):
        result = EPNewsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)
        assert "No entries" in (result or "")
        mock_client.place_limit_order.assert_not_called()

    def test_replay_does_not_double_submit(self, db_engine, mock_client, patch_main):
        """Guard against `job_execute` running twice before the first run's
        `mark_triggered` lands. The first run places the order and writes an Order
        row; if the row stays `ready` (e.g. mark_triggered failed or we crashed
        between the two writes), the second run must detect the recent Order and
        skip rather than double-submit."""
        from db.models import Order
        _seed_ready(db_engine, ticker="AU", ep_strategy="C", entry_price=22.0)

        # Simulate prior run: Order already submitted; Watchlist not yet flipped
        # to `triggered` (the exact window we're protecting against).
        with get_session(db_engine) as session:
            session.add(Order(
                ticker="AU", side="buy", order_type="limit", qty=100, price=22.0,
                status="submitted", broker_order_id="prior-run-id",
                created_at=datetime.utcnow(),
            ))
            session.commit()

        EPNewsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

        mock_client.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end regression
# ---------------------------------------------------------------------------

def test_full_lifecycle_survives_restart_between_confirm_and_execute(
    db_engine, mock_client, patch_main,
):
    plugin1 = EPNewsPlugin()
    plugin1._persist_pending_day2(
        {
            "ticker": "STAA",
            "ep_strategy": "C",
            "day2_confirm": True,
            "gap_day_close": 30.0,
            "gap_pct": 14.0,
            "stop_loss_pct": 7.0,
            "max_hold_days": 20,
            "chg_open_pct": -0.5,
            "close_in_range": 35,
            "downside_from_open": 3.0,
            "prev_10d_change_pct": -18.0,
            "atr_pct": 4.5,
            "open_price": 29.5,
            "prev_close": 28.0,
            "prev_high": 29.0,
            "market_cap": 1_500_000_000,
            "rvol": 2.5,
        },
        scan_date=_today_et() - timedelta(days=1),
        db_engine=db_engine,
    )

    mock_client.get_snapshots.return_value = {"STAA": _snap(33.0)}
    plugin1.job_day2_confirm(_config(), mock_client, db_engine, notify=lambda m: None)

    with get_session(db_engine) as session:
        assert session.query(Watchlist).filter_by(ticker="STAA").first().stage == "ready"

    # Restart simulation: discard plugin1, build plugin2
    del plugin1
    EPNewsPlugin().job_execute(_config(), mock_client, db_engine, notify=lambda m: None)

    mock_client.place_limit_order.assert_called_once()
    with get_session(db_engine) as session:
        wl = session.query(Watchlist).filter_by(ticker="STAA").first()
        assert wl.stage == "triggered"
        assert session.query(Signal).filter_by(ticker="STAA").first().setup_type == "ep_news_c"
