"""Tests for the 9:40 AM ET EP time-based partial-profit job.

Rule (added 2026-05-11 after backtest on corrected 2020-2026 Spikeet data):
  At day 19+ in trade, if position is in profit (current price > entry),
  scale out 40% via market sell + move stop on remainder to entry × 1.05.

Snapshot backtest showed: PF 3.46 → 3.86, win rate 48.5% → 61.3%,
capital efficiency (return per share-day deployed) +20%.

Implementation: monitor/position_tracker.py::check_ep_time_partial.
Scheduled at 9:40 AM ET from main.py and main_ib.py so order-failure
retries have 6h of market time rather than EOD's 5-min squeeze.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from db.models import Position, init_db, get_session
from monitor.position_tracker import PositionTracker


def _base_config() -> dict:
    """Minimal config the tracker needs to instantiate."""
    return {
        "exits": {
            "partial_exit_after_days": 3,
            "partial_exit_gain_threshold_pct": 15.0,
            "partial_exit_fraction": 0.40,
            "trailing_ma_period": 10,
            "ep_time_partial_day": 19,
            "ep_time_partial_fraction": 0.40,
            "ep_time_partial_new_stop_pct": 5.0,
        },
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_positions": 30,
            "max_position_pct": 10.0,
            "daily_loss_limit_pct": 3.0,
            "weekly_loss_limit_pct": 5.0,
        },
    }


def _make_client(market_open: bool = True, current_price: float = 110.0):
    """Stub broker client. Defaults to market-open + price = $110."""
    client = MagicMock()
    client.is_market_open.return_value = market_open
    client.get_realtime_quote.return_value = {
        "bid": current_price - 0.05, "ask": current_price + 0.05,
        "last_price": current_price,
    }
    # Order-related stubs default to success; tests override as needed.
    client.cancel_order.return_value = None
    client.close_position.return_value = "partial-order-1"
    client.get_order_status.return_value = {
        "status": "filled", "filled_qty": 40, "filled_avg_price": 110.0,
    }
    client.place_stop_order.return_value = "new-stop-1"
    return client


def _make_ep_position(
    session, ticker: str = "AMD", setup_type: str = "ep_earnings_b",
    days_held: int = 25, entry_price: float = 100.0, shares: int = 100,
    stop_price: float = 93.0, partial_exit_done: bool = False,
    partial_exit_order_id: str | None = None,
) -> Position:
    """Insert an EP position with controllable days_held by setting opened_at."""
    pos = Position(
        ticker=ticker, setup_type=setup_type, side="long",
        shares=shares, entry_price=entry_price,
        stop_price=stop_price, initial_stop_price=stop_price,
        stop_order_id="old-stop-id",
        opened_at=datetime.utcnow() - timedelta(days=days_held),
        partial_exit_done=partial_exit_done,
        partial_exit_order_id=partial_exit_order_id,
    )
    session.add(pos)
    session.commit()
    return pos


# ─────────────────────────────────────────────────────────────────────
# Eligibility gating — should/should-not fire
# ─────────────────────────────────────────────────────────────────────


class TestEligibility:
    def test_fires_for_ep_earnings_at_day_19_in_profit(self):
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)  # +10% profit
        tracker = PositionTracker(_base_config(), engine, client)

        with get_session(engine) as s:
            _make_ep_position(s, setup_type="ep_earnings_b", days_held=19)

        summary = tracker.check_ep_time_partial()

        client.cancel_order.assert_called_once_with("old-stop-id")
        client.close_position.assert_called_once_with("AMD", 40, "long")
        client.place_stop_order.assert_called_once()
        assert "fired=AMD" in summary

    def test_fires_for_ep_news_at_day_20_in_profit(self):
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, ticker="STRL", setup_type="ep_news_a", days_held=20)
        summary = tracker.check_ep_time_partial()
        assert "fired=STRL" in summary

    def test_skips_when_days_held_under_19(self):
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=10)
        summary = tracker.check_ep_time_partial()
        assert summary == "0 candidates"
        client.close_position.assert_not_called()

    def test_skips_when_not_in_profit(self):
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=99.0)  # below entry
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=25)
        summary = tracker.check_ep_time_partial()
        client.close_position.assert_not_called()
        assert "not_in_profit=AMD" in summary

    def test_skips_when_partial_already_done(self):
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=25, partial_exit_done=True)
        assert tracker.check_ep_time_partial() == "0 candidates"
        client.close_position.assert_not_called()

    def test_skips_when_partial_in_flight(self):
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=25, partial_exit_order_id="pending-1")
        assert tracker.check_ep_time_partial() == "0 candidates"
        client.close_position.assert_not_called()

    def test_skips_non_ep_setups(self):
        """Breakout / episodic_pivot positions don't trigger this rule."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, setup_type="breakout", days_held=25)
            _make_ep_position(s, ticker="XYZ", setup_type="episodic_pivot", days_held=25)
        assert tracker.check_ep_time_partial() == "0 candidates"
        client.close_position.assert_not_called()

    def test_skips_when_market_closed(self):
        engine = init_db("sqlite:///:memory:")
        client = _make_client(market_open=False, current_price=110.0)
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=25)
        summary = tracker.check_ep_time_partial()
        assert "market not open" in summary.lower()
        client.close_position.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Happy path: full 4-step sequence
