"""
Unit tests for main module helpers.
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from main import _format_watchlist_notification, _wait_for_fill


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
        """After partial exit, old stop should be cancelled and new one placed with reduced qty."""
        from db.models import get_session, Position

        tracker, engine, client, pos_id = self._make_tracker_with_position()

        # Trigger partial exit: price at 120 → 20% gain > 15% threshold
        tracker.on_candle_update("AAPL", 120.0, [], [])

        # Verify cancel was called on old stop
        client.cancel_order.assert_called_once_with("old-stop-789")

        # Verify new stop placed with reduced qty (100 - 40 = 60 shares)
        client.place_stop_order.assert_called_once_with("AAPL", "sell", 60, 100.0)

        # Verify DB updated with new stop order ID
        with get_session(engine) as session:
            pos = session.query(Position).filter_by(id=pos_id).first()
            assert pos.stop_order_id == "new-stop-456"
            assert pos.stop_price == 100.0  # break-even
            assert pos.partial_exit_done is True
