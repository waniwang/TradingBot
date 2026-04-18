"""
Unified watchlist manager — DB-backed for ALL setup types.

Lifecycle stages:
  watching  — breakout consolidating but not all criteria met
  ready     — breakout all criteria met, waiting for promotion to active
  active    — today's tradeable candidates (all setup types)
  triggered — signal fired, trade entered (terminal)
  expired   — end-of-day without trigger (terminal for EP/parabolic)
  failed    — pattern broke down or went stale (terminal)

The nightly scan (5 PM ET) updates breakout entries and discovers new ones.
The morning premarket scan persists EP/parabolic entries and promotes
breakout ready -> active.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta
from typing import Any

from db.models import Watchlist, BreakoutWatchlist, get_session
from scanner.consolidation import analyze_consolidation, classify_consolidation_stage
from scanner.momentum_rank import rank_by_momentum

logger = logging.getLogger(__name__)

STALE_WATCHING_DAYS = 45


# ---------------------------------------------------------------------------
# Generic persistence helpers
# ---------------------------------------------------------------------------

def persist_candidates(
    candidates: list[dict],
    setup_type: str,
    stage: str,
    scan_date: date,
    db_engine,
) -> int:
    """
    Insert candidates into the Watchlist table.

    Each candidate dict should have at minimum a 'ticker' key.
    All other keys are stored in metadata_json.

    Returns the number of rows inserted.
    """
    if not candidates:
        return 0

    now = datetime.utcnow()
    count = 0

    with get_session(db_engine) as session:
        for c in candidates:
            ticker = c["ticker"]
            # Skip if this ticker+setup_type+scan_date already exists (dedup on retry)
            existing = (
                session.query(Watchlist)
                .filter_by(ticker=ticker, setup_type=setup_type, scan_date=scan_date)
                .first()
            )
            if existing:
                logger.debug("Skipping duplicate %s/%s for %s", ticker, setup_type, scan_date)
                continue
            # Build metadata from all keys except 'ticker' and 'setup_type'
            meta = {k: v for k, v in c.items() if k not in ("ticker", "setup_type")}
            row = Watchlist(
                ticker=ticker,
                setup_type=setup_type,
                stage=stage,
                scan_date=scan_date,
                metadata_json=json.dumps(meta),
                added_at=now,
                updated_at=now,
                stage_changed_at=now,
            )
            session.add(row)
            count += 1
        session.commit()

    logger.info("Persisted %d %s candidates (stage=%s) for %s", count, setup_type, stage, scan_date)
    return count


def promote_ready_to_active(scan_date: date, db_engine) -> int:
    """
    Move breakout entries with stage='ready' to stage='active' for today.

    Returns count of promoted entries.
    """
    now = datetime.utcnow()
    count = 0

    with get_session(db_engine) as session:
        rows = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type == "breakout",
                Watchlist.stage == "ready",
            )
            .all()
        )
        for row in rows:
            row.stage = "active"
            row.scan_date = scan_date
            row.stage_changed_at = now
            row.updated_at = now
            count += 1
        session.commit()

    if count:
        logger.info("Promoted %d breakout entries from ready -> active", count)
    return count


def expire_stale_active(today: date, db_engine, plugins=None) -> int:
    """
    Expire yesterday's entries using plugin.watchlist_persist_days:
    - persist_days == 1 (single-day): set stage='expired'
    - persist_days == 0 (multi-day): set stage='ready' (demote for re-promotion)

    Falls back to legacy behavior if no plugins dict provided.
    Returns count of affected entries.
    """
    now = datetime.utcnow()
    count = 0

    if plugins:
        single_day = [name for name, p in plugins.items() if p.watchlist_persist_days == 1]
        multi_day = [name for name, p in plugins.items() if p.watchlist_persist_days == 0]
    else:
        # Legacy fallback (for tests and backward compat)
        single_day = ["episodic_pivot", "parabolic_short"]
        multi_day = ["breakout"]

    with get_session(db_engine) as session:
        # Single-day strategies: expire if not triggered today
        if single_day:
            stale_oneday = (
                session.query(Watchlist)
                .filter(
                    Watchlist.setup_type.in_(single_day),
                    Watchlist.stage == "active",
                    Watchlist.scan_date < today,
                )
                .all()
            )
            for row in stale_oneday:
                row.stage = "expired"
                row.stage_changed_at = now
                row.updated_at = now
                count += 1

        # Multi-day strategies: demote active back to ready
        if multi_day:
            stale_multiday = (
                session.query(Watchlist)
                .filter(
                    Watchlist.setup_type.in_(multi_day),
                    Watchlist.stage == "active",
                    Watchlist.scan_date < today,
                )
                .all()
            )
            for row in stale_multiday:
                row.stage = "ready"
                row.stage_changed_at = now
                row.updated_at = now
                count += 1

        session.commit()

    if count:
        logger.info("Expired/demoted %d stale active entries", count)
    return count


def get_active_watchlist(db_engine, enabled: list[str] | None = None) -> list[dict]:
    """Return all stage='active' entries as list[dict] via to_dict().

    If `enabled` is provided, only rows whose setup_type is in that list are returned.
    """
    with get_session(db_engine) as session:
        query = session.query(Watchlist).filter_by(stage="active")
        if enabled is not None:
            query = query.filter(Watchlist.setup_type.in_(list(enabled)))
        return [row.to_dict() for row in query.all()]


def purge_disabled_strategies(enabled: list[str], db_engine) -> int:
    """Delete every Watchlist row whose setup_type is not in `enabled`.

    Called on startup so toggling a strategy off in config.yaml is self-healing:
    on the next bot restart, stale rows from the disabled strategy disappear.

    Returns the number of rows deleted.
    """
    enabled_set = set(enabled)
    with get_session(db_engine) as session:
        rows = session.query(Watchlist).filter(
            ~Watchlist.setup_type.in_(enabled_set)
        ).all() if enabled_set else session.query(Watchlist).all()
        count = len(rows)
        if count:
            by_type: dict[str, int] = {}
            for r in rows:
                by_type[r.setup_type] = by_type.get(r.setup_type, 0) + 1
                session.delete(r)
            session.commit()
            logger.info("Purged %d watchlist rows for disabled strategies: %s", count, by_type)
    return count


# ---------------------------------------------------------------------------
# Nightly breakout scan (updates breakout pipeline)
# ---------------------------------------------------------------------------

def run_nightly_scan(
    config: dict,
    client,
    db_engine,
    progress_cb=None,
) -> dict[str, Any]:
    """
    Orchestrate the nightly breakout watchlist update.

    1. Fetch tradable universe
    2. Rank by momentum (top 100)
    3. Analyze consolidation for each
    4. Upsert into DB (new entries + update existing)
    5. Age out stale watching entries

    Returns summary dict: {new, updated, failed, ready, watching, aged_out}.
    """
    # 1. Universe → momentum rank (with price/volume filtering from yfinance data)
    _progress = progress_cb or (lambda task="", detail="": None)
    try:
        _progress("Fetching universe")
        all_tickers = client.get_tradable_universe()
        universe_cfg = config.get("universe", {})

        def _dl_progress(processed, total):
            _progress("Downloading daily bars (yfinance)", f"{processed} / {total} tickers")

        _progress("Downloading daily bars (yfinance)", f"0 / {len(all_tickers)} tickers")
        top_momentum = rank_by_momentum(
            all_tickers, config, client, top_n=100,
            min_price=universe_cfg.get("min_price", 5.0),
            min_avg_volume=universe_cfg.get("min_avg_volume", 100_000),
            progress_cb=_dl_progress,
        )
    except Exception as e:
        logger.error("Nightly scan: momentum rank failed: %s", e)
        _progress()  # clear
        return {"error": str(e)}

    universe_raw = len(all_tickers)

    momentum_tickers = [t["ticker"] for t in top_momentum]
    rs_by_ticker = {t["ticker"]: t.get("rs_composite", 0.0) for t in top_momentum}

    # 2. Fetch daily bars and analyze consolidation
    _progress("Analyzing consolidation", f"{len(momentum_tickers)} tickers")
    try:
        bars_by_symbol = client.get_daily_bars_batch(momentum_tickers, days=90)
    except Exception as e:
        logger.error("Nightly scan: daily bars fetch failed: %s", e)
        _progress()  # clear
        return {"error": str(e)}

    analyses = {}
    for ticker in momentum_tickers:
        try:
            df = bars_by_symbol.get(ticker)
            if df is None or df.empty:
                continue
            result = analyze_consolidation(ticker, config, df)
            result["rs_composite"] = rs_by_ticker.get(ticker, 0.0)
            analyses[ticker] = result
        except Exception as e:
            logger.warning("Nightly scan: consolidation check failed for %s: %s", ticker, e)

    # 3. Upsert DB
    summary = _update_watchlist_db(analyses, db_engine)

    # 4. Age out stale entries
    aged = _age_out_stale(db_engine)
    summary["aged_out"] = aged
    summary["universe_raw"] = universe_raw
    summary["momentum_top"] = len(top_momentum)

    _progress()  # clear progress

    logger.info(
        "Nightly watchlist scan complete: universe=%d momentum_top=%d new=%d updated=%d failed=%d "
        "ready=%d watching=%d aged_out=%d",
        universe_raw,
        len(top_momentum),
        summary.get("new", 0),
        summary.get("updated", 0),
        summary.get("failed", 0),
        summary.get("ready", 0),
        summary.get("watching", 0),
        aged,
    )
    return summary


def _update_watchlist_db(
    analyses: dict[str, dict],
    db_engine,
) -> dict[str, int]:
    """
    Upsert consolidation analysis results into the watchlist table.

    - New tickers with stage watching/ready are inserted.
    - Existing non-terminal entries are updated.
    - Existing entries whose stage transitions to failed are marked.

    Returns counts: {new, updated, failed, ready, watching}.
    """
    counts = {"new": 0, "updated": 0, "failed": 0, "ready": 0, "watching": 0}
    now = datetime.utcnow()
    today = now.date()

    with get_session(db_engine) as session:
        # Load existing active breakout entries (watching or ready)
        active_rows = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type == "breakout",
                Watchlist.stage.in_(["watching", "ready"]),
            )
            .all()
        )
        existing = {row.ticker: row for row in active_rows}

        for ticker, result in analyses.items():
            stage = classify_consolidation_stage(result)
            meta = {
                "consolidation_days": result.get("consolidation_days", 0),
                "atr_ratio": result.get("atr_ratio", 1.0),
                "atr_contracting": result.get("atr_contracting", False),
                "higher_lows": result.get("higher_lows", False),
                "near_10d_ma": result.get("near_10d_ma", False),
                "near_20d_ma": result.get("near_20d_ma", False),
                "volume_drying": result.get("volume_drying", False),
                "rs_composite": result.get("rs_composite"),
                "qualifies": result.get("qualifies", False),
                "has_prior_move": result.get("has_prior_move", False),
                "reason": result.get("reason", ""),
            }

            if ticker in existing:
                row = existing[ticker]
                old_stage = row.stage
                row.metadata_json = json.dumps(meta)
                row.updated_at = now

                if stage == "failed":
                    row.stage = "failed"
                    row.stage_changed_at = now
                    row.notes = result.get("reason", "pattern_failed")
                    counts["failed"] += 1
                else:
                    if stage != old_stage:
                        row.stage = stage
                        row.stage_changed_at = now
                    counts["updated"] += 1
                    counts[stage] += 1
            else:
                # New entry — only insert if watching or ready
                if stage in ("watching", "ready"):
                    row = Watchlist(
                        ticker=ticker,
                        setup_type="breakout",
                        stage=stage,
                        scan_date=today,
                        metadata_json=json.dumps(meta),
                        added_at=now,
                        updated_at=now,
                        stage_changed_at=now,
                    )
                    session.add(row)
                    counts["new"] += 1
                    counts[stage] += 1

        session.commit()

    return counts


def _age_out_stale(db_engine, max_days: int = STALE_WATCHING_DAYS) -> int:
    """
    Move watching entries older than max_days to failed.

    Returns count of aged-out entries.
    """
    cutoff = datetime.utcnow() - timedelta(days=max_days)
    now = datetime.utcnow()
    count = 0

    with get_session(db_engine) as session:
        stale = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type == "breakout",
                Watchlist.stage == "watching",
                Watchlist.added_at < cutoff,
            )
            .all()
        )
        for row in stale:
            row.stage = "failed"
            row.stage_changed_at = now
            row.notes = "stale_aged_out"
            count += 1
        session.commit()

    return count


# ---------------------------------------------------------------------------
# Query helpers used by main.py and dashboard
# ---------------------------------------------------------------------------

def get_ready_candidates(db_engine) -> list[dict]:
    """
    Return breakout stage="ready" entries formatted like scan_breakout_candidates() output.

    This provides drop-in compatibility with the merge logic in job_premarket_scan.
    """
    with get_session(db_engine) as session:
        rows = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type == "breakout",
                Watchlist.stage == "ready",
            )
            .all()
        )
        results = []
        for row in rows:
            d = row.to_dict()
            # Ensure backward-compatible keys
            d.setdefault("qualifies", True)
            d.setdefault("consolidation_days", 0)
            d.setdefault("atr_ratio", 1.0)
            d.setdefault("atr_contracting", d.get("atr_ratio", 1.0) < 0.85)
            d.setdefault("higher_lows", False)
            d.setdefault("near_10d_ma", False)
            d.setdefault("near_20d_ma", False)
            d.setdefault("volume_drying", False)
            d.setdefault("rs_composite", None)
            d.setdefault("has_prior_move", True)
            d.setdefault("reason", "ok")
            results.append(d)
        return results


def mark_triggered(ticker: str, db_engine, setup_type: str | None = None) -> bool:
    """
    Move an active (or ready) entry to triggered stage.

    Works for ALL setup types. Optionally filter by setup_type.
    Returns True if a row was updated, False if no matching entry found.
    """
    now = datetime.utcnow()
    with get_session(db_engine) as session:
        query = session.query(Watchlist).filter(
            Watchlist.ticker == ticker,
            Watchlist.stage.in_(["active", "ready"]),
        )
        if setup_type:
            query = query.filter(Watchlist.setup_type == setup_type)
        row = query.first()
        if row is None:
            return False
        row.stage = "triggered"
        row.stage_changed_at = now
        row.updated_at = now
        session.commit()
        return True


def get_pipeline_counts(db_engine) -> dict[str, int]:
    """Return counts of watching, ready, and active entries for the status heartbeat."""
    with get_session(db_engine) as session:
        watching = (
            session.query(Watchlist)
            .filter_by(stage="watching")
            .count()
        )
        ready = (
            session.query(Watchlist)
            .filter_by(stage="ready")
            .count()
        )
        active = (
            session.query(Watchlist)
            .filter_by(stage="active")
            .count()
        )
    return {"watching": watching, "ready": ready, "active": active}
