"""Tests for the EP Breakout strategy (EP 2.0 Track A).

Covers:
  - the confirm state machine (`plugin._decide`): gap-low break, deadline,
    rested-breakout entry, chase guard, wait-during-rest, gap-day level
    refresh from daily bars, data-failure raises
  - the +30% target partial (position_tracker.check_ep_breakout_target_partial)
  - the EOD breakeven move (_check_ep_breakout_breakeven)
  - the MA-close exit exception for ep_breakout (active from day 1)
  - _check_max_hold_exits ep_breakout entry
  - trailing-stop tightening is disabled for ep_breakout

Strategy reference: sweeps/path_harness.py::find_rested_breakout_entry
(the validated simulator these live rules must mirror).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.models import Position, init_db, get_session
from monitor.position_tracker import PositionTracker
from strategies.ep_breakout.plugin import EPBreakoutPlugin


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

GAP_HIGH = 110.0
GAP_LOW = 100.0
GAP_DATE = date(2026, 6, 1)  # a Monday


def _meta(**over):
    m = {"gap_date": GAP_DATE.isoformat(), "gap_high": GAP_HIGH,
         "gap_low": GAP_LOW, "gap_pct": 12.0, "atr_pct": 4.0,
         "market_cap": 8e9, "dollar_vol": 2e8, "prev_close": 95.0,
         "open_price": 106.0}
    m.update(over)
    return m


def _bars(closes: list[float], start: date = None, gap_bar: bool = True):
    """Build a daily-bars frame like get_daily_bars_batch returns:
    a 'date' COLUMN (not index) + ohlcv. First row = gap day when gap_bar."""
    start = start or GAP_DATE
    rows = []
    d = start
    if gap_bar:
        rows.append({"date": pd.Timestamp(d), "open": 106.0, "high": GAP_HIGH,
                     "low": GAP_LOW, "close": 108.0, "volume": 1e7})
    for c in closes:
        d = _next_weekday(d)
        rows.append({"date": pd.Timestamp(d), "open": c, "high": c + 0.5,
                     "low": c - 0.5, "close": c, "volume": 5e6})
    return pd.DataFrame(rows), d


def _next_weekday(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _snap(price: float) -> dict:
    return {"latest_price": price, "prev_close": 100.0, "daily_volume": 1e7}


def decide(closes, price, today=None, **kw):
    """Run _decide with `closes` completed post-gap sessions and today's
    live `price`. today defaults to the next weekday after the last bar."""
    df, last = _bars(closes)
    today = today or _next_weekday(last)
    return EPBreakoutPlugin._decide(
        "TEST", _meta(), df, _snap(price), today,
        kw.get("bo_min_days", 4), kw.get("bo_window", 15),
        kw.get("premium_pct", 5.0),
    )


# ---------------------------------------------------------------------------
# confirm state machine
# ---------------------------------------------------------------------------

class TestDecide:
    def test_waits_during_rest_period(self):
        # 2 completed sessions, price above gap high — too early (min 4)
        r = decide([105, 106], price=111.0)
        assert r["action"] == "watch"

    def test_enters_after_rest_on_breakout(self):
        # 4 completed sessions holding the range, today closes over gap high
        r = decide([105, 104, 106, 107], price=111.0)
        assert r["action"] == "enter"
        assert r["meta_updates"]["confirm_day"] == 4
        assert r["price"] == 111.0

    def test_chase_guard_expires(self):
        # First qualifying close is >5% above the gap high -> dead, not chased
        r = decide([105, 104, 106, 107], price=GAP_HIGH * 1.06)
        assert r["action"] == "expire"
        assert r["reason"] == "chase guard"

    def test_gap_low_break_in_history_expires(self):
        r = decide([105, 99.0, 106, 107], price=111.0)  # closed below 100
        assert r["action"] == "expire"
        assert r["reason"] == "gap-low break"

    def test_gap_low_break_live_price_expires(self):
        r = decide([105, 104, 106, 107], price=99.0)
        assert r["action"] == "expire"
        assert r["reason"] == "gap-low break"

    def test_deadline_expires(self):
        closes = [105 + (i % 3) * 0.5 for i in range(15)]  # 15 quiet sessions
        r = decide(closes, price=106.0)
        assert r["action"] == "expire"
        assert r["reason"] == "no confirmation"

    def test_below_gap_high_keeps_watching(self):
        r = decide([105, 104, 106, 107], price=109.0)
        assert r["action"] == "watch"

    def test_gap_levels_refreshed_from_daily_bar(self):
        # Daily bar carries the authoritative gap-day high (112, not the
        # provisional 110 in meta) -> price 111 is NOT a breakout.
        df, last = _bars([105, 104, 106, 107])
        df.loc[0, "high"] = 112.0
        today = _next_weekday(last)
        r = EPBreakoutPlugin._decide("TEST", _meta(), df, _snap(111.0),
                                     today, 4, 15, 5.0)
        assert r["action"] == "watch"

    def test_missing_bars_raises(self):
        with pytest.raises(RuntimeError):
            EPBreakoutPlugin._decide("TEST", _meta(), None, _snap(111.0),
                                     date(2026, 6, 10), 4, 15, 5.0)

    def test_missing_snapshot_raises(self):
        df, _ = _bars([105])
        with pytest.raises(RuntimeError):
            EPBreakoutPlugin._decide("TEST", _meta(), df, None,
                                     date(2026, 6, 10), 4, 15, 5.0)

    def test_matches_path_harness_reference(self):
        """The live decision must mirror the validated simulator on the same
        path: sweeps/path_harness.find_rested_breakout_entry."""
        import numpy as np
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from sweeps.path_harness import find_rested_breakout_entry

        closes = [105, 104, 106, 107, 111.0]  # entry on 5th bar (index 4)
        bars_arr = np.array(
            [(c, c + 0.5, c - 0.5, c) for c in closes], dtype=float)
        ref = find_rested_breakout_entry(GAP_HIGH, GAP_LOW, bars_arr,
                                         min_days=4, window=15,
                                         max_premium=1.05)
        assert ref == (4, pytest.approx(111.0))

        # Live: 4 completed sessions, today's price = the 5th close.
        r = decide(closes[:4], price=closes[4])
        assert r["action"] == "enter"
        assert r["meta_updates"]["confirm_day"] == 4


# ---------------------------------------------------------------------------
# position_tracker exits
# ---------------------------------------------------------------------------

def _base_config() -> dict:
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
        "signals": {
            "ep_breakout_profit_target_pct": 30.0,
            "ep_breakout_profit_target_fraction": 0.33,
            "ep_breakout_breakeven_trigger_pct": 15.0,
            "ep_breakout_trail_ma_days": 10,
            "ep_breakout_max_hold_days": 50,
        },
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_positions": 30,
            "max_position_pct": 10.0,
            "daily_loss_limit_pct": 3.0,
            "weekly_loss_limit_pct": 5.0,
        },
    }


def _make_client(market_open: bool = True, current_price: float = 132.0):
    client = MagicMock()
    client.is_market_open.return_value = market_open
    client.get_realtime_quote.return_value = {
        "bid": current_price - 0.05, "ask": current_price + 0.05,
        "last_price": current_price,
    }
    client.cancel_order.return_value = None
    client.close_position.return_value = "partial-order-1"
    client.get_order_status.return_value = {
        "status": "filled", "filled_qty": 33, "filled_avg_price": current_price,
    }
    client.place_stop_order.return_value = "new-stop-1"
    client.modify_stop_order.return_value = None
    return client


@pytest.fixture
def engine():
    return init_db("sqlite:///:memory:")


def _make_bo_position(session, ticker="DELL", entry_price=100.0, shares=100,
                      stop_price=92.0, partial_exit_done=False):
    pos = Position(
        ticker=ticker, setup_type="ep_breakout", side="long",
        shares=shares, entry_price=entry_price,
        stop_price=stop_price, initial_stop_price=stop_price,
        stop_order_id="old-stop-id",
        opened_at=datetime.utcnow() - timedelta(days=5),
        partial_exit_done=partial_exit_done,
        is_open=True,
    )
    session.add(pos)
    session.commit()
    return pos


class TestTargetPartial:
    def test_fires_at_target(self, engine):
        client = _make_client(current_price=132.0)  # +32% > +30 target
        with get_session(engine) as s:
            _make_bo_position(s)
        tracker = PositionTracker(_base_config(), engine, client)
        out = tracker.check_ep_breakout_target_partial()
        assert "fired=DELL" in out
        client.close_position.assert_called_once_with("DELL", 33, "long")
        # stop re-placed at max(old stop 92, entry 100) = 100 for remainder 67
        client.place_stop_order.assert_called_with("DELL", 67, "sell", 100.0)
        with get_session(engine) as s:
            pos = s.query(Position).first()
            assert pos.partial_exit_done is True
            assert pos.stop_price == 100.0

    def test_below_target_no_fire(self, engine):
        client = _make_client(current_price=120.0)  # +20% < +30
        with get_session(engine) as s:
            _make_bo_position(s)
        tracker = PositionTracker(_base_config(), engine, client)
        out = tracker.check_ep_breakout_target_partial()
        assert "below_target=1" in out
        client.close_position.assert_not_called()

    def test_single_shot(self, engine):
        client = _make_client(current_price=140.0)
        with get_session(engine) as s:
            _make_bo_position(s, partial_exit_done=True)
        tracker = PositionTracker(_base_config(), engine, client)
        assert tracker.check_ep_breakout_target_partial() == "0 candidates"

    def test_ignores_other_setups(self, engine):
        client = _make_client(current_price=140.0)
        with get_session(engine) as s:
            pos = _make_bo_position(s)
            pos.setup_type = "ep_earnings_b"
            s.commit()
        tracker = PositionTracker(_base_config(), engine, client)
        assert tracker.check_ep_breakout_target_partial() == "0 candidates"

    def test_market_closed_skips(self, engine):
        client = _make_client(market_open=False)
        tracker = PositionTracker(_base_config(), engine, client)
        assert "market not open" in tracker.check_ep_breakout_target_partial()


class TestBreakeven:
    def test_moves_stop_to_entry_after_trigger_close(self, engine):
        client = _make_client()
        with get_session(engine) as s:
            _make_bo_position(s)
            positions = s.query(Position).all()
            tracker = PositionTracker(_base_config(), engine, client)
            tracker._check_ep_breakout_breakeven(
                s, positions, {"DELL": [100.0, 116.0]})  # close +16% >= +15
            pos = s.query(Position).first()
            assert pos.stop_price == 100.0
        client.modify_stop_order.assert_called_once_with("old-stop-id", 100.0)

    def test_no_move_below_trigger(self, engine):
        client = _make_client()
        with get_session(engine) as s:
            _make_bo_position(s)
            positions = s.query(Position).all()
            tracker = PositionTracker(_base_config(), engine, client)
            tracker._check_ep_breakout_breakeven(s, positions, {"DELL": [114.0]})
            assert s.query(Position).first().stop_price == 92.0
        client.modify_stop_order.assert_not_called()

    def test_broker_failure_keeps_db_stop(self, engine):
        client = _make_client()
        client.modify_stop_order.side_effect = RuntimeError("api down")
        with get_session(engine) as s:
            _make_bo_position(s)
            positions = s.query(Position).all()
            tracker = PositionTracker(_base_config(), engine, client)
            tracker._check_ep_breakout_breakeven(s, positions, {"DELL": [116.0]})
            assert s.query(Position).first().stop_price == 92.0  # unchanged

    def test_one_shot_once_at_breakeven(self, engine):
        client = _make_client()
        with get_session(engine) as s:
            _make_bo_position(s, stop_price=100.0)  # already at entry
            positions = s.query(Position).all()
            tracker = PositionTracker(_base_config(), engine, client)
            tracker._check_ep_breakout_breakeven(s, positions, {"DELL": [120.0]})
        client.modify_stop_order.assert_not_called()


class TestMaCloseExit:
    def test_ep_breakout_trails_from_day_one(self, engine):
        """No partial-exit precondition: close < MA10 exits immediately."""
        client = _make_client()
        with get_session(engine) as s:
            _make_bo_position(s, partial_exit_done=False)
            positions = s.query(Position).all()
            tracker = PositionTracker(_base_config(), engine, client)
            tracker._close_position = MagicMock()
            closes = [110.0] * 9 + [100.0]  # MA10=109, close 100 below
            tracker._check_ma_close_exits(s, positions, {"DELL": closes})
            tracker._close_position.assert_called_once()
            assert tracker._close_position.call_args.kwargs.get("reason") \
                == "trailing_ma_close"

    def test_ep_earnings_still_gated(self, engine):
        client = _make_client()
        with get_session(engine) as s:
            pos = _make_bo_position(s, partial_exit_done=True)
            pos.setup_type = "ep_earnings_b"
            s.commit()
            positions = s.query(Position).all()
            tracker = PositionTracker(_base_config(), engine, client)
            tracker._close_position = MagicMock()
            closes = [110.0] * 9 + [100.0]
            tracker._check_ma_close_exits(s, positions, {"DELL": closes})
            tracker._close_position.assert_not_called()


class TestEodIntegration:
    def test_max_hold_uses_ep_breakout_config(self, engine):
        client = _make_client()
        cfg = _base_config()
        cfg["signals"]["ep_breakout_max_hold_days"] = 7
        with get_session(engine) as s:
            _make_bo_position(s)  # opened 5 days ago -> under 7
            positions = s.query(Position).all()
            tracker = PositionTracker(cfg, engine, client)
            tracker._close_position = MagicMock()
            tracker._check_max_hold_exits(s, positions, {"DELL": [105.0]})
            tracker._close_position.assert_not_called()

            pos = positions[0]
            pos.opened_at = datetime.utcnow() - timedelta(days=8)
            s.commit()
            tracker._check_max_hold_exits(s, positions, {"DELL": [105.0]})
            tracker._close_position.assert_called_once()

    def test_trailing_stop_tightening_skipped(self, engine):
        """run_eod_tasks must NOT MA-tighten ep_breakout broker stops."""
        client = _make_client()
        with get_session(engine) as s:
            _make_bo_position(s)
        tracker = PositionTracker(_base_config(), engine, client)
        tracker._update_trailing_stop = MagicMock()
        tracker._check_ep_breakout_breakeven = MagicMock()
        closes = [120.0] * 12  # MA10 well above the 92 stop
        tracker.run_eod_tasks({"DELL": closes})
        tracker._update_trailing_stop.assert_not_called()
