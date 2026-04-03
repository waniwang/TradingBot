"""Performance endpoints — P&L history and summary stats."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter

from db.models import DailyPnl, Position, get_session
from api.deps import get_db_engine

router = APIRouter()


@router.get("/performance/pnl")
def get_pnl_history(days: int = 30):
    engine = get_db_engine()
    cutoff = date.today() - timedelta(days=days)

    with get_session(engine) as session:
        rows = (
            session.query(DailyPnl)
            .filter(DailyPnl.trade_date >= cutoff)
            .order_by(DailyPnl.trade_date)
            .all()
        )

    if not rows:
        return []

    cumulative = 0.0
    result = []
    for r in rows:
        cumulative += r.total_pnl
        result.append({
            "date": str(r.trade_date),
            "daily_pnl": r.total_pnl,
            "realized": r.realized_pnl,
            "unrealized": r.unrealized_pnl,
            "cumulative": round(cumulative, 2),
            "portfolio_value": r.portfolio_value,
            "trades": r.num_trades,
            "winners": r.num_winners,
            "losers": r.num_losers,
        })

    return result


@router.get("/performance/summary")
def get_performance_summary(days: int = 30):
    engine = get_db_engine()
    cutoff = date.today() - timedelta(days=days)

    with get_session(engine) as session:
        pnl_rows = (
            session.query(DailyPnl)
            .filter(DailyPnl.trade_date >= cutoff)
            .order_by(DailyPnl.trade_date)
            .all()
        )

        closed = (
            session.query(Position)
            .filter(Position.is_open == False, Position.closed_at != None)
            .all()
        )

    if not pnl_rows:
        return {
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "best_day": 0.0,
            "worst_day": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "strategy_breakdown": {},
        }

    total_trades = sum(r.num_trades for r in pnl_rows)
    total_winners = sum(r.num_winners for r in pnl_rows)
    daily_pnls = [r.total_pnl for r in pnl_rows]

    # Strategy breakdown from closed positions
    strategy_stats: dict[str, dict] = {}
    for p in closed:
        setup = p.setup_type.replace("_", " ").title()
        if setup not in strategy_stats:
            strategy_stats[setup] = {"trades": 0, "pnl": 0.0, "winners": 0}
        strategy_stats[setup]["trades"] += 1
        strategy_stats[setup]["pnl"] += p.realized_pnl or 0.0
        if (p.realized_pnl or 0.0) > 0:
            strategy_stats[setup]["winners"] += 1

    # Avg win / avg loss
    wins = [p.realized_pnl for p in closed if (p.realized_pnl or 0) > 0]
    losses = [p.realized_pnl for p in closed if (p.realized_pnl or 0) < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0.0

    return {
        "total_pnl": round(sum(daily_pnls), 2),
        "win_rate": round(total_winners / total_trades * 100, 1) if total_trades else 0.0,
        "total_trades": total_trades,
        "best_day": round(max(daily_pnls), 2),
        "worst_day": round(min(daily_pnls), 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "strategy_breakdown": strategy_stats,
    }
