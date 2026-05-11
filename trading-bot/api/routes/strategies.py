"""Strategies endpoint — list all strategies with status and stats."""

from __future__ import annotations

from fastapi import APIRouter

from api.constants import (
    PIPELINE_SCHEDULE,
    STRATEGY_META,
    STRATEGY_EXTRA_JOBS,
    job_to_strategy,
)
from api.deps import get_alpaca, get_config, get_db_engine
from api.param_meta import PHASE_LABELS, build_config_params
from db.models import JobExecution, Position, get_session

router = APIRouter()


def _strategy_job_ids(slug: str) -> list[str]:
    """Return all job_ids associated with a strategy."""
    ids = [j["job_id"] for j in PIPELINE_SCHEDULE if job_to_strategy(j["job_id"]) == slug]
    ids.extend(STRATEGY_EXTRA_JOBS.get(slug, []))
    return ids


def _position_r(p: Position, exit_pnl: float) -> float | None:
    """R = exit_pnl / (per-share risk × shares)."""
    risk_per_share = abs(p.entry_price - p.initial_stop_price)
    if risk_per_share <= 0 or p.shares <= 0:
        return None
    return exit_pnl / (risk_per_share * p.shares)


@router.get("/strategies")
def get_strategies():
    config = get_config()
    engine = get_db_engine()
    enabled_list = config.get("strategies", {}).get("enabled", [])

    # Portfolio value for % computation. Best-effort — falls back to 0 (no %).
    try:
        alpaca = get_alpaca()
        portfolio_value = alpaca.get_portfolio_value()
    except Exception:
        alpaca = None
        portfolio_value = 0.0

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
            closed_positions = (
                session.query(Position)
                .filter(Position.is_open == False, Position.setup_type.like(f"{slug}%"))
                .all()
            )
            open_positions = (
                session.query(Position)
                .filter(Position.is_open == True, Position.setup_type.like(f"{slug}%"))
                .all()
            )

            # Realized stats (closed positions)
            total_closed = len(closed_positions)
            winners = sum(1 for p in closed_positions if (p.realized_pnl or 0.0) > 0)
            total_pnl = sum(p.realized_pnl or 0.0 for p in closed_positions)
            win_rate = round(winners / total_closed * 100, 1) if total_closed > 0 else 0.0
            realized_rs = [
                r for p in closed_positions
                if p.realized_pnl is not None
                and (r := _position_r(p, p.realized_pnl)) is not None
            ]
            # Cumulative R (sum), matching how the $ figures are presented.
            realized_total_r = round(sum(realized_rs), 2) if realized_rs else 0.0
            realized_pnl_pct = (total_pnl / portfolio_value * 100) if portfolio_value else 0.0

            # Unrealized stats (open positions, marked at current price)
            unrealized_pnl = 0.0
            unrealized_rs: list[float] = []
            if alpaca is not None:
                for p in open_positions:
                    try:
                        bar = alpaca.get_latest_bar(p.ticker)
                        price = bar["last_price"]
                    except Exception:
                        continue
                    if not price or price <= 0:
                        continue
                    pnl = p.unrealized_pnl(price)
                    unrealized_pnl += pnl
                    r = _position_r(p, pnl)
                    if r is not None:
                        unrealized_rs.append(r)
            unrealized_total_r = round(sum(unrealized_rs), 2) if unrealized_rs else 0.0
            unrealized_pnl_pct = (unrealized_pnl / portfolio_value * 100) if portfolio_value else 0.0
            open_count = len(open_positions)

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
                    "realized_total_r": realized_total_r,
                    "realized_pnl_pct": round(realized_pnl_pct, 2),
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "unrealized_total_r": unrealized_total_r,
                    "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                },
                "last_run": last_run,
            })

    # Sort: enabled first, then alphabetical
    strategies.sort(key=lambda s: (not s["enabled"], s["slug"]))

    return {"strategies": strategies, "phase_labels": PHASE_LABELS}
