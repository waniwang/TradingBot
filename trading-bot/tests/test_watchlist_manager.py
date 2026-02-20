"""
Unit tests for the unified watchlist manager.

Uses in-memory SQLite and mocked AlpacaClient — no network calls.
"""

import json
import pytest
from datetime import datetime, date, timedelta
from unittest.mock import MagicMock

import pandas as pd

from db.models import Base, Watchlist, BreakoutWatchlist, get_session, get_engine
from scanner.consolidation import classify_consolidation_stage
from scanner.watchlist_manager import (
    persist_candidates,
    promote_ready_to_active,
    expire_stale_active,
    get_active_watchlist,
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
# persist_candidates tests
# ---------------------------------------------------------------------------

class TestPersistCandidates:
    def test_persist_ep_candidates(self, db_engine):
        candidates = [
            {"ticker": "NVDA", "gap_pct": 15.2, "pre_mkt_rvol": 3.5},
            {"ticker": "AAPL", "gap_pct": 8.1, "pre_mkt_rvol": 2.0},
        ]
        count = persist_candidates(candidates, "episodic_pivot", "active", date.today(), db_engine)
        assert count == 2

        with get_session(db_engine) as session:
            rows = session.query(Watchlist).filter_by(setup_type="episodic_pivot").all()
            assert len(rows) == 2
            tickers = {r.ticker for r in rows}
            assert tickers == {"NVDA", "AAPL"}
            for r in rows:
                assert r.stage == "active"
                assert r.scan_date == date.today()
                meta = r.meta
                assert "gap_pct" in meta

    def test_persist_empty_list(self, db_engine):
        count = persist_candidates([], "episodic_pivot", "active", date.today(), db_engine)
        assert count == 0

    def test_persist_breakout_candidates(self, db_engine):
        candidates = [
            {"ticker": "MSFT", "consolidation_days": 25, "atr_ratio": 0.7, "rs_composite": 65.0},
        ]
        count = persist_candidates(candidates, "breakout", "watching", date.today(), db_engine)
        assert count == 1

        with get_session(db_engine) as session:
            row = session.query(Watchlist).first()
            assert row.setup_type == "breakout"
            assert row.meta["consolidation_days"] == 25


# ---------------------------------------------------------------------------
# promote_ready_to_active tests
# ---------------------------------------------------------------------------

class TestPromoteReadyToActive:
    def test_promotes_ready_breakout_to_active(self, db_engine):
        today = date.today()
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="AAPL", setup_type="breakout", stage="ready",
                scan_date=today - timedelta(days=1),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        promoted = promote_ready_to_active(today, db_engine)
        assert promoted == 1

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="AAPL").first()
            assert row.stage == "active"
            assert row.scan_date == today

    def test_does_not_promote_watching(self, db_engine):
        today = date.today()
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="MSFT", setup_type="breakout", stage="watching",
                scan_date=today - timedelta(days=1),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        promoted = promote_ready_to_active(today, db_engine)
        assert promoted == 0


# ---------------------------------------------------------------------------
# expire_stale_active tests
# ---------------------------------------------------------------------------

class TestExpireStaleActive:
    def test_expires_old_ep_active(self, db_engine):
        today = date.today()
        yesterday = today - timedelta(days=1)
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="NVDA", setup_type="episodic_pivot", stage="active",
                scan_date=yesterday,
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        count = expire_stale_active(today, db_engine)
        assert count == 1

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="NVDA").first()
            assert row.stage == "expired"

    def test_demotes_old_breakout_active_to_ready(self, db_engine):
        today = date.today()
        yesterday = today - timedelta(days=1)
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="AAPL", setup_type="breakout", stage="active",
                scan_date=yesterday,
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        count = expire_stale_active(today, db_engine)
        assert count == 1

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="AAPL").first()
            assert row.stage == "ready"

    def test_keeps_todays_active(self, db_engine):
        today = date.today()
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="TSLA", setup_type="episodic_pivot", stage="active",
                scan_date=today,
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        count = expire_stale_active(today, db_engine)
        assert count == 0

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="TSLA").first()
            assert row.stage == "active"


# ---------------------------------------------------------------------------
# get_active_watchlist tests
# ---------------------------------------------------------------------------

