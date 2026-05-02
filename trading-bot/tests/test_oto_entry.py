"""Tests for the OTO-based entry flow (commit shipped 2026-05-01 to address
Alpaca wash-trade rejections, error 40310000).

Covers:
  - core.execution._await_fill_and_setup_stop persists stop_order_id from
    client.get_child_stop_order_id when the OTO child is present
  - falls back to client.place_stop_order when the child is absent
    (this is the IB bot's path)
  - critical-alert path when both OTO and fallback fail
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
    def test_oto_child_id_persisted_when_present(self, engine, monkeypatch):
        """Alpaca path: get_child_stop_order_id returns the OTO child's id;
        we persist it to Position.stop_order_id without ever calling
        place_stop_order."""
        from core import execution

        order_db_id = _seed_order(engine)
        monkeypatch.setattr(execution, "_wait_for_fill", lambda *a, **kw: {
            "filled_qty": 10, "filled_avg_price": 100.0,
        })

        client = MagicMock()
        client.get_child_stop_order_id.return_value = "child-stop-789"
        client.place_stop_order.side_effect = AssertionError(
            "place_stop_order must NOT be called when OTO child exists"
        )

        execution._await_fill_and_setup_stop(
            ticker="AAPL", signal=_signal(), shares=10,
            broker_order_id="parent-id-123", order_db_id=order_db_id,
            client=client, db_engine=engine, notify=lambda m: None,
        )

        with get_session(engine) as session:
            pos = session.query(Position).filter_by(ticker="AAPL").first()
            assert pos is not None
            assert pos.stop_order_id == "child-stop-789"

    def test_falls_back_to_place_stop_order_when_no_oto_child(self, engine, monkeypatch):
        """IB path: get_child_stop_order_id returns None; we fall back to
        placing a separate stop and persisting that id."""
        from core import execution

        order_db_id = _seed_order(engine)
        monkeypatch.setattr(execution, "_wait_for_fill", lambda *a, **kw: {
            "filled_qty": 10, "filled_avg_price": 100.0,
        })
        # Patch sleep to keep the 3x retry loop fast
        monkeypatch.setattr(execution.time, "sleep", lambda *_: None)

        client = MagicMock()
        client.get_child_stop_order_id.return_value = None
        client.place_stop_order.return_value = "fallback-stop-456"

        execution._await_fill_and_setup_stop(
            ticker="AAPL", signal=_signal(), shares=10,
            broker_order_id="parent-id-123", order_db_id=order_db_id,
            client=client, db_engine=engine, notify=lambda m: None,
        )

        # The fallback stop must have been placed exactly once with the
        # correct sell-side / shares / price.
        client.place_stop_order.assert_called_once_with("AAPL", "sell", 10, 93.0)

        with get_session(engine) as session:
            pos = session.query(Position).filter_by(ticker="AAPL").first()
            assert pos.stop_order_id == "fallback-stop-456"

    def test_critical_alert_when_both_paths_fail(self, engine, monkeypatch):
        """Defense-in-depth: if OTO returns None AND place_stop_order raises
        on every retry, we leave the position unprotected and notify
        loudly."""
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

        # Position exists but unprotected, and a CRITICAL alert was sent.
        with get_session(engine) as session:
            pos = session.query(Position).filter_by(ticker="AAPL").first()
            assert pos is not None
            assert pos.stop_order_id is None

        assert any("CRITICAL" in m and "UNPROTECTED" in m for m in sent), sent
