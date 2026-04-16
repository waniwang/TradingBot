"""Strategies endpoint — list all strategies with status and stats."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import case, func

from api.constants import (
    PIPELINE_SCHEDULE,
    STRATEGY_META,
    STRATEGY_EXTRA_JOBS,
    job_to_strategy,
)
from api.deps import get_config, get_db_engine
from api.param_meta import PHASE_LABELS, build_config_params
from db.models import JobExecution, Position, get_session

router = APIRouter()


def _strategy_job_ids(slug: str) -> list[str]:
    """Return all job_ids associated with a strategy."""
    ids = [j["job_id"] for j in PIPELINE_SCHEDULE if job_to_strategy(j["job_id"]) == slug]
    ids.extend(STRATEGY_EXTRA_JOBS.get(slug, []))
    return ids


@router.get("/strategies")
def get_strategies():
    config = get_config()
    engine = get_db_engine()
    enabled_list = config.get("strategies", {}).get("enabled", [])

    strategies = []

    with get_session(engine) as session:
        for slug, meta in STRATEGY_META.items():
            enabled = slug in enabled_list
            job_ids = _strategy_job_ids(slug)

            # Last run across all this strategy's jobs
            last_run = None
            if job_ids:
                row = (
                    session.query(JobExecution)
                    .filter(JobExecution.job_id.in_(job_ids))
                    .order_by(JobExecution.started_at.desc())
                    .first()
                )
                if row:
                    last_run = {
                        "job_id": row.job_id,
                        "label": row.job_label,
                        "status": row.status,
                        "ran_at": row.started_at.isoformat() if row.started_at else None,
                        "result_summary": row.result_summary,
                    }

            # Position stats — prefix match (ep_earnings matches ep_earnings + ep_earnings_c)
            open_count = (
                session.query(func.count(Position.id))
                .filter(Position.is_open == True, Position.setup_type.like(f"{slug}%"))
                .scalar()
            ) or 0

            total_closed, winners, total_pnl = session.query(
                func.count(Position.id),
                func.sum(case((Position.realized_pnl > 0, 1), else_=0)),
                func.coalesce(func.sum(Position.realized_pnl), 0.0),
            ).filter(
                Position.is_open == False,
                Position.setup_type.like(f"{slug}%"),
            ).one()
            winners = winners or 0
            win_rate = round(winners / total_closed * 100, 1) if total_closed > 0 else 0.0

            # Extract strategy-specific config params, enriched with metadata
            # (description + variation + phase). See api/param_meta.py.
            # Handle `signals: null` in config.yaml by falling back to {}.
            signals_cfg = config.get("signals") or {}
            config_params = build_config_params(slug, signals_cfg)

            strategies.append({
                "slug": slug,
                "display_name": meta["display_name"],
                "enabled": enabled,
                "description": meta["description"],
                "job_ids": job_ids,
                "config_params": config_params,
                "stats": {
                    "open_positions": open_count,
                    "total_closed": total_closed,
                    "win_rate": win_rate,
                    "total_pnl": round(total_pnl, 2),
                },
                "last_run": last_run,
            })

    # Sort: enabled first, then alphabetical
    strategies.sort(key=lambda s: (not s["enabled"], s["slug"]))

    return {"strategies": strategies, "phase_labels": PHASE_LABELS}
