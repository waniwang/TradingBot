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
from core.eod import (
    SETUP_LABELS,
    compute_eod_strategy_breakdown,
    compute_eod_r_totals,
    fmt_r_signed,
    fmt_dollar_signed,
)

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


def _seed_closed_position(engine, ticker, opened_at_et, closed_at_et,
                          entry_price, initial_stop_price, shares, realized_pnl):
    """Closed Position with explicit realized_pnl + risk fields for R tests."""
    opened_utc = _et_to_utc_naive(ET.localize(opened_at_et))
    closed_utc = _et_to_utc_naive(ET.localize(closed_at_et))
    with get_session(engine) as session:
        p = Position(
            ticker=ticker,
            setup_type="ep_earnings_b",
            side="long",
            shares=shares,
            entry_price=entry_price,
            stop_price=initial_stop_price,
            initial_stop_price=initial_stop_price,
            opened_at=opened_utc,
            closed_at=closed_utc,
            is_open=False,
            realized_pnl=realized_pnl,
            exit_reason="stop_hit" if realized_pnl < 0 else "trailing_ma_close",
        )
        session.add(p)
        session.commit()


def _seed_open_position(engine, ticker, opened_at_et,
                       entry_price, initial_stop_price, shares):
    opened_utc = _et_to_utc_naive(ET.localize(opened_at_et))
    with get_session(engine) as session:
        p = Position(
            ticker=ticker,
            setup_type="ep_earnings_b",
            side="long",
            shares=shares,
            entry_price=entry_price,
            stop_price=initial_stop_price,
            initial_stop_price=initial_stop_price,
            opened_at=opened_utc,
            is_open=True,
        )
        session.add(p)
        session.commit()


class TestComputeEodRTotals:
    def test_no_positions_returns_zero(self, engine):
        realized_r, unrealized_r = compute_eod_r_totals(date(2026, 5, 11), engine)
        assert realized_r == 0.0
        assert unrealized_r == 0.0

    def test_realized_r_uses_initial_risk(self, engine):
        # entry=100, stop=93 → risk/share=7; shares=100 → total risk=$700.
        # realized=+$700 should give R=+1.0; realized=-$350 should give R=-0.5.
        target = date(2026, 5, 11)
        opened = datetime.combine(target, datetime.min.time()).replace(hour=15, minute=37)
        closed = datetime.combine(target, datetime.min.time()).replace(hour=15, minute=55)
        _seed_closed_position(engine, "AAA", opened, closed, 100.0, 93.0, 100, 700.0)
        _seed_closed_position(engine, "BBB", opened, closed, 100.0, 93.0, 100, -350.0)

        realized_r, unrealized_r = compute_eod_r_totals(target, engine)
        assert realized_r == pytest.approx(0.5)  # +1.0R + -0.5R
        assert unrealized_r == 0.0

    def test_unrealized_r_uses_current_price(self, engine):
        # Open position: entry=100, stop=93, shares=100; current price=110 →
        # unrealized=$1000; R = 1000/700 ≈ 1.4286.
        target = date(2026, 5, 11)
        opened = datetime.combine(target, datetime.min.time()).replace(hour=15, minute=37)
        _seed_open_position(engine, "AAA", opened, 100.0, 93.0, 100)

        realized_r, unrealized_r = compute_eod_r_totals(
            target, engine, current_prices={"AAA": 110.0},
        )
        assert realized_r == 0.0
        assert unrealized_r == pytest.approx(1000.0 / 700.0, rel=1e-4)

    def test_missing_current_price_skips_position(self, engine):
        target = date(2026, 5, 11)
        opened = datetime.combine(target, datetime.min.time()).replace(hour=15, minute=37)
        _seed_open_position(engine, "AAA", opened, 100.0, 93.0, 100)
        _seed_open_position(engine, "BBB", opened, 50.0, 47.0, 200)

        # Only AAA has a price → only AAA contributes.
        _, unrealized_r = compute_eod_r_totals(
            target, engine, current_prices={"AAA": 110.0},
        )
        assert unrealized_r == pytest.approx(1000.0 / 700.0, rel=1e-4)

    def test_zero_risk_position_skipped(self, engine):
        # entry == initial_stop → undefined R → skip silently rather than
        # crash the EOD job on a degenerate row.
        target = date(2026, 5, 11)
        opened = datetime.combine(target, datetime.min.time()).replace(hour=15, minute=37)
        closed = datetime.combine(target, datetime.min.time()).replace(hour=15, minute=55)
        _seed_closed_position(engine, "ZRO", opened, closed, 100.0, 100.0, 100, 50.0)

        realized_r, _ = compute_eod_r_totals(target, engine)
        assert realized_r == 0.0


class TestFormatHelpers:
    def test_fmt_r_signed_explicit_plus_on_positive(self):
        assert fmt_r_signed(2.5) == "+2.50R"
        assert fmt_r_signed(0.0) == "+0.00R"
        assert fmt_r_signed(-1.3) == "-1.30R"

    def test_fmt_dollar_signed_keeps_sign_before_dollar(self):
        assert fmt_dollar_signed(1234.56) == "+$1,234.56"
        assert fmt_dollar_signed(-420.0) == "-$420.00"
        assert fmt_dollar_signed(0.0) == "+$0.00"


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
