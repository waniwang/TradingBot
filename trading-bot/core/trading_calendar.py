"""
US equity-market trading-calendar helpers used to enforce per-variant
scan_date recency on the EP earnings/news execute paths.

Why this exists
---------------
EP setups have a strict execution-day rule baked into the strategy thesis:

* **A / B variants** fire on the SAME day as the gap. ``scan_date`` MUST equal
  ``today`` — otherwise we're entering a stale signal whose entry-at-close
  thesis no longer applies.
* **C variant** fires on day +1 after a day-2 confirmation. ``scan_date`` MUST
  equal the previous trading day — Friday's C row should fire on the
  following Monday, never on Tuesday.

Without per-variant filtering the execute path silently picks up rows that
were never cleared from a prior session, producing late entries on bot or
broker recoveries (e.g. the 2026-05-01 FSS leak: a 4/29-scanned C row that
Alpaca's executor first ran on 5/1 instead of 4/30).

Calendar implementation
-----------------------
We don't depend on a broker client here so the helper works equally for the
Alpaca-side plugin paths AND the IB-side cross-DB reader (which has no
broker handle in scope). ``pandas_market_calendars`` would be the
"correct" answer but is too heavy for this single use; instead we use
weekday arithmetic plus a hardcoded NYSE holiday set covering the current
year and the next. Update ``_US_MARKET_HOLIDAYS`` once a year.

If we ever miss a holiday in the table the worst-case is a C row firing
ONE trading day late — strictly better than the current "fires arbitrarily
late forever" failure mode.
"""
from __future__ import annotations

from datetime import date as Date, timedelta

# NYSE market holidays the equity market is fully closed.
# Source: NYSE 2026/2027 holiday calendar. Day-after-Thanksgiving and
# Christmas Eve are early-close days, NOT closed days, so they're omitted.
# When a federal holiday falls on a weekend, NYSE observes it on the
# nearest weekday per its standard calendar — those observed dates are
# included below (e.g. 2026-07-03 for July 4).
_US_MARKET_HOLIDAYS: frozenset[Date] = frozenset({
    # 2026
    Date(2026, 1, 1),    # New Year's Day
    Date(2026, 1, 19),   # Martin Luther King Jr. Day
    Date(2026, 2, 16),   # Presidents Day
    Date(2026, 4, 3),    # Good Friday
    Date(2026, 5, 25),   # Memorial Day
    Date(2026, 6, 19),   # Juneteenth
    Date(2026, 7, 3),    # Independence Day (observed; July 4 is a Saturday)
    Date(2026, 9, 7),    # Labor Day
    Date(2026, 11, 26),  # Thanksgiving
    Date(2026, 12, 25),  # Christmas

    # 2027
    Date(2027, 1, 1),    # New Year's Day
    Date(2027, 1, 18),   # MLK Day
    Date(2027, 2, 15),   # Presidents Day
    Date(2027, 3, 26),   # Good Friday
    Date(2027, 5, 31),   # Memorial Day
    Date(2027, 6, 18),   # Juneteenth (observed; June 19 is a Saturday)
    Date(2027, 7, 5),    # Independence Day (observed; July 4 is a Sunday)
    Date(2027, 9, 6),    # Labor Day
    Date(2027, 11, 25),  # Thanksgiving
    Date(2027, 12, 24),  # Christmas (observed; December 25 is a Saturday)
})


def is_market_open_date(d: Date) -> bool:
    """Return True iff ``d`` is a US equity-market trading day."""
    if d.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    return d not in _US_MARKET_HOLIDAYS


def previous_trading_day(today: Date) -> Date:
    """Return the most recent trading day STRICTLY BEFORE ``today``.

    Walks backward day-by-day skipping weekends and known holidays.
    Bounded loop (max 10 iterations) to fail loudly rather than spin if
    the holiday table is grossly wrong.
    """
    d = today - timedelta(days=1)
    for _ in range(10):
        if is_market_open_date(d):
            return d
        d -= timedelta(days=1)
    raise RuntimeError(
        f"previous_trading_day: walked back 10 days from {today} without "
        f"finding a trading day — the holiday table likely needs an update."
    )


def valid_scan_dates_for_variant(variant: str, today: Date) -> set[Date]:
    """Return the set of valid ``Watchlist.scan_date`` values for an EP variant.

    * ``A`` / ``B`` → ``{today}`` (same-day execute)
    * ``C`` → ``{previous_trading_day(today)}`` (day +1 execute)
    * Anything else → empty set (caller should reject)

    Variant lookup is case-insensitive. Empty / None returns an empty set
    so unknown rows are filtered out conservatively.
    """
    v = (variant or "").strip().upper()
    if v in ("A", "B"):
        return {today}
    if v == "C":
        return {previous_trading_day(today)}
    return set()


def is_valid_scan_date(variant: str, scan_date: Date, today: Date) -> bool:
    """Convenience wrapper for the common "should this row execute today?" check."""
    return scan_date in valid_scan_dates_for_variant(variant, today)
