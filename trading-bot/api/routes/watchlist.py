"""Watchlist endpoint — pipeline stages with setup-specific metadata."""

from __future__ import annotations

from fastapi import APIRouter

from db.models import Order, Position, Watchlist, get_session
from api.deps import get_db_engine, get_enabled_strategies

router = APIRouter()

# Tag written into Watchlist.notes by day2_confirm when the snapshot fetch errors
# or returns no data. Lets the dashboard split expired rows into "Expired"
# (legitimate price rejection) vs "Cancelled" (bot/broker failure).
BOT_FAILURE_TAG = "[bot-failure]"


def _iso(dt) -> str | None:
    """Serialize a naive-UTC datetime as an ISO string with a 'Z' suffix so JS
    clients parse it as UTC rather than local time."""
    if dt is None:
        return None
    s = dt.isoformat()
    return s if s.endswith("Z") or "+" in s[10:] else s + "Z"


def _format_candidate(row: Watchlist) -> dict:
    meta = row.meta
    # EP swing variation (A/B/A+B/C). C candidates at scan time don't have
    # ep_strategy set yet — they're parked in stage=watching awaiting day-2
    # confirm, which is what makes them Strategy C.
    variation = None
    if row.setup_type in ("ep_earnings", "ep_news"):
        variation = meta.get("ep_strategy")
        if variation is None and row.stage == "watching":
            variation = "C"

    base = {
        "id": row.id,
        "ticker": row.ticker,
        "setup": row.setup_type.replace("_", " ").title(),
        "setup_raw": row.setup_type,
        "stage": row.stage.upper(),
        "variation": variation,
        "scan_date": str(row.scan_date),
        "added_at": _iso(row.added_at),
        "stage_changed_at": _iso(row.stage_changed_at),
        "updated_at": _iso(row.updated_at),
    }

    if row.setup_type in ("episodic_pivot", "ep_earnings", "ep_news"):
        gap = meta.get("gap_pct")
        base["gap_pct"] = round(gap, 1) if gap else None
        rvol = meta.get("pre_mkt_rvol")
        base["pre_mkt_rvol"] = round(rvol, 1) if rvol else None
        base["consolidation_days"] = None
        base["atr_ratio"] = None
        base["rs_score"] = None
        base["quality_flags"] = []
    elif row.setup_type == "breakout":
        base["gap_pct"] = None
        base["pre_mkt_rvol"] = None
        base["consolidation_days"] = meta.get("consolidation_days")
        atr = meta.get("atr_ratio")
        base["atr_ratio"] = round(atr, 3) if atr else None
        rs = meta.get("rs_composite")
        base["rs_score"] = round(rs, 1) if rs else None

        flags = []
        if meta.get("higher_lows"):
            flags.append("Higher Lows")
        if meta.get("volume_drying"):
            flags.append("Vol Dry")
        if meta.get("near_10d_ma"):
            flags.append("Near 10d MA")
        if meta.get("near_20d_ma"):
            flags.append("Near 20d MA")
        base["quality_flags"] = flags
    else:
        base["gap_pct"] = None
        base["pre_mkt_rvol"] = None
        base["consolidation_days"] = None
        base["atr_ratio"] = None
        base["rs_score"] = None
        base["quality_flags"] = []

    return base


def _ticker_has_position(session, ticker: str, setup_type: str) -> bool:
    """True if a Position ever existed for this ticker+setup (open or closed)."""
    return session.query(Position).filter(
        Position.ticker == ticker,
        Position.setup_type == setup_type,
    ).first() is not None


def _is_execution_row(row: Watchlist) -> bool:
    """
    Filter out scan-pool candidate rows from the terminal-state buckets.

    EP swing scans persist two rows per A/B entry — a stage="active" candidate
    snapshot and a stage="ready" execution row. The old mark_triggered bug
    (fixed 2026-04-23) flipped the "active" row to "triggered", leaving
    candidate-pool artifacts in the Filled/Cancelled views. Real execution
    rows always have meta["ep_strategy"] set.

    Non-EP strategies (breakout, episodic_pivot) don't use ep_strategy, so
    they pass through unchanged.
    """
    if row.setup_type not in ("ep_earnings", "ep_news"):
        return True
    return (row.meta or {}).get("ep_strategy") is not None


