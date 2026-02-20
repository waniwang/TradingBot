"""
Persistent breakout watchlist manager.

Maintains a DB-backed watchlist with lifecycle stages:
  watching  — consolidating but not all criteria met
  ready     — all criteria met, waiting for intraday breakout
  triggered — signal fired, trade entered (terminal)
  failed    — pattern broke down or went stale (terminal)

The nightly scan (5 PM ET) updates existing entries and discovers new ones.
The morning premarket scan reads ready candidates from DB instead of
scanning from scratch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from db.models import BreakoutWatchlist, get_session
from scanner.consolidation import analyze_consolidation, classify_consolidation_stage
from scanner.momentum_rank import rank_by_momentum

logger = logging.getLogger(__name__)

STALE_WATCHING_DAYS = 45


def run_nightly_scan(
    config: dict,
    client,
    db_engine,
) -> dict[str, Any]:
    """
    Orchestrate the nightly breakout watchlist update.

    1. Fetch tradable universe
    2. Rank by momentum (top 100 — wider net than the old top-50)
    3. Analyze consolidation for each
    4. Upsert into DB (new entries + update existing)
    5. Age out stale watching entries

    Returns summary dict: {new, updated, failed, ready, watching, aged_out}.
    """
    # 1. Universe + momentum rank
    try:
        tickers_universe = client.get_tradable_universe(max_tickers=1500)
        top_momentum = rank_by_momentum(tickers_universe, config, client, top_n=100)
    except Exception as e:
        logger.error("Nightly scan: momentum rank failed: %s", e)
        return {"error": str(e)}

    momentum_tickers = [t["ticker"] for t in top_momentum]
    rs_by_ticker = {t["ticker"]: t.get("rs_composite", 0.0) for t in top_momentum}

    # 2. Fetch daily bars and analyze consolidation
    try:
        bars_by_symbol = client.get_daily_bars_batch(momentum_tickers, days=90)
    except Exception as e:
        logger.error("Nightly scan: daily bars fetch failed: %s", e)
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

    logger.info(
        "Nightly watchlist scan complete: new=%d updated=%d failed=%d "
        "ready=%d watching=%d aged_out=%d",
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
    Upsert consolidation analysis results into the breakout_watchlist table.

    - New tickers with stage watching/ready are inserted.
    - Existing non-terminal entries are updated.
    - Existing entries whose stage transitions to failed are marked.

    Returns counts: {new, updated, failed, ready, watching}.
    """
    counts = {"new": 0, "updated": 0, "failed": 0, "ready": 0, "watching": 0}
    now = datetime.utcnow()

    with get_session(db_engine) as session:
        # Load existing active entries (watching or ready)
        active_rows = (
            session.query(BreakoutWatchlist)
            .filter(BreakoutWatchlist.stage.in_(["watching", "ready"]))
            .all()
        )
        existing = {row.ticker: row for row in active_rows}

        for ticker, result in analyses.items():
            stage = classify_consolidation_stage(result)

            if ticker in existing:
                row = existing[ticker]
                old_stage = row.stage
                row.atr_ratio = result.get("atr_ratio", 1.0)
                row.consolidation_days = result.get("consolidation_days", 0)
                row.higher_lows = result.get("higher_lows", False)
                row.near_10d_ma = result.get("near_10d_ma", False)
                row.near_20d_ma = result.get("near_20d_ma", False)
                row.volume_drying = result.get("volume_drying", False)
                row.rs_composite = result.get("rs_composite")
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
                    row = BreakoutWatchlist(
                        ticker=ticker,
                        stage=stage,
                        consolidation_days=result.get("consolidation_days", 0),
                        atr_ratio=result.get("atr_ratio", 1.0),
                        higher_lows=result.get("higher_lows", False),
                        near_10d_ma=result.get("near_10d_ma", False),
                        near_20d_ma=result.get("near_20d_ma", False),
                        volume_drying=result.get("volume_drying", False),
                        rs_composite=result.get("rs_composite"),
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
            session.query(BreakoutWatchlist)
            .filter(
                BreakoutWatchlist.stage == "watching",
                BreakoutWatchlist.added_at < cutoff,
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


def get_ready_candidates(db_engine) -> list[dict[str, Any]]:
    """
    Return stage="ready" entries formatted like scan_breakout_candidates() output.

    This provides drop-in compatibility with the merge logic in job_premarket_scan.
    """
    with get_session(db_engine) as session:
        rows = (
            session.query(BreakoutWatchlist)
            .filter_by(stage="ready")
            .all()
        )
        return [
            {
                "ticker": row.ticker,
                "setup_type": "breakout",
                "qualifies": True,
                "consolidation_days": row.consolidation_days,
                "atr_contracting": row.atr_ratio < 0.85,
                "atr_ratio": row.atr_ratio,
                "higher_lows": row.higher_lows,
                "near_10d_ma": row.near_10d_ma,
                "near_20d_ma": row.near_20d_ma,
                "volume_drying": row.volume_drying,
                "rs_composite": row.rs_composite,
                "has_prior_move": True,
                "reason": "ok",
            }
            for row in rows
        ]


def mark_triggered(ticker: str, db_engine) -> bool:
    """
    Move a ready entry to triggered stage.

    Returns True if a row was updated, False if no ready entry found.
    """
    now = datetime.utcnow()
    with get_session(db_engine) as session:
        row = (
            session.query(BreakoutWatchlist)
            .filter_by(ticker=ticker, stage="ready")
            .first()
        )
        if row is None:
            return False
        row.stage = "triggered"
        row.stage_changed_at = now
        row.updated_at = now
        session.commit()
        return True


def get_pipeline_counts(db_engine) -> dict[str, int]:
    """Return counts of watching and ready entries for the status heartbeat."""
    with get_session(db_engine) as session:
        watching = (
            session.query(BreakoutWatchlist)
            .filter_by(stage="watching")
            .count()
        )
        ready = (
            session.query(BreakoutWatchlist)
            .filter_by(stage="ready")
            .count()
        )
    return {"watching": watching, "ready": ready}