# ─────────────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_4_step_sequence_db_updated(self):
        """Cancel old stop → market sell partial → mark DB → place new stop at +5%."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=22, entry_price=100.0, shares=100)

        tracker.check_ep_time_partial()

        with get_session(engine) as s:
            pos = s.query(Position).filter_by(ticker="AMD").first()
            # DB updated to reflect the partial
            assert pos.partial_exit_done is True
            assert pos.partial_exit_shares == 40
            assert pos.partial_exit_price == 110.0
            assert pos.partial_exit_order_id is None
            # Stop replaced — entry × 1.05 = $105.00
            assert pos.stop_price == 105.0
            assert pos.stop_order_id == "new-stop-1"
            # Position itself still marked open (remainder is held)
            assert pos.is_open is True

    def test_broker_calls_in_correct_order(self):
        """cancel BEFORE close_position; place_stop_order AFTER close."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=22)

        call_order = []
        client.cancel_order.side_effect = lambda *a, **kw: call_order.append("cancel")
        client.close_position.side_effect = lambda *a, **kw: (
            call_order.append("close"), "partial-order-1"
        )[1]
        client.place_stop_order.side_effect = lambda *a, **kw: (
            call_order.append("place_stop"), "new-stop-1"
        )[1]

        tracker.check_ep_time_partial()
        assert call_order == ["cancel", "close", "place_stop"]


# ─────────────────────────────────────────────────────────────────────
# Failure paths — sequenced unwind
# ─────────────────────────────────────────────────────────────────────


