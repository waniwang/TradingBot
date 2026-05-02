"""Tests for core.trading_calendar.

Locks in the per-variant scan_date contract used by the EP earnings/news
execute paths to reject stale ready rows. Built after the 2026-05-01 FSS
incident where a 4/29-scanned C row leaked into 5/1's executor instead of
firing on 4/30.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.trading_calendar import (
    is_market_open_date,
    is_valid_scan_date,
    previous_trading_day,
    valid_scan_dates_for_variant,
)


class TestIsMarketOpenDate:
    def test_weekday_no_holiday_is_open(self):
        # Tuesday 2026-05-05
        assert is_market_open_date(date(2026, 5, 5)) is True

    def test_saturday_is_closed(self):
        assert is_market_open_date(date(2026, 5, 2)) is False

    def test_sunday_is_closed(self):
        assert is_market_open_date(date(2026, 5, 3)) is False

    def test_known_holiday_is_closed(self):
        # Memorial Day 2026
        assert is_market_open_date(date(2026, 5, 25)) is False

    def test_observed_holiday_is_closed(self):
        # July 4, 2026 falls on Saturday — observed Friday July 3
        assert is_market_open_date(date(2026, 7, 3)) is False


class TestPreviousTradingDay:
    def test_tuesday_returns_monday(self):
        assert previous_trading_day(date(2026, 5, 5)) == date(2026, 5, 4)

    def test_monday_returns_friday(self):
        assert previous_trading_day(date(2026, 5, 4)) == date(2026, 5, 1)

    def test_skips_holiday_back_to_prior_trading_day(self):
        # Day after Memorial Day 2026 (Tue 5/26) → previous trading day is
        # Friday 5/22 (skipping Sat 5/23, Sun 5/24, Memorial Day Mon 5/25).
        assert previous_trading_day(date(2026, 5, 26)) == date(2026, 5, 22)

    def test_after_long_weekend_with_holiday_friday(self):
        # Tuesday after Good Friday 2026 (Fri 4/3) → previous trading day
        # is Thursday 4/2.
        assert previous_trading_day(date(2026, 4, 6)) == date(2026, 4, 2)


class TestValidScanDatesForVariant:
    def test_variant_a_today_only(self):
        today = date(2026, 5, 5)
        assert valid_scan_dates_for_variant("A", today) == {today}

    def test_variant_b_today_only(self):
        today = date(2026, 5, 5)
        assert valid_scan_dates_for_variant("B", today) == {today}

    def test_variant_c_previous_trading_day_only(self):
        # Today = Mon 5/4; previous trading day = Fri 5/1
        assert valid_scan_dates_for_variant("C", date(2026, 5, 4)) == {date(2026, 5, 1)}

    def test_variant_c_handles_holiday_long_weekend(self):
        # Today = Tue after Memorial Day = 5/26; prev trading day = Fri 5/22
        assert valid_scan_dates_for_variant("C", date(2026, 5, 26)) == {date(2026, 5, 22)}

    def test_lowercase_variant_accepted(self):
        today = date(2026, 5, 5)
        assert valid_scan_dates_for_variant("a", today) == {today}
        assert valid_scan_dates_for_variant("c", date(2026, 5, 4)) == {date(2026, 5, 1)}

    def test_unknown_variant_returns_empty_set(self):
        assert valid_scan_dates_for_variant("D", date(2026, 5, 5)) == set()
        assert valid_scan_dates_for_variant("", date(2026, 5, 5)) == set()
        assert valid_scan_dates_for_variant(None, date(2026, 5, 5)) == set()  # type: ignore[arg-type]


class TestIsValidScanDate:
    """Real-world scenarios from the 2026-05-01 cross-check incident."""

    def test_ftai_a_scanned_yesterday_rejected_today(self):
        # FTAI: scan 4/30 (Thu), A variant. IB fired it 5/1 (Fri).
        # Today=5/1 → A requires scan_date == 5/1, so 4/30 → REJECT.
        assert is_valid_scan_date("A", date(2026, 4, 30), today=date(2026, 5, 1)) is False

    def test_fss_c_scanned_two_days_ago_rejected_today(self):
        # FSS: scan 4/29 (Wed), C variant. Alpaca fired it 5/1 (Fri).
        # Today=5/1 → C requires scan_date == prev trading day (4/30), so 4/29 → REJECT.
        assert is_valid_scan_date("C", date(2026, 4, 29), today=date(2026, 5, 1)) is False

    def test_myrg_pwr_c_scanned_yesterday_accepted_today(self):
        # MYRG/PWR: scan 4/30 (Thu), C variant. Both bots fired 5/1 — correct.
        # Today=5/1 → C requires scan_date == 4/30 → ACCEPT.
        assert is_valid_scan_date("C", date(2026, 4, 30), today=date(2026, 5, 1)) is True

    def test_a_variant_same_day_accepted(self):
        # A row scanned today must execute today — accept.
        assert is_valid_scan_date("A", date(2026, 5, 1), today=date(2026, 5, 1)) is True

    def test_c_variant_after_long_weekend(self):
        # Friday 5/22 scan (C variant) → executes Tue 5/26 (after Memorial Day).
        assert is_valid_scan_date("C", date(2026, 5, 22), today=date(2026, 5, 26)) is True
        # But a 5/21 (Thu) scan would NOT execute on 5/26.
        assert is_valid_scan_date("C", date(2026, 5, 21), today=date(2026, 5, 26)) is False
