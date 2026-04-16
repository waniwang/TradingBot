"""Tests for api.variation.resolve_variation."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
import pytz
from sqlalchemy import create_engine

from api.variation import resolve_variation, resolve_variations_batch
from db.models import Base, Watchlist, get_session


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


def _add_watchlist(session, ticker, setup_type, scan_date, stage, meta):
    session.add(Watchlist(
        ticker=ticker,
        setup_type=setup_type,
        scan_date=scan_date,
        stage=stage,
        metadata_json=json.dumps(meta),
    ))
    session.commit()


def test_strategy_c_from_setup_type_suffix(engine):
    """C is encoded in the setup_type suffix — resolver returns C without any DB join."""
    with get_session(engine) as session:
        assert resolve_variation(session, "AAPL", "ep_earnings_c", date(2026, 4, 16)) == "C"
        assert resolve_variation(session, "AAPL", "ep_news_c", date(2026, 4, 16)) == "C"


def test_non_ep_setup_returns_none(engine):
    """Breakout / episodic_pivot / parabolic_short have no variation concept."""
    with get_session(engine) as session:
        assert resolve_variation(session, "AAPL", "breakout", date(2026, 4, 16)) is None
        assert resolve_variation(session, "AAPL", "episodic_pivot", date(2026, 4, 16)) is None


def test_strategy_a_from_watchlist(engine):
    with get_session(engine) as session:
        _add_watchlist(session, "AAPL", "ep_earnings", date(2026, 4, 16), "triggered",
                       {"ep_strategy": "A"})

    with get_session(engine) as session:
        assert resolve_variation(session, "AAPL", "ep_earnings", date(2026, 4, 16)) == "A"


def test_strategy_b_from_watchlist(engine):
    with get_session(engine) as session:
        _add_watchlist(session, "TSLA", "ep_news", date(2026, 4, 16), "triggered",
                       {"ep_strategy": "B"})

    with get_session(engine) as session:
        assert resolve_variation(session, "TSLA", "ep_news", date(2026, 4, 16)) == "B"


def test_strategy_a_plus_b_when_both_rows_exist(engine):
    """Same ticker passed both A and B filters → two Watchlist rows → 'A+B'."""
    with get_session(engine) as session:
        _add_watchlist(session, "NVDA", "ep_earnings", date(2026, 4, 16), "triggered",
                       {"ep_strategy": "A"})
        _add_watchlist(session, "NVDA", "ep_earnings", date(2026, 4, 16), "triggered",
                       {"ep_strategy": "B"})

    with get_session(engine) as session:
        assert resolve_variation(session, "NVDA", "ep_earnings", date(2026, 4, 16)) == "A+B"


def test_missing_watchlist_row_returns_none(engine):
    """Watchlist row purged/expired: gracefully return None rather than guessing."""
    with get_session(engine) as session:
        assert resolve_variation(session, "GONE", "ep_earnings", date(2026, 4, 16)) is None


def test_utc_datetime_is_converted_to_et_scan_date(engine):
    """Signal.fired_at is UTC-ish; resolver should match the ET calendar day."""
    # A trade fired at 19:50 UTC on 2026-04-16 = 15:50 ET same day (scan_date=2026-04-16).
    with get_session(engine) as session:
        _add_watchlist(session, "MSFT", "ep_earnings", date(2026, 4, 16), "triggered",
                       {"ep_strategy": "A"})

    fired_at = datetime(2026, 4, 16, 19, 50, tzinfo=timezone.utc)
    with get_session(engine) as session:
        assert resolve_variation(session, "MSFT", "ep_earnings", fired_at) == "A"


def test_et_midnight_rollover(engine):
    """A UTC 03:00 ET fired_at (07:00 UTC) still resolves to the ET date."""
    et = pytz.timezone("America/New_York")
    naive_et = datetime(2026, 4, 16, 15, 50)
    localized = et.localize(naive_et)
    with get_session(engine) as session:
        _add_watchlist(session, "CRM", "ep_news", date(2026, 4, 16), "ready",
                       {"ep_strategy": "B"})

    with get_session(engine) as session:
        assert resolve_variation(session, "CRM", "ep_news", localized) == "B"


def test_non_ep_c_suffix_is_not_strategy_c(engine):
    """`_c` suffix on a non-EP setup_type should NOT be treated as Strategy C."""
    with get_session(engine) as session:
        # Hypothetical future strategy that happens to end in `_c`
        assert resolve_variation(session, "AAPL", "breakout_c", date(2026, 4, 16)) is None
        # Also guard against exact-prefix confusion (`ep_earnings` shouldn't be C)
        assert resolve_variation(session, "AAPL", "ep_earnings_extra", date(2026, 4, 16)) is None


def test_batch_equivalent_to_single(engine):
    """Batch resolver returns the same value as single resolver per key."""
    with get_session(engine) as session:
        _add_watchlist(session, "AAPL", "ep_earnings", date(2026, 4, 16), "triggered",
                       {"ep_strategy": "A"})
        _add_watchlist(session, "MSFT", "ep_earnings", date(2026, 4, 16), "triggered",
                       {"ep_strategy": "A"})
        _add_watchlist(session, "MSFT", "ep_earnings", date(2026, 4, 16), "triggered",
                       {"ep_strategy": "B"})

    items = [
        ("AAPL", "ep_earnings", date(2026, 4, 16)),
        ("MSFT", "ep_earnings", date(2026, 4, 16)),
        ("TSLA", "ep_news_c", date(2026, 4, 16)),
        ("GONE", "ep_earnings", date(2026, 4, 16)),
        ("QQQ", "breakout", date(2026, 4, 16)),
    ]

    with get_session(engine) as session:
        batch = resolve_variations_batch(session, items)

    with get_session(engine) as session:
        for ticker, setup_type, as_of in items:
            single = resolve_variation(session, ticker, setup_type, as_of)
            assert batch[(ticker, setup_type, as_of)] == single

    assert batch[("AAPL", "ep_earnings", date(2026, 4, 16))] == "A"
    assert batch[("MSFT", "ep_earnings", date(2026, 4, 16))] == "A+B"
    assert batch[("TSLA", "ep_news_c", date(2026, 4, 16))] == "C"
    assert batch[("GONE", "ep_earnings", date(2026, 4, 16))] is None
    assert batch[("QQQ", "breakout", date(2026, 4, 16))] is None
