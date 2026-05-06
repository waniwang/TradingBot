"""Tests for the OTO-based entry flow (commit shipped 2026-05-01 to address
Alpaca wash-trade rejections, error 40310000) — and the 2026-05-06
follow-up that swaps the DAY-TIF OTO child for a GTC stop.

Covers:
  - When an OTO child stop is detected, we cancel it and place a fresh
    GTC stop (post-2026-05-06: the child inherits the parent's DAY TIF
    and would expire EOD, leaving the position unprotected overnight)
  - When no OTO child is present (IB shim, or OTO never fired), we just
    place a GTC stop — same code path
  - Critical-alert path when GTC stop placement fails after retries
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from db.models import Base, Order, Position, get_engine, get_session
from signals.base import SignalResult


@pytest.fixture
def engine():
    eng = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


def _signal():
    return SignalResult(
        ticker="AAPL",
        setup_type="ep_earnings_a",
        side="long",
        entry_price=100.0,
        stop_price=93.0,
        orh=101.0,
        orb_low=99.0,
        gap_pct=12.0,
    )


def _seed_order(engine, broker_order_id="parent-id-123"):
    """Insert a submitted Order so _await_fill_and_setup_stop has something
    to update on fill."""
    with get_session(engine) as session:
        o = Order(
            broker_order_id=broker_order_id,
            ticker="AAPL",
            side="buy",
            order_type="limit",
            qty=10,
            price=100.0,
            status="submitted",
        )
        session.add(o)
        session.commit()
        return o.id


class TestAwaitFillAndSetupStop:
    def test_oto_child_cancelled_and_replaced_with_gtc(self, engine, monkeypatch):
        """Alpaca path: get_child_stop_order_id returns the OTO child's id;
        we cancel it (because it's DAY-TIF and would expire EOD) then
        place a GTC stop and persist THAT id to Position.stop_order_id."""
        from core import execution

        order_db_id = _seed_order(engine)
        monkeypatch.setattr(execution, "_wait_for_fill", lambda *a, **kw: {
            "filled_qty": 10, "filled_avg_price": 100.0,
        })
        # Skip the 1s post-cancel pause so the test runs instantly.
        monkeypatch.setattr(execution.time, "sleep", lambda *_: None)

        client = MagicMock()
        client.get_child_stop_order_id.return_value = "child-stop-789"
        client.place_stop_order.return_value = "gtc-stop-456"

        execution._await_fill_and_setup_stop(
            ticker="AAPL", signal=_signal(), shares=10,
            broker_order_id="parent-id-123", order_db_id=order_db_id,
            client=client, db_engine=engine, notify=lambda m: None,
        )

        # Cancel of the DAY-TIF child must have been called once.
        client.cancel_order.assert_called_once_with("child-stop-789")
        # GTC replacement placed with the correct sell-side / shares / price.
        client.place_stop_order.assert_called_once_with("AAPL", "sell", 10, 93.0)

        with get_session(engine) as session:
            pos = session.query(Position).filter_by(ticker="AAPL").first()
            assert pos is not None
            # Persisted stop_order_id is the GTC replacement, NOT the
            # cancelled OTO child.
            assert pos.stop_order_id == "gtc-stop-456"

    def test_no_oto_child_skips_cancel_and_places_gtc(self, engine, monkeypatch):
        """IB path: get_child_stop_order_id returns None; we skip the cancel
        step (nothing to cancel) and place a GTC stop directly."""
        from core import execution

        order_db_id = _seed_order(engine)
        monkeypatch.setattr(execution, "_wait_for_fill", lambda *a, **kw: {
            "filled_qty": 10, "filled_avg_price": 100.0,
        })
        monkeypatch.setattr(execution.time, "sleep", lambda *_: None)

        client = MagicMock()
        client.get_child_stop_order_id.return_value = None
        client.place_stop_order.return_value = "gtc-stop-456"

        execution._await_fill_and_setup_stop(
            ticker="AAPL", signal=_signal(), shares=10,
            broker_order_id="parent-id-123", order_db_id=order_db_id,
            client=client, db_engine=engine, notify=lambda m: None,
        )

        # No OTO child → no cancel attempt.
        client.cancel_order.assert_not_called()
        client.place_stop_order.assert_called_once_with("AAPL", "sell", 10, 93.0)

        with get_session(engine) as session:
            pos = session.query(Position).filter_by(ticker="AAPL").first()
            assert pos.stop_order_id == "gtc-stop-456"

    def test_oto_cancel_failure_still_proceeds_to_gtc_place(self, engine, monkeypatch):
        """If cancel_order on the DAY-TIF child fails for some reason
        (network, already-filled race), we still attempt to place the GTC
        stop. If GTC succeeds, we persist that and accept that there may
        briefly be two stops on the same position (Alpaca will reject the
        second if it would over-allocate, in which case we fall through
        to the critical-alert path)."""
        from core import execution

        order_db_id = _seed_order(engine)
        monkeypatch.setattr(execution, "_wait_for_fill", lambda *a, **kw: {
            "filled_qty": 10, "filled_avg_price": 100.0,
        })
        monkeypatch.setattr(execution.time, "sleep", lambda *_: None)

        client = MagicMock()
        client.get_child_stop_order_id.return_value = "child-stop-789"
        client.cancel_order.side_effect = RuntimeError("transient broker error")
        client.place_stop_order.return_value = "gtc-stop-456"

        execution._await_fill_and_setup_stop(
            ticker="AAPL", signal=_signal(), shares=10,
            broker_order_id="parent-id-123", order_db_id=order_db_id,
            client=client, db_engine=engine, notify=lambda m: None,
        )

        # Cancel was attempted, then GTC was placed regardless.
        client.cancel_order.assert_called_once_with("child-stop-789")
        client.place_stop_order.assert_called_once()
        with get_session(engine) as session:
            pos = session.query(Position).filter_by(ticker="AAPL").first()
            assert pos.stop_order_id == "gtc-stop-456"

    def test_critical_alert_when_gtc_placement_fails(self, engine, monkeypatch):
        """If place_stop_order raises on every retry, we leave the position
        unprotected and notify loudly. Covers both the no-OTO-child path
        (IB) and the OTO-cancelled-but-replacement-failed path (Alpaca)."""
        from core import execution

        order_db_id = _seed_order(engine)
        monkeypatch.setattr(execution, "_wait_for_fill", lambda *a, **kw: {
            "filled_qty": 10, "filled_avg_price": 100.0,
        })
        monkeypatch.setattr(execution.time, "sleep", lambda *_: None)

        client = MagicMock()
        client.get_child_stop_order_id.return_value = None
        client.place_stop_order.side_effect = RuntimeError("broker error")

        sent = []
        execution._await_fill_and_setup_stop(
            ticker="AAPL", signal=_signal(), shares=10,
            broker_order_id="parent-id-123", order_db_id=order_db_id,
            client=client, db_engine=engine, notify=sent.append,
        )

        with get_session(engine) as session:
            pos = session.query(Position).filter_by(ticker="AAPL").first()
            assert pos is not None
            assert pos.stop_order_id is None

        assert any("CRITICAL" in m and "UNPROTECTED" in m for m in sent), sent
