"""
Tests for the EP execution-drop check (Check 19) in verify_day.py.

This is the diagnostic that surfaces silent drops in EP A/B/C execution —
e.g. a watchlist row marked ready/triggered but no actual order/Signal placed.
The check is the operational backstop in case a future bug re-introduces the
April 9, 2026 silent-drop pattern.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from db.models import Base, Watchlist, Signal, get_engine, get_session
from verify_day import run_checks


@pytest.fixture
def db_engine():
    engine = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _config():
    return {
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 15.0,
            "max_positions": 4,
        },
    }


def _client():
    """Stub broker client — Check 19 doesn't call any broker methods."""
    return MagicMock()


def _make_wl(ticker, setup_type, stage, scan_date, stage_changed_at=None):
    return Watchlist(
        ticker=ticker, setup_type=setup_type, stage=stage,
        scan_date=scan_date,
        added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        stage_changed_at=stage_changed_at or datetime.utcnow(),
        metadata_json=json.dumps({"ep_strategy": "A"}),
    )


def _get_check19(results):
    return next((r for r in results if r.num == 19), None)


def test_flags_ready_rows_with_no_signal(db_engine):
    """The classic April 9 failure mode: row stuck at stage='ready' = unexecuted."""
    target = date.today()
    with get_session(db_engine) as session:
        session.add(_make_wl("LEVI", "ep_earnings", "ready", target - timedelta(days=1)))
        session.add(_make_wl("AU", "ep_news", "ready", target))
        session.commit()

    results = run_checks(target, db_engine, _client(), _config(), log_lines=[])
    check19 = _get_check19(results)

    assert check19 is not None
    assert check19.status == "FAIL"
    assert "LEVI" in check19.detail
    assert "AU" in check19.detail


def test_flags_triggered_rows_with_no_signal(db_engine):
    """Watchlist row marked triggered today but no Signal fired = orphaned trigger."""
    target = date.today()
    triggered_today = datetime.combine(target, datetime.min.time()) + timedelta(hours=15, minutes=50)

    with get_session(db_engine) as session:
        session.add(_make_wl(
            "STAA", "ep_earnings", "triggered", target - timedelta(days=1),
            stage_changed_at=triggered_today,
        ))
        session.commit()

    results = run_checks(target, db_engine, _client(), _config(), log_lines=[])
    check19 = _get_check19(results)
    assert check19 is not None
    assert check19.status == "FAIL"
    assert "STAA" in check19.detail


def test_passes_when_triggered_row_has_matching_signal(db_engine):
    """Happy path: row triggered today AND Signal fired today → no drop."""
    target = date.today()
    triggered_today = datetime.combine(target, datetime.min.time()) + timedelta(hours=15, minutes=50)

    with get_session(db_engine) as session:
        session.add(_make_wl(
            "OK", "ep_earnings", "triggered", target - timedelta(days=1),
            stage_changed_at=triggered_today,
        ))
        session.add(Signal(
            ticker="OK", setup_type="ep_earnings_c",
            entry_price=22.0, stop_price=20.46,
            acted_on=True, fired_at=triggered_today,
        ))
        session.commit()

    results = run_checks(target, db_engine, _client(), _config(), log_lines=[])
    check19 = _get_check19(results)
    assert check19 is not None, "Check 19 must always run"
    assert check19.status == "PASS"


def test_passes_when_no_ep_rows(db_engine):
    """No EP rows at all → no drop, no false positive."""
    results = run_checks(date.today(), db_engine, _client(), _config(), log_lines=[])
    check19 = _get_check19(results)
    assert check19 is not None
    assert check19.status == "PASS"


def test_ignores_non_ep_setups(db_engine):
    """Breakout/episodic_pivot ready rows must NOT trip the EP check."""
    target = date.today()
    with get_session(db_engine) as session:
        session.add(_make_wl("BREAK", "breakout", "ready", target))
        session.add(_make_wl("EP", "episodic_pivot", "ready", target))
        session.commit()

    results = run_checks(target, db_engine, _client(), _config(), log_lines=[])
    check19 = _get_check19(results)
    assert check19.status == "PASS"


def test_ignores_triggered_from_other_days(db_engine):
    """Old triggered rows (with stage_changed_at outside target_date) must not cause noise."""
    target = date.today()
    yesterday = datetime.combine(target - timedelta(days=1), datetime.min.time()) + timedelta(hours=15, minutes=50)

    with get_session(db_engine) as session:
        session.add(_make_wl(
            "OLD", "ep_earnings", "triggered", target - timedelta(days=2),
            stage_changed_at=yesterday,
        ))
        session.commit()

    results = run_checks(target, db_engine, _client(), _config(), log_lines=[])
    check19 = _get_check19(results)
    assert check19.status == "PASS"
