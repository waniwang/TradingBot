"""Watchlist endpoint — pipeline stages with setup-specific metadata."""

from __future__ import annotations

from fastapi import APIRouter

from db.models import Watchlist, get_session
from api.deps import get_db_engine, get_enabled_strategies

router = APIRouter()


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


@router.get("/watchlist")
def get_watchlist():
    engine = get_db_engine()
    enabled = get_enabled_strategies()

    with get_session(engine) as session:
        q = session.query(Watchlist).filter(Watchlist.setup_type.in_(tuple(enabled)))
        active = q.filter(Watchlist.stage == "active").all()
        ready = (
            session.query(Watchlist)
            .filter(Watchlist.setup_type.in_(tuple(enabled)), Watchlist.stage == "ready")
            .all()
        )
        watching = (
            session.query(Watchlist)
            .filter(Watchlist.setup_type.in_(tuple(enabled)), Watchlist.stage == "watching")
            .all()
        )

    # Deduplicate: active takes priority
    active_tickers = {r.ticker for r in active}
    ready_filtered = [r for r in ready if r.ticker not in active_tickers]
    shown_tickers = active_tickers | {r.ticker for r in ready}
    watching_filtered = [r for r in watching if r.ticker not in shown_tickers]

    return {
        "counts": {
            "active": len(active),
            "ready": len(ready_filtered),
            "watching": len(watching_filtered),
        },
        "active": [_format_candidate(r) for r in active],
        "ready": [_format_candidate(r) for r in ready_filtered],
        "watching": [_format_candidate(r) for r in watching_filtered],
    }