def _latest_order_status(session, ticker: str) -> str | None:
    """Most recent Order status for this ticker (across all signals)."""
    order = (
        session.query(Order)
        .filter(Order.ticker == ticker)
        .order_by(Order.created_at.desc())
        .first()
    )
    return order.status if order else None


@router.get("/watchlist")
def get_watchlist():
    engine = get_db_engine()
    enabled = get_enabled_strategies()

    with get_session(engine) as session:
        enabled_tuple = tuple(enabled)
        # Server-side sort: most recently added first. Each pipeline tab on the
        # dashboard shows newest-on-top so an operator scanning the page sees
        # today's activity above older rows.
        base_query = (
            session.query(Watchlist)
            .filter(Watchlist.setup_type.in_(enabled_tuple))
            .order_by(Watchlist.added_at.desc())
        )

        active = base_query.filter(Watchlist.stage == "active").all()
        ready = base_query.filter(Watchlist.stage == "ready").all()
        watching = base_query.filter(Watchlist.stage == "watching").all()
        triggered = base_query.filter(Watchlist.stage == "triggered").all()
        expired_all = base_query.filter(Watchlist.stage == "expired").all()

        # Derive filled / cancelled from triggered rows + Order/Position state.
        # Skip candidate-pool artifacts (see _is_execution_row).
        filled: list[Watchlist] = []
        cancelled_from_triggered: list[Watchlist] = []
        for row in triggered:
            if not _is_execution_row(row):
                continue
            if _ticker_has_position(session, row.ticker, row.setup_type):
                filled.append(row)
            elif _latest_order_status(session, row.ticker) in ("cancelled", "rejected"):
                cancelled_from_triggered.append(row)
            else:
                # Triggered + no position + no terminal-failure order = in-flight
                # (submitted/pending). Show in Filled optimistically; it'll flip
                # once the fill comes through (or to Cancelled on timeout).
                filled.append(row)

        # Split expired: [bot-failure] tag → Cancelled; everything else → Expired.
        cancelled_from_expired: list[Watchlist] = []
        expired: list[Watchlist] = []
        for row in expired_all:
            if not _is_execution_row(row):
                continue
            notes = row.notes or ""
            if BOT_FAILURE_TAG in notes:
                cancelled_from_expired.append(row)
            else:
                expired.append(row)

        cancelled = cancelled_from_triggered + cancelled_from_expired

    # Dedup: active wins over ready; ready wins over watching — but only suppress
    # watching rows that are NOT day-2-confirm C candidates. EP strategies
    # intentionally persist both an active pool row AND a watching row for C
    # candidates. Suppressing watching on active-ticker presence would hide C
    # candidates from the dashboard. day2_confirm rows are always shown.
    active_tickers = {r.ticker for r in active}
    ready_filtered = [r for r in ready if r.ticker not in active_tickers]
    shown_tickers = active_tickers | {r.ticker for r in ready}
    watching_filtered = [
        r for r in watching
        if r.ticker not in shown_tickers or (r.meta or {}).get("day2_confirm")
    ]

    # Final safety: explicitly resort every list newest-first. The base query
    # already orders by added_at desc, but `cancelled` is a concat of two such
    # lists which loses the global ordering.
    def _newest_first(rows):
        return sorted(rows, key=lambda r: r.added_at or 0, reverse=True)

    active = _newest_first(active)
    ready_filtered = _newest_first(ready_filtered)
    watching_filtered = _newest_first(watching_filtered)
    filled = _newest_first(filled)
    cancelled = _newest_first(cancelled)
    expired = _newest_first(expired)

    return {
        "counts": {
            "active": len(active),
            "ready": len(ready_filtered),
            "watching": len(watching_filtered),
            "filled": len(filled),
            "cancelled": len(cancelled),
            "expired": len(expired),
        },
        "active": [_format_candidate(r) for r in active],
        "ready": [_format_candidate(r) for r in ready_filtered],
        "watching": [_format_candidate(r) for r in watching_filtered],
        "filled": [_format_candidate(r) for r in filled],
        "cancelled": [_format_candidate(r) for r in cancelled],
        "expired": [_format_candidate(r) for r in expired],
    }
