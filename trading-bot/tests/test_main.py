"""
Unit tests for main module helpers.
"""

import time
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from main import (
    _format_watchlist_notification,
    _wait_for_fill,
    _clear_daily_caches,
    _prefetch_daily_bars,
    job_reconcile_positions,
)


# ---------------------------------------------------------------------------
# _format_watchlist_notification
# ---------------------------------------------------------------------------

class TestFormatWatchlistNotification:
    def test_empty_watchlist(self):
        result = _format_watchlist_notification([])
        assert result == "WATCHLIST READY: 0 candidates"

    def test_single_ep_candidate(self):
        watchlist = [
            {"ticker": "NVDA", "setup_type": "episodic_pivot", "gap_pct": 15.2},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "WATCHLIST READY: 1 candidates" in result
        assert "EP: NVDA (+15.2%)" in result

    def test_single_breakout_candidate(self):
        watchlist = [
            {"ticker": "MSFT", "setup_type": "breakout", "atr_ratio": 0.75},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "WATCHLIST READY: 1 candidates" in result
        assert "Breakout: MSFT" in result

    def test_mixed_setup_types(self):
        watchlist = [
            {"ticker": "NVDA", "setup_type": "episodic_pivot", "gap_pct": 15.2},
            {"ticker": "AAPL", "setup_type": "episodic_pivot", "gap_pct": 12.1},
            {"ticker": "MSFT", "setup_type": "breakout", "atr_ratio": 0.75},
            {"ticker": "TSLA", "setup_type": "breakout", "atr_ratio": 0.80},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "WATCHLIST READY: 4 candidates" in result
        assert "EP: NVDA (+15.2%), AAPL (+12.1%)" in result
        assert "Breakout: MSFT, TSLA" in result

    def test_breakout_no_gap_pct(self):
        """Breakout candidates typically have no gap_pct — show ticker only."""
        watchlist = [
            {"ticker": "AMZN", "setup_type": "breakout"},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "Breakout: AMZN" in result
        # No percentage should appear for breakout
        assert "%" not in result.split("Breakout:")[1]

    def test_parabolic_short_label(self):
        watchlist = [
            {"ticker": "MEME", "setup_type": "parabolic_short", "gap_pct": 0},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "Parabolic Short: MEME" in result


# ---------------------------------------------------------------------------
# _wait_for_fill
# ---------------------------------------------------------------------------

class TestWaitForFill:
    def test_filled_immediately(self):
        client = MagicMock()
        client.get_order_status.return_value = {
            "status": "filled",
            "filled_qty": 100,
            "filled_avg_price": 50.0,
        }
        result = _wait_for_fill(client, "order-123", timeout_secs=5)
        assert result is not None
        assert result["status"] == "filled"

    def test_cancelled_returns_none(self):
        client = MagicMock()
        client.get_order_status.return_value = {"status": "cancelled"}
        result = _wait_for_fill(client, "order-123", timeout_secs=5)
        assert result is None

    def test_partial_fill_accepted_on_timeout(self):
        """When order times out but has partial fills, accept the partial fill."""
        call_count = 0

        def mock_status(order_id):
            nonlocal call_count
            call_count += 1
            return {
                "status": "partially_filled",
                "filled_qty": 50,
                "filled_avg_price": 49.5,
            }

        client = MagicMock()
        client.get_order_status.side_effect = mock_status
        result = _wait_for_fill(client, "order-123", timeout_secs=1)
        assert result is not None
        assert result["filled_qty"] == 50

    def test_timeout_no_fill_returns_none(self):
        client = MagicMock()
        client.get_order_status.return_value = {"status": "submitted"}
        result = _wait_for_fill(client, "order-123", timeout_secs=1)
        assert result is None


# ---------------------------------------------------------------------------
# compute_daily_pnl with current_prices
# ---------------------------------------------------------------------------

class TestComputeDailyPnl:
    def test_unrealized_pnl_with_current_prices(self):
        """compute_daily_pnl should use current_prices for unrealized P&L."""
        from unittest.mock import MagicMock, patch
        from monitor.position_tracker import PositionTracker
        from db.models import init_db, get_session, Position, DailyPnl
        from datetime import datetime

        engine = init_db("sqlite:///:memory:")

        config = {
            "exits": {
                "partial_exit_after_days": 3,
                "partial_exit_gain_threshold_pct": 15.0,
                "partial_exit_fraction": 0.4,
                "trailing_ma_period": 10,
            },
            "risk": {
                "risk_per_trade_pct": 1.0,
                "max_positions": 4,
                "max_position_pct": 10.0,
                "daily_loss_limit_pct": 3.0,
                "weekly_loss_limit_pct": 5.0,
            },
        }

        tracker = PositionTracker(config, engine, MagicMock())

        # Create an open position
        with get_session(engine) as session:
            pos = Position(
                ticker="AAPL",
                setup_type="breakout",
                side="long",
                shares=100,
                entry_price=150.0,
                stop_price=145.0,
                initial_stop_price=145.0,
            )
            session.add(pos)
            session.commit()

        # Compute with current prices — AAPL at 160 means +$10/share unrealized
        daily = tracker.compute_daily_pnl(100_000.0, current_prices={"AAPL": 160.0})
        assert daily.unrealized_pnl == pytest.approx(1000.0)  # 100 * (160 - 150)
        assert daily.total_pnl == daily.realized_pnl + daily.unrealized_pnl

    def test_unrealized_pnl_without_current_prices(self):
        """Without current_prices, unrealized should be 0."""
        from monitor.position_tracker import PositionTracker
        from db.models import init_db, get_session, Position

        engine = init_db("sqlite:///:memory:")

        config = {
            "exits": {
                "partial_exit_after_days": 3,
                "partial_exit_gain_threshold_pct": 15.0,
                "partial_exit_fraction": 0.4,
                "trailing_ma_period": 10,
            },
            "risk": {
                "risk_per_trade_pct": 1.0,
                "max_positions": 4,
                "max_position_pct": 10.0,
                "daily_loss_limit_pct": 3.0,
                "weekly_loss_limit_pct": 5.0,
            },
        }

        tracker = PositionTracker(config, engine, MagicMock())

        with get_session(engine) as session:
            pos = Position(
                ticker="AAPL",
                setup_type="breakout",
                side="long",
                shares=100,
                entry_price=150.0,
                stop_price=145.0,
                initial_stop_price=145.0,
            )
            session.add(pos)
            session.commit()

        daily = tracker.compute_daily_pnl(100_000.0)
        assert daily.unrealized_pnl == 0.0


# ---------------------------------------------------------------------------
# Partial exit stop replacement
# ---------------------------------------------------------------------------

class TestPartialExitStopReplacement:
    def _make_tracker_with_position(self):
        from monitor.position_tracker import PositionTracker
        from db.models import init_db, get_session, Position

        engine = init_db("sqlite:///:memory:")
        config = {
            "exits": {
                "partial_exit_after_days": 3,
                "partial_exit_gain_threshold_pct": 15.0,
                "partial_exit_fraction": 0.4,
                "trailing_ma_period": 10,
            },
            "risk": {
                "risk_per_trade_pct": 1.0,
                "max_positions": 4,
                "max_position_pct": 10.0,
                "daily_loss_limit_pct": 3.0,
                "weekly_loss_limit_pct": 5.0,
            },
        }

        client = MagicMock()
        client.place_limit_order.return_value = "partial-order-123"
        client.cancel_order.return_value = None
        client.place_stop_order.return_value = "new-stop-456"

        tracker = PositionTracker(config, engine, client)

        with get_session(engine) as session:
            pos = Position(
                ticker="AAPL",
                setup_type="breakout",
                side="long",
                shares=100,
                entry_price=100.0,
                stop_price=95.0,
                initial_stop_price=95.0,
                stop_order_id="old-stop-789",
            )
            # Backdate opened_at to satisfy days_held >= 3
            from datetime import datetime, timedelta
            pos.opened_at = datetime.utcnow() - timedelta(days=5)
            session.add(pos)
            session.commit()
            pos_id = pos.id

        return tracker, engine, client, pos_id

    def test_partial_exit_replaces_stop_order(self):
        """After partial exit fill confirmation, old stop should be cancelled and new one placed."""
        from db.models import get_session, Position

        tracker, engine, client, pos_id = self._make_tracker_with_position()

        # Phase 1: Trigger partial exit — places limit order but does NOT resize stop yet
        tracker.on_candle_update("AAPL", 120.0, [], [])

        # Verify limit order was placed
        client.place_limit_order.assert_called_once()
        # Stop should NOT have been touched yet
        client.cancel_order.assert_not_called()
        client.place_stop_order.assert_not_called()

        with get_session(engine) as session:
            pos = session.query(Position).filter_by(id=pos_id).first()
            assert pos.partial_exit_done is False
            assert pos.partial_exit_order_id == "partial-order-123"

        # Phase 2: Next candle — mock reports the partial exit order as filled
        client.get_order_status.return_value = {
            "order_id": "partial-order-123",
            "status": "filled",
            "filled_qty": 40,
            "filled_avg_price": 120.0,
        }
        tracker.on_candle_update("AAPL", 120.0, [], [])

        # Now stop should be replaced
        client.cancel_order.assert_called_once_with("old-stop-789")
        client.place_stop_order.assert_called_once_with("AAPL", "sell", 60, 100.0)

        # Verify DB updated with new stop order ID
        with get_session(engine) as session:
            pos = session.query(Position).filter_by(id=pos_id).first()
            assert pos.stop_order_id == "new-stop-456"
            assert pos.stop_price == 100.0  # break-even
            assert pos.partial_exit_done is True
            assert pos.partial_exit_order_id is None


# ---------------------------------------------------------------------------
# Cache clearing
# ---------------------------------------------------------------------------

class TestCacheClearAndPrefetch:
    def test_clear_daily_caches(self):
        """_clear_daily_caches should empty all cache dicts."""
        import main
        with main._cache_lock:
            main._daily_bars_cache["AAPL"] = [{"close": 150}]
            main._daily_closes_cache["AAPL"] = [150.0]
            main._daily_volumes_cache["AAPL"] = [1000]
            main._daily_highs_cache["AAPL"] = [155.0]
            main._daily_lows_cache["AAPL"] = [145.0]

        _clear_daily_caches()

        assert len(main._daily_bars_cache) == 0
        assert len(main._daily_closes_cache) == 0
        assert len(main._daily_volumes_cache) == 0
        assert len(main._daily_highs_cache) == 0
        assert len(main._daily_lows_cache) == 0

    def test_prefetch_populates_caches(self):
        """_prefetch_daily_bars should populate caches from yfinance batch data."""
        import pandas as pd
        import main

        _clear_daily_caches()

        mock_client = MagicMock()
        df = pd.DataFrame({
            "open": [100.0, 101.0],
            "high": [105.0, 106.0],
            "low": [98.0, 99.0],
            "close": [103.0, 104.0],
            "volume": [50000, 60000],
        })
        mock_client.get_daily_bars_batch.return_value = {"AAPL": df}

        _prefetch_daily_bars(mock_client, ["AAPL"])

        assert "AAPL" in main._daily_bars_cache
        assert main._daily_closes_cache["AAPL"] == [103.0, 104.0]
        assert main._daily_volumes_cache["AAPL"] == [50000, 60000]
        assert main._daily_highs_cache["AAPL"] == [105.0, 106.0]
        assert main._daily_lows_cache["AAPL"] == [98.0, 99.0]

        # Clean up
        _clear_daily_caches()

    def test_prefetch_empty_tickers(self):
        """_prefetch_daily_bars should do nothing with empty list."""
        mock_client = MagicMock()
        _prefetch_daily_bars(mock_client, [])
        mock_client.get_daily_bars_batch.assert_not_called()


# ---------------------------------------------------------------------------
# Broker position reconciliation
# ---------------------------------------------------------------------------

class TestReconcilePositions:
    def _setup(self):
        from db.models import init_db, get_session, Position
        engine = init_db("sqlite:///:memory:")
        client = MagicMock()
        client.is_market_open.return_value = True
        notify = MagicMock()
        return engine, client, notify

    def test_stop_filled_at_broker_closes_db_position(self):
        """When broker reports stop as filled, reconcile should close in DB."""
        from db.models import get_session, Position
        engine, client, notify = self._setup()

        with get_session(engine) as session:
            pos = Position(
                ticker="AAPL",
                setup_type="breakout",
                side="long",
                shares=100,
                entry_price=100.0,
                stop_price=95.0,
                initial_stop_price=95.0,
                stop_order_id="stop-123",
            )
            session.add(pos)
            session.commit()

        client.get_order_status.return_value = {
            "status": "filled",
            "filled_qty": 100,
            "filled_avg_price": 95.0,
        }

        job_reconcile_positions(client, engine, notify)

        with get_session(engine) as session:
            pos = session.query(Position).first()
            assert pos.is_open is False
            assert pos.exit_reason == "stop_hit"
            assert pos.exit_price == 95.0
            assert pos.realized_pnl == pytest.approx(-500.0)  # 100 * (95 - 100)

        notify.assert_called_once()
        assert "STOP FILLED" in notify.call_args[0][0]

    def test_no_action_when_stop_still_active(self):
        """When broker reports stop as new/accepted, no action should be taken."""
        from db.models import get_session, Position
        engine, client, notify = self._setup()

        with get_session(engine) as session:
            pos = Position(
                ticker="AAPL",
                setup_type="breakout",
                side="long",
                shares=100,
                entry_price=100.0,
                stop_price=95.0,
                initial_stop_price=95.0,
                stop_order_id="stop-123",
            )
            session.add(pos)
            session.commit()

        client.get_order_status.return_value = {"status": "accepted"}

        job_reconcile_positions(client, engine, notify)

        with get_session(engine) as session:
            pos = session.query(Position).first()
            assert pos.is_open is True
        notify.assert_not_called()

    def test_cancelled_stop_alerts(self):
        """When broker reports stop as cancelled, alert user."""
        from db.models import get_session, Position
        engine, client, notify = self._setup()

        with get_session(engine) as session:
            pos = Position(
                ticker="AAPL",
                setup_type="breakout",
                side="long",
                shares=100,
                entry_price=100.0,
                stop_price=95.0,
                initial_stop_price=95.0,
                stop_order_id="stop-123",
            )
            session.add(pos)
            session.commit()

        client.get_order_status.return_value = {"status": "cancelled"}

        job_reconcile_positions(client, engine, notify)

        with get_session(engine) as session:
            pos = session.query(Position).first()
            assert pos.is_open is True
            assert pos.stop_order_id is None  # cleared

        notify.assert_called_once()
        assert "UNPROTECTED" in notify.call_args[0][0]

    def test_market_closed_skips(self):
        """Reconcile should do nothing when market is closed."""
        from db.models import get_session, Position
        engine, client, notify = self._setup()
        client.is_market_open.return_value = False

        with get_session(engine) as session:
            pos = Position(
                ticker="AAPL",
                setup_type="breakout",
                side="long",
                shares=100,
                entry_price=100.0,
                stop_price=95.0,
                initial_stop_price=95.0,
                stop_order_id="stop-123",
            )
            session.add(pos)
            session.commit()

        job_reconcile_positions(client, engine, notify)

        client.get_order_status.assert_not_called()
        notify.assert_not_called()
