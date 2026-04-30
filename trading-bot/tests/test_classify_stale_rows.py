"""
Regression tests for scripts/classify_stale_rows.py::_classify.

Why: on 2026-04-30, the classifier mis-tagged 6 real-trade rows as
[bot-failure] CANCELLED because it queried Position by setup_type
"ep_earnings_<a/b/c>" only, missing pre-d7691dc Positions stored as plain
"ep_earnings". These tests lock both naming forms in.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import Base, Order, Position, Watchlist, get_engine, get_session
from scripts.classify_stale_rows import _classify


@pytest.fixture
def db_engine():
    engine = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _make_watchlist(session, *, ticker="GEV", setup_type="ep_earnings",
                    ep_strategy="A", scan_date=None, stage="triggered"):
    scan_date = scan_date or date(2026, 4, 22)
    row = Watchlist(
        ticker=ticker,
        setup_type=setup_type,
        stage=stage,
        scan_date=scan_date,
        added_at=datetime.combine(scan_date, datetime.min.time()),
        meta={"ep_strategy": ep_strategy} if ep_strategy else {},
    )
    session.add(row)
    session.flush()
    return row


def _make_position(session, *, ticker, setup_type, opened_at=None,
                   is_open=True, exit_reason=None):
    opened_at = opened_at or datetime(2026, 4, 22, 19, 50, 0)
    pos = Position(
        ticker=ticker,
        setup_type=setup_type,
        side="long",
        shares=10,
        entry_price=100.0,
        stop_price=93.0,
        initial_stop_price=93.0,
        is_open=is_open,
        opened_at=opened_at,
        exit_reason=exit_reason,
    )
    session.add(pos)
    session.flush()
    return pos


def _make_order(session, *, ticker, status="filled"):
    order = Order(
        ticker=ticker,
        side="buy",
        order_type="limit",
        qty=10,
        price=100.0,
        status=status,
    )
    session.add(order)
    session.flush()
    return order


class TestClassify:
    def test_legacy_setup_type_matches_a_strategy_row(self, db_engine):
        """Pre-d7691dc Position stored as 'ep_earnings' must match an A row.

        This is the regression for the 2026-04-30 incident — 4 still-open
        positions (GEV/TXN/URI/WST) and 2 closed (ARM/MXL) all had legacy
        Position.setup_type='ep_earnings' but the classifier was looking
        for 'ep_earnings_a'.
        """
        with get_session(db_engine) as session:
            row = _make_watchlist(session, ticker="GEV", ep_strategy="A")
            _make_position(session, ticker="GEV", setup_type="ep_earnings")
            session.commit()

            row = session.query(Watchlist).filter_by(ticker="GEV").first()
            verdict, _expl, tag = _classify(row, session)
            assert verdict == "TRADED"
            assert tag is None  # don't add [bot-failure]

    def test_new_setup_type_matches_a_strategy_row(self, db_engine):
        """Post-d7691dc Position stored as 'ep_earnings_a' must still match."""
        with get_session(db_engine) as session:
            row = _make_watchlist(session, ticker="APOG", ep_strategy="B")
            _make_position(session, ticker="APOG", setup_type="ep_earnings_b")
            session.commit()

            row = session.query(Watchlist).filter_by(ticker="APOG").first()
            verdict, _expl, tag = _classify(row, session)
            assert verdict == "TRADED"
            assert tag is None

    def test_legacy_setup_for_ep_news(self, db_engine):
        """Legacy 'ep_news' Position must match an ep_news A row too."""
        with get_session(db_engine) as session:
            row = _make_watchlist(session, ticker="ARM", setup_type="ep_news",
                                  ep_strategy="A", scan_date=date(2026, 4, 24))
            _make_position(session, ticker="ARM", setup_type="ep_news",
                           opened_at=datetime(2026, 4, 24, 19, 55, 0),
                           is_open=False, exit_reason="stop_hit")
            session.commit()

            row = session.query(Watchlist).filter_by(ticker="ARM").first()
            verdict, expl, tag = _classify(row, session)
            assert verdict == "TRADED"
            assert "stop_hit" in expl
            assert tag is None

    def test_no_position_with_order_is_cancelled(self, db_engine):
        """Order present, no Position under either naming → CANCELLED."""
        with get_session(db_engine) as session:
            row = _make_watchlist(session, ticker="MCRI", ep_strategy="A")
            _make_order(session, ticker="MCRI", status="cancelled")
            session.commit()

            row = session.query(Watchlist).filter_by(ticker="MCRI").first()
            verdict, _expl, tag = _classify(row, session)
            assert verdict == "CANCELLED"
            assert tag == "[bot-failure]"

    def test_no_position_no_order_is_cancelled(self, db_engine):
        """Watchlist row marked triggered but nothing executed → CANCELLED."""
        with get_session(db_engine) as session:
            row = _make_watchlist(session, ticker="ZZZZ", ep_strategy="A")
            session.commit()

            row = session.query(Watchlist).filter_by(ticker="ZZZZ").first()
            verdict, _expl, tag = _classify(row, session)
            assert verdict == "CANCELLED"
            assert tag == "[bot-failure]"

    def test_no_ep_strategy_in_meta_is_expired(self, db_engine):
        """Orphan candidate row with no ep_strategy → EXPIRED orphan."""
        with get_session(db_engine) as session:
            row = _make_watchlist(session, ticker="ORPH", ep_strategy=None)
            session.commit()

            row = session.query(Watchlist).filter_by(ticker="ORPH").first()
            verdict, _expl, tag = _classify(row, session)
            assert verdict == "EXPIRED"
            assert tag == "[stale-cleanup]"

    def test_legacy_position_takes_priority_when_both_exist(self, db_engine):
        """If both legacy and suffixed Positions exist, take the most recent.

        Edge case: a multi-position run where a legacy A position from
        before d7691dc closed, and a fresh suffixed-A re-entry on the same
        ticker exists. The classifier should match the more recent one.
        """
        with get_session(db_engine) as session:
            row = _make_watchlist(session, ticker="GEV", ep_strategy="A",
                                  scan_date=date(2026, 4, 22))
            _make_position(session, ticker="GEV", setup_type="ep_earnings",
                           opened_at=datetime(2026, 4, 22, 19, 50))
            _make_position(session, ticker="GEV", setup_type="ep_earnings_a",
                           opened_at=datetime(2026, 4, 28, 19, 50))
            session.commit()

            row = session.query(Watchlist).filter_by(ticker="GEV").first()
            verdict, expl, tag = _classify(row, session)
            assert verdict == "TRADED"
            # Most recent wins (#2, the suffixed one).
            assert "Position #2" in expl
            assert tag is None
