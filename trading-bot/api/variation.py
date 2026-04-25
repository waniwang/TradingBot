"""Resolve the variation (A/B/C) that triggered an EP trade.

The variation lives in `Watchlist.metadata_json["ep_strategy"]` — it's never
persisted as a first-class column on Signal / Position. For Strategy C we also
encode a distinct setup_type suffix (`_c`) because it has a different max hold
period, but A and B share `setup_type="ep_earnings"` or `"ep_news"`.

This helper joins Signal/Position back to the Watchlist row that staged it,
using `(ticker, base setup_type, scan_date)`. When multiple Watchlist rows
match (same ticker passed both A and B filters), the variants are joined with
`+` to yield "A+B".

Returns:
    "A" | "B" | "A+B" | "C" | None
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

import pytz

from db.models import Watchlist

_ET = pytz.timezone("America/New_York")
_EP_BASES = ("ep_earnings", "ep_news")
# Suffixes that encode the variant directly in setup_type (multi-position support).
# Positions created after the multi-position change use ep_earnings_a/b/c and ep_news_a/b/c.
# Older positions use ep_earnings/ep_news (base) and need a DB lookup.
_SUFFIX_TO_VARIANT: dict[str, str] = {"_a": "A", "_b": "B", "_c": "C"}


def _to_et_date(value: Any) -> date | None:
    """Coerce a date or datetime (in any tz) to an ET calendar date."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # Bot persists naive UTC datetimes
            value = pytz.utc.localize(value)
        return value.astimezone(_ET).date()
    return None


def _classify(setup_type: str) -> tuple[str | None, str | None]:
    """Return (base, direct_variant).

    base is None for non-EP setup types.
    direct_variant is "A"/"B"/"C" when the suffix encodes it (ep_earnings_a → "A"),
    or None when the setup_type is the bare base (ep_earnings) and a DB lookup is needed.
    """
    base = next((b for b in _EP_BASES if setup_type.startswith(b)), None)
    if base is None:
        return None, None
    suffix = setup_type[len(base):]
    direct_variant = _SUFFIX_TO_VARIANT.get(suffix)
    return base, direct_variant


def resolve_variation(
    session,
    ticker: str,
    setup_type: str,
    as_of: Any,
) -> str | None:
    """Single-row variant. Prefer `resolve_variations_batch` inside loops."""
    result = resolve_variations_batch(session, [(ticker, setup_type, as_of)])
    return result[(ticker, setup_type, as_of)]


def resolve_variations_batch(
    session,
    items: Iterable[tuple[str, str, Any]],
) -> dict[tuple[str, str, Any], str | None]:
    """Resolve variation for every (ticker, setup_type, as_of) tuple in one query.

    Use this anywhere you'd otherwise call `resolve_variation` in a loop —
    closed positions lists, signals-today lists, pipeline job-detail signal
    panels — to avoid N+1 round-trips against the Watchlist table.
    """
    items = list(items)
    result: dict[tuple[str, str, Any], str | None] = {}
    # Rows that need a DB lookup: (ticker, base, scan_date, original_key)
    ep_lookups: list[tuple[str, str, date, tuple[str, str, Any]]] = []

    for ticker, setup_type, as_of in items:
        key = (ticker, setup_type, as_of)
        base, direct_variant = _classify(setup_type)

        if base is None:
            result[key] = None
            continue
        if direct_variant is not None:
            # Variant encoded in setup_type suffix (ep_earnings_a/b/c) — no DB lookup needed.
            result[key] = direct_variant
            continue

        # Bare base setup_type (ep_earnings, ep_news) — old positions need a DB join.
        scan_date = _to_et_date(as_of)
        if scan_date is None:
            result[key] = None
            continue
        ep_lookups.append((ticker, base, scan_date, key))

    if not ep_lookups:
        return result

    tickers = list({x[0] for x in ep_lookups})
    bases = list({x[1] for x in ep_lookups})
    scan_dates = list({x[2] for x in ep_lookups})

    rows = (
        session.query(Watchlist)
        .filter(
            Watchlist.ticker.in_(tickers),
            Watchlist.setup_type.in_(bases),
            Watchlist.scan_date.in_(scan_dates),
        )
        .all()
    )

    # Group variants by (ticker, base, scan_date). Keep only A / B — "C" is
    # already handled via the setup_type suffix.
    grouped: dict[tuple[str, str, date], set[str]] = {}
    for r in rows:
        variant = (r.meta or {}).get("ep_strategy")
        if variant not in ("A", "B"):
            continue
        bucket = grouped.setdefault((r.ticker, r.setup_type, r.scan_date), set())
        bucket.add(variant)

    for ticker, base, scan_date, key in ep_lookups:
        variants = sorted(grouped.get((ticker, base, scan_date), set()))
        result[key] = "+".join(variants) if variants else None

    return result
