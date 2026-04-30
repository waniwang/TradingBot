"""Tests for core.eod.compute_eod_strategy_breakdown.

The function is shared between the Alpaca bot (main.py::job_eod_tasks) and
the IB bot (main_ib.py::job_eod_tasks_ib) so the EOD Telegram summary shape
matches across both. These tests lock in the contract.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
import pytz

from db.models import Base, Order, Position, Watchlist, get_engine, get_session
from core.eod import SETUP_LABELS, compute_eod_strategy_breakdown

ET = pytz.timezone("America/New_York")


@pytest.fixture
def engine():
    eng = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


def _et_to_utc_naive(et_dt: datetime) -> datetime:
    return et_dt.astimezone(pytz.UTC).replace(tzinfo=None)


def _seed_position(engine, ticker, setup_type, opened_at_et, ep_strategy=None,
                   closed_at_et=None, exit_reason=None):
    opened_utc = _et_to_utc_naive(ET.localize(opened_at_et))
    closed_utc = _et_to_utc_naive(ET.localize(closed_at_et)) if closed_at_et else None
    with get_session(engine) as session:
        # Watchlist row carries the ep_strategy variant — the breakdown
        # joins to it via api.variation.resolve_variations_batch.
        if ep_strategy:
            wl = Watchlist(
                ticker=ticker,
                setup_type=setup_type.split("_")[0] + "_" + setup_type.split("_")[1]
                            if "_" in setup_type and setup_type.split("_")[-1] in ("a", "b", "c")
                            else setup_type,
                stage="triggered",
                scan_date=opened_at_et.date(),
            )
            wl.meta = {"ep_strategy": ep_strategy}
            session.add(wl)
        p = Position(
            ticker=ticker,
            setup_type=setup_type,
            side="long",
            shares=100,
            entry_price=100.0,
            stop_price=93.0,
            initial_stop_price=93.0,
            opened_at=opened_utc,
            closed_at=closed_utc,
            exit_reason=exit_reason,
            is_open=closed_utc is None,
        )
        session.add(p)
        session.commit()


def _seed_order(engine, ticker, status, created_at_et):
    created_utc = _et_to_utc_naive(ET.localize(created_at_et))
    with get_session(engine) as session:
        o = Order(
            ticker=ticker, side="buy", order_type="limit",
            qty=10, price=100.0, status=status,
            created_at=created_utc,
        )
        session.add(o)
        session.commit()


class TestComputeEodStrategyBreakdown:
    def test_no_activity_returns_none_label(self, engine):
        line, opened, closed, failed = compute_eod_strategy_breakdown(
            date(2026, 4, 30), engine,
        )
        assert line == "none"
        assert (opened, closed, failed) == (0, 0, 0)

    def test_opened_position_today_counts_and_labels(self, engine):
        target = date(2026, 4, 30)
        _seed_position(engine, "AAA", "ep_earnings_a",
                       datetime.combine(target, datetime.min.time()).replace(hour=15, minute=37),
                       ep_strategy="A")

        line, opened, closed, failed = compute_eod_strategy_breakdown(target, engine)
        assert opened == 1
        assert closed == 0
        assert failed == 0
        assert "EP Earnings" in line
        # Variant label resolution may or may not find a matching Watchlist row
        # via the variation helper; either way, opened count is the contract.

    def test_closed_position_today_counts(self, engine):
        target = date(2026, 4, 30)
        _seed_position(engine, "BBB", "ep_news_c",
                       datetime.combine(target - timedelta(days=2), datetime.min.time()).replace(hour=15, minute=37),
                       ep_strategy="C",
                       closed_at_et=datetime.combine(target, datetime.min.time()).replace(hour=15, minute=55),
                       exit_reason="stop_hit")
        line, opened, closed, failed = compute_eod_strategy_breakdown(target, engine)
        assert opened == 0
        assert closed == 1
        assert line == "none"   # opened=0 → "none" label

    def test_failed_orders_today_counted(self, engine):
        target = date(2026, 4, 30)
        order_time = datetime.combine(target, datetime.min.time()).replace(hour=15, minute=50)
        _seed_order(engine, "AAA", "cancelled", order_time)
        _seed_order(engine, "BBB", "rejected", order_time)
        _seed_order(engine, "CCC", "filled", order_time)  # not failed
        line, opened, closed, failed = compute_eod_strategy_breakdown(target, engine)
        assert failed == 2

    def test_only_today_window(self, engine):
        target = date(2026, 4, 30)
        # Yesterday's open should NOT count
        _seed_position(engine, "AAA", "ep_earnings_a",
                       datetime.combine(target - timedelta(days=1), datetime.min.time()).replace(hour=15, minute=37),
                       ep_strategy="A")
        # Today's open SHOULD count
        _seed_position(engine, "BBB", "ep_earnings_a",
                       datetime.combine(target, datetime.min.time()).replace(hour=15, minute=37),
                       ep_strategy="A")
        line, opened, closed, failed = compute_eod_strategy_breakdown(target, engine)
        assert opened == 1, f"expected only today's BBB to count, got opened={opened}"


class TestSetupLabels:
    def test_covers_a_b_c_suffixes(self):
        # The breakdown looks up label by Position.setup_type which is the
        # strategy-suffixed form ("ep_earnings_a"). All variants must map.
        for suffix in ("a", "b", "c"):
            assert SETUP_LABELS[f"ep_earnings_{suffix}"] == "EP Earnings"
            assert SETUP_LABELS[f"ep_news_{suffix}"] == "EP News"
        # And the bare forms (used by Watchlist.setup_type) map too.
        assert SETUP_LABELS["ep_earnings"] == "EP Earnings"
        assert SETUP_LABELS["ep_news"] == "EP News"