class TestGetActiveWatchlist:
    def test_returns_active_entries_as_dicts(self, db_engine):
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="NVDA", setup_type="episodic_pivot", stage="active",
                scan_date=date.today(),
                metadata_json=json.dumps({"gap_pct": 15.2}),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(Watchlist(
                ticker="AAPL", setup_type="breakout", stage="active",
                scan_date=date.today(),
                metadata_json=json.dumps({"consolidation_days": 25}),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(Watchlist(
                ticker="MSFT", setup_type="breakout", stage="watching",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        result = get_active_watchlist(db_engine)
        assert len(result) == 2
        tickers = {r["ticker"] for r in result}
        assert tickers == {"NVDA", "AAPL"}
        nvda = next(r for r in result if r["ticker"] == "NVDA")
        assert nvda["setup_type"] == "episodic_pivot"
        assert nvda["gap_pct"] == 15.2

    def test_empty_when_no_active(self, db_engine):
        result = get_active_watchlist(db_engine)
        assert result == []


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
            rows = session.query(Watchlist).filter_by(setup_type="breakout").all()
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
            row = session.query(Watchlist).first()
            assert row.stage == "watching"

    def test_skip_insert_for_failed(self, db_engine):
        analyses = {"BAD": _make_analysis("BAD", qualifies=False,
                                          has_prior_move=False, atr_contracting=False,
                                          atr_ratio=1.2)}
        counts = _update_watchlist_db(analyses, db_engine)

        assert counts["new"] == 0
        with get_session(db_engine) as session:
            assert session.query(Watchlist).count() == 0

    def test_update_existing_watching_to_ready(self, db_engine):
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="TSLA", setup_type="breakout", stage="watching",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        analyses = {"TSLA": _make_analysis("TSLA", qualifies=True)}
        counts = _update_watchlist_db(analyses, db_engine)

        assert counts["updated"] == 1
        assert counts["ready"] == 1

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="TSLA").first()
            assert row.stage == "ready"

    def test_update_existing_to_failed(self, db_engine):
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="FAIL", setup_type="breakout", stage="watching",
                scan_date=date.today(),
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
            row = session.query(Watchlist).filter_by(ticker="FAIL").first()
            assert row.stage == "failed"

    def test_does_not_update_terminal_entries(self, db_engine):
        """Triggered entries should not be modified by new scans."""
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="DONE", setup_type="breakout", stage="triggered",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        analyses = {"DONE": _make_analysis("DONE", qualifies=True)}
        counts = _update_watchlist_db(analyses, db_engine)

        with get_session(db_engine) as session:
            rows = session.query(Watchlist).filter_by(ticker="DONE").all()
            stages = {r.stage for r in rows}
            assert "triggered" in stages


# ---------------------------------------------------------------------------
# _age_out_stale tests
# ---------------------------------------------------------------------------

class TestAgeOutStale:
    def test_ages_old_watching_entries(self, db_engine):
        old_date = datetime.utcnow() - timedelta(days=50)
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="OLD", setup_type="breakout", stage="watching",
                scan_date=old_date.date(),
                added_at=old_date, updated_at=old_date,
                stage_changed_at=old_date,
            ))
            session.commit()

        count = _age_out_stale(db_engine, max_days=45)
        assert count == 1

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="OLD").first()
            assert row.stage == "failed"
            assert row.notes == "stale_aged_out"

    def test_keeps_recent_watching(self, db_engine):
        recent = datetime.utcnow() - timedelta(days=10)
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="NEW", setup_type="breakout", stage="watching",
                scan_date=recent.date(),
                added_at=recent, updated_at=recent,
                stage_changed_at=recent,
            ))
            session.commit()

        count = _age_out_stale(db_engine, max_days=45)
        assert count == 0

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="NEW").first()
            assert row.stage == "watching"

    def test_does_not_age_ready(self, db_engine):
        old_date = datetime.utcnow() - timedelta(days=50)
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="READY", setup_type="breakout", stage="ready",
                scan_date=old_date.date(),
                added_at=old_date, updated_at=old_date,
                stage_changed_at=old_date,
            ))
            session.commit()

        count = _age_out_stale(db_engine, max_days=45)
        assert count == 0

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="READY").first()
            assert row.stage == "ready"


