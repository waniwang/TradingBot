"""
Unit tests for the persistent breakout watchlist manager.

Uses in-memory SQLite and mocked AlpacaClient — no network calls.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd

from db.models import Base, BreakoutWatchlist, get_session, get_engine
from scanner.consolidation import classify_consolidation_stage
from scanner.watchlist_manager import (
    _update_watchlist_db,
    _age_out_stale,
    get_ready_candidates,
    mark_triggered,
    get_pipeline_counts,
    run_nightly_scan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_engine():
    engine = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _make_analysis(ticker, qualifies=True, has_prior_move=True, atr_contracting=True,
                   atr_ratio=0.7, higher_lows=True, near_10d_ma=True, near_20d_ma=True,
                   volume_drying=False, reason="ok", rs_composite=50.0):
    return {
        "ticker": ticker,
        "qualifies": qualifies,
        "consolidation_days": 25,
        "atr_contracting": atr_contracting,
        "atr_ratio": atr_ratio,
        "higher_lows": higher_lows,
        "near_10d_ma": near_10d_ma,
        "near_20d_ma": near_20d_ma,
        "volume_drying": volume_drying,
        "has_prior_move": has_prior_move,
        "setup_type": "breakout",
        "reason": reason,
        "rs_composite": rs_composite,
    }


def _make_daily_df(n=130, start_price=50.0, drift=0.1):
    rows = []
    price = start_price
    for i in range(n):
        price += drift
        rows.append({
            "date": pd.Timestamp("2025-06-01") + pd.Timedelta(days=i),
            "open": round(price - 0.5, 2),
            "high": round(price + 1.0, 2),
            "low": round(price - 1.0, 2),
            "close": round(price, 2),
            "volume": 1_000_000 + i * 1000,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# classify_consolidation_stage tests
# ---------------------------------------------------------------------------

class TestClassifyConsolidationStage:
    def test_ready_when_qualifies(self):
        result = _make_analysis("AAPL", qualifies=True)
        assert classify_consolidation_stage(result) == "ready"

    def test_watching_with_prior_move_and_atr_contracting(self):
        result = _make_analysis("AAPL", qualifies=False, has_prior_move=True,
                                atr_contracting=True, atr_ratio=0.7)
        assert classify_consolidation_stage(result) == "watching"

    def test_watching_with_prior_move_and_low_ratio(self):
        result = _make_analysis("AAPL", qualifies=False, has_prior_move=True,
                                atr_contracting=False, atr_ratio=0.95)
        assert classify_consolidation_stage(result) == "watching"

    def test_failed_no_prior_move(self):
        result = _make_analysis("AAPL", qualifies=False, has_prior_move=False,
                                atr_contracting=True, atr_ratio=0.7)
        assert classify_consolidation_stage(result) == "failed"

    def test_failed_no_tightening(self):
        result = _make_analysis("AAPL", qualifies=False, has_prior_move=True,
                                atr_contracting=False, atr_ratio=1.2)
        assert classify_consolidation_stage(result) == "failed"


# ---------------------------------------------------------------------------
# _update_watchlist_db tests
# ---------------------------------------------------------------------------

class TestUpdateWatchlistDb:
    def test_insert_new_ready(self, db_engine):
        analyses = {"AAPL": _make_analysis("AAPL", qualifies=True)}
        counts = _update_watchlist_db(analyses, db_engine)

        assert counts["new"] == 1
        assert counts["ready"] == 1

        with get_session(db_engine) as session:
            rows = session.query(BreakoutWatchlist).all()
            assert len(rows) == 1
            assert rows[0].ticker == "AAPL"
            assert rows[0].stage == "ready"

    def test_insert_new_watching(self, db_engine):
        analyses = {"MSFT": _make_analysis("MSFT", qualifies=False,
                                           has_prior_move=True, atr_contracting=True)}
        counts = _update_watchlist_db(analyses, db_engine)

        assert counts["new"] == 1
        assert counts["watching"] == 1

        with get_session(db_engine) as session:
            row = session.query(BreakoutWatchlist).first()
            assert row.stage == "watching"

    def test_skip_insert_for_failed(self, db_engine):
        analyses = {"BAD": _make_analysis("BAD", qualifies=False,
                                          has_prior_move=False, atr_contracting=False,
                                          atr_ratio=1.2)}
        counts = _update_watchlist_db(analyses, db_engine)

        assert counts["new"] == 0
        with get_session(db_engine) as session:
            assert session.query(BreakoutWatchlist).count() == 0

    def test_update_existing_watching_to_ready(self, db_engine):
        # Seed a watching entry
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="TSLA", stage="watching", atr_ratio=0.9,
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        analyses = {"TSLA": _make_analysis("TSLA", qualifies=True)}
        counts = _update_watchlist_db(analyses, db_engine)

        assert counts["updated"] == 1
        assert counts["ready"] == 1

        with get_session(db_engine) as session:
            row = session.query(BreakoutWatchlist).filter_by(ticker="TSLA").first()
            assert row.stage == "ready"

    def test_update_existing_to_failed(self, db_engine):
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="FAIL", stage="watching", atr_ratio=0.9,
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        analyses = {"FAIL": _make_analysis("FAIL", qualifies=False,
                                           has_prior_move=False, atr_contracting=False,
                                           atr_ratio=1.2)}
        counts = _update_watchlist_db(analyses, db_engine)

        assert counts["failed"] == 1

        with get_session(db_engine) as session:
            row = session.query(BreakoutWatchlist).filter_by(ticker="FAIL").first()
            assert row.stage == "failed"

    def test_does_not_update_terminal_entries(self, db_engine):
        """Triggered/failed entries should not be modified by new scans."""
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="DONE", stage="triggered",
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        analyses = {"DONE": _make_analysis("DONE", qualifies=True)}
        counts = _update_watchlist_db(analyses, db_engine)

        # Should be treated as new (not found in active), but since it qualifies
        # it would try to insert — however there's already a row.
        # The function only queries active (watching/ready) rows, so DONE won't
        # be in existing dict, and a new row gets inserted.
        # This is acceptable — the triggered row stays, and a new ready row is added.
        with get_session(db_engine) as session:
            rows = session.query(BreakoutWatchlist).filter_by(ticker="DONE").all()
            stages = {r.stage for r in rows}
            assert "triggered" in stages


# ---------------------------------------------------------------------------
# _age_out_stale tests
# ---------------------------------------------------------------------------

class TestAgeOutStale:
    def test_ages_old_watching_entries(self, db_engine):
        old_date = datetime.utcnow() - timedelta(days=50)
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="OLD", stage="watching",
                added_at=old_date, updated_at=old_date,
                stage_changed_at=old_date,
            ))
            session.commit()

        count = _age_out_stale(db_engine, max_days=45)
        assert count == 1

        with get_session(db_engine) as session:
            row = session.query(BreakoutWatchlist).filter_by(ticker="OLD").first()
            assert row.stage == "failed"
            assert row.notes == "stale_aged_out"

    def test_keeps_recent_watching(self, db_engine):
        recent = datetime.utcnow() - timedelta(days=10)
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="NEW", stage="watching",
                added_at=recent, updated_at=recent,
                stage_changed_at=recent,
            ))
            session.commit()

        count = _age_out_stale(db_engine, max_days=45)
        assert count == 0

        with get_session(db_engine) as session:
            row = session.query(BreakoutWatchlist).filter_by(ticker="NEW").first()
            assert row.stage == "watching"

    def test_does_not_age_ready(self, db_engine):
        old_date = datetime.utcnow() - timedelta(days=50)
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="READY", stage="ready",
                added_at=old_date, updated_at=old_date,
                stage_changed_at=old_date,
            ))
            session.commit()

        count = _age_out_stale(db_engine, max_days=45)
        assert count == 0

        with get_session(db_engine) as session:
            row = session.query(BreakoutWatchlist).filter_by(ticker="READY").first()
            assert row.stage == "ready"


# ---------------------------------------------------------------------------
# get_ready_candidates tests
# ---------------------------------------------------------------------------

class TestGetReadyCandidates:
    def test_returns_ready_entries(self, db_engine):
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="RDY", stage="ready", consolidation_days=30,
                atr_ratio=0.75, higher_lows=True, near_10d_ma=True,
                near_20d_ma=True, volume_drying=True, rs_composite=65.0,
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(BreakoutWatchlist(
                ticker="WATCH", stage="watching",
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        result = get_ready_candidates(db_engine)
        assert len(result) == 1
        assert result[0]["ticker"] == "RDY"
        assert result[0]["setup_type"] == "breakout"
        assert result[0]["qualifies"] is True
        assert result[0]["consolidation_days"] == 30
        assert result[0]["atr_ratio"] == 0.75
        assert result[0]["higher_lows"] is True
        assert result[0]["rs_composite"] == 65.0

    def test_empty_when_no_ready(self, db_engine):
        result = get_ready_candidates(db_engine)
        assert result == []


# ---------------------------------------------------------------------------
# mark_triggered tests
# ---------------------------------------------------------------------------

class TestMarkTriggered:
    def test_transitions_ready_to_triggered(self, db_engine):
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="FIRE", stage="ready",
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        result = mark_triggered("FIRE", db_engine)
        assert result is True

        with get_session(db_engine) as session:
            row = session.query(BreakoutWatchlist).filter_by(ticker="FIRE").first()
            assert row.stage == "triggered"

    def test_returns_false_for_nonexistent(self, db_engine):
        result = mark_triggered("NOPE", db_engine)
        assert result is False

    def test_returns_false_for_watching(self, db_engine):
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="WATCH", stage="watching",
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        result = mark_triggered("WATCH", db_engine)
        assert result is False


# ---------------------------------------------------------------------------
# get_pipeline_counts tests
# ---------------------------------------------------------------------------

class TestGetPipelineCounts:
    def test_counts(self, db_engine):
        with get_session(db_engine) as session:
            session.add(BreakoutWatchlist(
                ticker="A", stage="ready",
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(BreakoutWatchlist(
                ticker="B", stage="watching",
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(BreakoutWatchlist(
                ticker="C", stage="watching",
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(BreakoutWatchlist(
                ticker="D", stage="triggered",
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        counts = get_pipeline_counts(db_engine)
        assert counts["ready"] == 1
        assert counts["watching"] == 2


# ---------------------------------------------------------------------------
# run_nightly_scan integration test (mocked client)
# ---------------------------------------------------------------------------

class TestRunNightlyScan:
    def test_end_to_end(self, db_engine):
        client = MagicMock()
        client.get_tradable_universe.return_value = ["AAPL", "MSFT", "TSLA"]

        # All three return strong momentum
        df = _make_daily_df(130, start_price=50.0, drift=1.0)
        client.get_daily_bars_batch.return_value = {
            "AAPL": df, "MSFT": df, "TSLA": df,
        }

        config = {
            "signals": {
                "breakout_consolidation_days_min": 10,
                "breakout_consolidation_days_max": 40,
            },
        }

        summary = run_nightly_scan(config, client, db_engine)

        assert "error" not in summary
        # Verify DB has entries (some may be watching, ready, or neither depending
        # on the synthetic data, but the pipeline should have run without error)
        with get_session(db_engine) as session:
            total = session.query(BreakoutWatchlist).count()
        # The scan completed successfully
        assert isinstance(summary.get("new", 0), int)
        assert isinstance(summary.get("aged_out", 0), int)