class TestFailureRecovery:
    def test_partial_sell_fails_restores_old_stop(self):
        """If cancel succeeds but market-sell fails, we must put the original
        stop back so the position isn't naked. Then raise so _track_job sees it."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        # close_position raises (e.g., insufficient BP, broker reject)
        client.close_position.side_effect = RuntimeError("broker rejected")
        # place_stop_order will be called twice: once to restore, that one succeeds
        client.place_stop_order.return_value = "restored-stop-1"

        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=22, stop_price=93.0)

        # check_ep_time_partial raises when all attempts fail
        with pytest.raises(RuntimeError):
            tracker.check_ep_time_partial()

        with get_session(engine) as s:
            pos = s.query(Position).filter_by(ticker="AMD").first()
            # Did NOT partial
            assert pos.partial_exit_done is False
            # Stop was restored to original price
            assert pos.stop_price == 93.0
            assert pos.stop_order_id == "restored-stop-1"

    def test_stop_placement_fails_alerts_loud(self):
        """After partial fills, if new-stop placement fails 3 times we raise
        loudly — drift detector will catch the naked state next reconcile."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        # All 3 stop placements fail
        client.place_stop_order.side_effect = RuntimeError("broker error")

        tracker = PositionTracker(_base_config(), engine, client)
        notifications = []
        tracker.notify = lambda msg: notifications.append(msg)

        with get_session(engine) as s:
            _make_ep_position(s, days_held=22)

        with pytest.raises(RuntimeError, match="CRITICAL"):
            tracker.check_ep_time_partial()

        # 3 attempts made
        assert client.place_stop_order.call_count == 3
        # Loud Telegram alert sent
        critical_alerts = [m for m in notifications if "CRITICAL" in m]
        assert len(critical_alerts) >= 1
        assert "UNPROTECTED" in critical_alerts[0]

        # Partial state IS marked done (the sell happened) even though stop failed
        with get_session(engine) as s:
            pos = s.query(Position).filter_by(ticker="AMD").first()
            assert pos.partial_exit_done is True
            assert pos.partial_exit_shares == 40

    def test_stop_placement_succeeds_on_retry(self):
        """If first stop-placement attempt fails but second succeeds, we
        recover cleanly without raising."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        # First call raises, second returns OK
        client.place_stop_order.side_effect = [
            RuntimeError("transient"), "new-stop-1",
        ]
        tracker = PositionTracker(_base_config(), engine, client)
        with get_session(engine) as s:
            _make_ep_position(s, days_held=22)

        summary = tracker.check_ep_time_partial()
        assert "fired=AMD" in summary
        assert client.place_stop_order.call_count == 2

    def test_pending_partial_records_state_no_raise(self):
        """If the partial order is placed but doesn't fill within timeout,
        we record `partial_exit_order_id` and return — next 9:40 run picks
        it up via existing _check_pending_partial_exit machinery."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client(current_price=110.0)
        # Order stays in 'submitted' state, never reaches 'filled'
        client.get_order_status.return_value = {"status": "submitted"}
        tracker = PositionTracker(_base_config(), engine, client)

        with get_session(engine) as s:
            _make_ep_position(s, days_held=22)

        # Speed up the _wait_for_partial_fill loop in test by patching time.sleep
        # not strictly needed since timeout is 15s but the poll is 1s; tests can
        # tolerate a 15s wait. Use a much shorter timeout via monkey-patching the
        # tracker's wait helper.
        import time as _t
        orig_sleep = _t.sleep
        _t.sleep = lambda s: None
        try:
            tracker._wait_for_partial_fill = lambda oid, timeout_s=15: False
            tracker.check_ep_time_partial()
        finally:
            _t.sleep = orig_sleep

        with get_session(engine) as s:
            pos = s.query(Position).filter_by(ticker="AMD").first()
            assert pos.partial_exit_done is False
            assert pos.partial_exit_order_id == "partial-order-1"
            assert pos.partial_exit_shares == 40

        # No new stop placed yet — that happens after fill confirmation
        client.place_stop_order.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Integration: trailing-MA gate
# ─────────────────────────────────────────────────────────────────────


class TestTrailingMAGate:
    def test_ma_close_skips_ep_positions_after_partial(self):
        """_check_ma_close_exits must NOT close EP positions even after
        partial_exit_done=True. The EP rule is fixed-percentage trail,
        not MA-based."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client()
        tracker = PositionTracker(_base_config(), engine, client)

        with get_session(engine) as s:
            pos = _make_ep_position(
                s, setup_type="ep_earnings_b",
                days_held=25, partial_exit_done=True,
            )

        # Daily closes: today's $90 is well below 10-day MA of $105.
        # If the gate were broken, the MA-close path would fire and close
        # this position. With the gate, it must skip.
        daily_closes_map = {"AMD": [105, 106, 107, 108, 109, 108, 107, 106, 105, 90]}

        with get_session(engine) as s:
            positions = s.query(Position).filter_by(is_open=True).all()
            tracker._check_ma_close_exits(s, positions, daily_closes_map)

        with get_session(engine) as s:
            pos = s.query(Position).filter_by(ticker="AMD").first()
            assert pos.is_open is True  # NOT closed by MA-trail logic

    def test_ma_close_still_fires_for_non_ep_setups(self):
        """The gate only affects EP setups. breakout / episodic_pivot
        positions still get MA-trail-close as before."""
        engine = init_db("sqlite:///:memory:")
        client = _make_client()
        tracker = PositionTracker(_base_config(), engine, client)

        with get_session(engine) as s:
            _make_ep_position(
                s, ticker="BREAK", setup_type="breakout",
                days_held=25, partial_exit_done=True,
            )

        daily_closes_map = {"BREAK": [105, 106, 107, 108, 109, 108, 107, 106, 105, 90]}

        with get_session(engine) as s:
            positions = s.query(Position).filter_by(is_open=True).all()
            tracker._check_ma_close_exits(s, positions, daily_closes_map)

        with get_session(engine) as s:
            pos = s.query(Position).filter_by(ticker="BREAK").first()
            assert pos.is_open is False
            assert pos.exit_reason == "trailing_ma_close"