# ---------------------------------------------------------------------------
# get_ready_candidates tests
# ---------------------------------------------------------------------------

class TestGetReadyCandidates:
    def test_returns_ready_entries(self, db_engine):
        meta = {
            "consolidation_days": 30, "atr_ratio": 0.75,
            "higher_lows": True, "near_10d_ma": True,
            "near_20d_ma": True, "volume_drying": True,
            "rs_composite": 65.0,
        }
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="RDY", setup_type="breakout", stage="ready",
                scan_date=date.today(),
                metadata_json=json.dumps(meta),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(Watchlist(
                ticker="WATCH", setup_type="breakout", stage="watching",
                scan_date=date.today(),
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
    def test_transitions_active_to_triggered(self, db_engine):
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="FIRE", setup_type="episodic_pivot", stage="active",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        result = mark_triggered("FIRE", db_engine)
        assert result is True

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="FIRE").first()
            assert row.stage == "triggered"

    def test_transitions_ready_to_triggered(self, db_engine):
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="RDY", setup_type="breakout", stage="ready",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        result = mark_triggered("RDY", db_engine)
        assert result is True

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="RDY").first()
            assert row.stage == "triggered"

    def test_returns_false_for_nonexistent(self, db_engine):
        result = mark_triggered("NOPE", db_engine)
        assert result is False

    def test_returns_false_for_watching(self, db_engine):
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="WATCH", setup_type="breakout", stage="watching",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        result = mark_triggered("WATCH", db_engine)
        assert result is False

    def test_filter_by_setup_type(self, db_engine):
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="MULTI", setup_type="episodic_pivot", stage="active",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(Watchlist(
                ticker="MULTI", setup_type="breakout", stage="active",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        result = mark_triggered("MULTI", db_engine, setup_type="episodic_pivot")
        assert result is True

        with get_session(db_engine) as session:
            ep = session.query(Watchlist).filter_by(
                ticker="MULTI", setup_type="episodic_pivot"
            ).first()
            bo = session.query(Watchlist).filter_by(
                ticker="MULTI", setup_type="breakout"
            ).first()
            assert ep.stage == "triggered"
            assert bo.stage == "active"  # untouched


# ---------------------------------------------------------------------------
# get_pipeline_counts tests
# ---------------------------------------------------------------------------

class TestGetPipelineCounts:
    def test_counts(self, db_engine):
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="A", setup_type="breakout", stage="ready",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(Watchlist(
                ticker="B", setup_type="breakout", stage="watching",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(Watchlist(
                ticker="C", setup_type="breakout", stage="watching",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(Watchlist(
                ticker="D", setup_type="episodic_pivot", stage="active",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.add(Watchlist(
                ticker="E", setup_type="breakout", stage="triggered",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        counts = get_pipeline_counts(db_engine)
        assert counts["ready"] == 1
        assert counts["watching"] == 2
        assert counts["active"] == 1


# ---------------------------------------------------------------------------
# EP lifecycle test (single-day)
# ---------------------------------------------------------------------------

class TestEPLifecycle:
    def test_ep_full_lifecycle(self, db_engine):
        """EP: persist as active -> trigger or expire at EOD."""
        today = date.today()

        # Persist EP candidate
        candidates = [{"ticker": "NVDA", "gap_pct": 15.2}]
        persist_candidates(candidates, "episodic_pivot", "active", today, db_engine)

        # Verify active
        active = get_active_watchlist(db_engine)
        assert len(active) == 1
        assert active[0]["ticker"] == "NVDA"
        assert active[0]["gap_pct"] == 15.2

        # Mark triggered
        assert mark_triggered("NVDA", db_engine, setup_type="episodic_pivot") is True

        # No longer in active list
        active = get_active_watchlist(db_engine)
        assert len(active) == 0

    def test_ep_expires_next_day(self, db_engine):
        """EP active from yesterday gets expired."""
        yesterday = date.today() - timedelta(days=1)
        persist_candidates(
            [{"ticker": "AAPL", "gap_pct": 10.0}],
            "episodic_pivot", "active", yesterday, db_engine,
        )

        expire_stale_active(date.today(), db_engine)

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="AAPL").first()
            assert row.stage == "expired"


# ---------------------------------------------------------------------------
# Breakout lifecycle test (multi-day)
# ---------------------------------------------------------------------------

class TestBreakoutLifecycle:
    def test_breakout_full_lifecycle(self, db_engine):
        """Breakout: watching -> ready -> active -> triggered."""
        today = date.today()

        # Nightly scan inserts as watching
        analyses = {"MSFT": _make_analysis("MSFT", qualifies=False,
                                           has_prior_move=True, atr_contracting=True)}
        _update_watchlist_db(analyses, db_engine)

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="MSFT").first()
            assert row.stage == "watching"

        # Next nightly scan upgrades to ready
        analyses = {"MSFT": _make_analysis("MSFT", qualifies=True)}
        _update_watchlist_db(analyses, db_engine)

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="MSFT").first()
            assert row.stage == "ready"

        # Morning premarket promotes to active
        promote_ready_to_active(today, db_engine)

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="MSFT").first()
            assert row.stage == "active"

        # Signal fires -> triggered
        mark_triggered("MSFT", db_engine, setup_type="breakout")

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="MSFT").first()
            assert row.stage == "triggered"

    def test_breakout_active_demotes_to_ready_next_day(self, db_engine):
        """Breakout active from yesterday gets demoted back to ready."""
        yesterday = date.today() - timedelta(days=1)
        with get_session(db_engine) as session:
            session.add(Watchlist(
                ticker="TSLA", setup_type="breakout", stage="active",
                scan_date=yesterday,
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            ))
            session.commit()

        expire_stale_active(date.today(), db_engine)

        with get_session(db_engine) as session:
            row = session.query(Watchlist).filter_by(ticker="TSLA").first()
            assert row.stage == "ready"


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
        with get_session(db_engine) as session:
            total = session.query(Watchlist).count()
        assert isinstance(summary.get("new", 0), int)
        assert isinstance(summary.get("aged_out", 0), int)


# ---------------------------------------------------------------------------
# Watchlist model tests
# ---------------------------------------------------------------------------

class TestWatchlistModel:
    def test_meta_property(self, db_engine):
        with get_session(db_engine) as session:
            row = Watchlist(
                ticker="TEST", setup_type="breakout", stage="watching",
                scan_date=date.today(),
                metadata_json=json.dumps({"atr_ratio": 0.75, "higher_lows": True}),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            )
            session.add(row)
            session.commit()

            fetched = session.query(Watchlist).filter_by(ticker="TEST").first()
            assert fetched.meta == {"atr_ratio": 0.75, "higher_lows": True}

    def test_meta_empty_when_null(self, db_engine):
        with get_session(db_engine) as session:
            row = Watchlist(
                ticker="NULL", setup_type="breakout", stage="watching",
                scan_date=date.today(),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            )
            session.add(row)
            session.commit()

            fetched = session.query(Watchlist).filter_by(ticker="NULL").first()
            assert fetched.meta == {}

    def test_to_dict_merges_meta(self, db_engine):
        with get_session(db_engine) as session:
            row = Watchlist(
                ticker="DICT", setup_type="episodic_pivot", stage="active",
                scan_date=date.today(),
                metadata_json=json.dumps({"gap_pct": 12.5}),
                added_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            )
            session.add(row)
            session.commit()

            fetched = session.query(Watchlist).filter_by(ticker="DICT").first()
            d = fetched.to_dict()
            assert d["ticker"] == "DICT"
            assert d["setup_type"] == "episodic_pivot"
            assert d["gap_pct"] == 12.5

    def test_days_on_list(self, db_engine):
        with get_session(db_engine) as session:
            row = Watchlist(
                ticker="AGE", setup_type="breakout", stage="watching",
                scan_date=date.today(),
                added_at=datetime.utcnow() - timedelta(days=5),
                updated_at=datetime.utcnow(),
                stage_changed_at=datetime.utcnow(),
            )
            session.add(row)
            session.commit()

            fetched = session.query(Watchlist).filter_by(ticker="AGE").first()
            assert fetched.days_on_list == 5
