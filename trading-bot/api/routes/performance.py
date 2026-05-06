"""Performance endpoints — P&L history and summary stats."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter

from db.models import DailyPnl, Position, get_session
from api.deps import get_db_engine
from api.variation import resolve_variations_batch

router = APIRouter()

# Display labels for strategy breakdown grouping.
_STRATEGY_LABELS = {
    "ep_earnings": "EP Earnings",
    "ep_news": "EP News",
    "breakout": "Breakout",
    "episodic_pivot": "Episodic Pivot",
    "parabolic_short": "Parabolic Short",
}


def _initial_risk_dollars(p: Position) -> float | None:
    """Per-share risk × shares. None if risk is invalid (zero or wrong-sided)."""
    risk_per_share = abs(p.entry_price - p.initial_stop_price)
    if risk_per_share <= 0 or p.shares <= 0:
        return None
    return risk_per_share * p.shares


def _r_multiple(p: Position) -> float | None:
    """realized_pnl / initial_risk_$. None if risk is invalid or pnl missing."""
    risk = _initial_risk_dollars(p)
    if risk is None or p.realized_pnl is None:
        return None
    return p.realized_pnl / risk


def _strategy_display(setup_type: str, variation: str | None) -> str:
    """Human label for the breakdown grouping. EP variants get an A/B/C suffix."""
    base = setup_type
    for suffix in ("_a", "_b", "_c"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    label = _STRATEGY_LABELS.get(base, base.replace("_", " ").title())
    if variation:
        return f"{label} {variation}"
    return label


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


_EMPTY_SUMMARY = {
    "total_return_pct": 0.0,
    "total_pnl_dollars": 0.0,
    "win_rate": 0.0,
    "total_trades": 0,
    "expectancy_r": 0.0,
    "profit_factor": 0.0,
    "best_trade_r": 0.0,
    "best_trade_pnl": 0.0,
    "worst_trade_r": 0.0,
    "worst_trade_pnl": 0.0,
    "avg_win_r": 0.0,
    "avg_win_dollars": 0.0,
    "avg_loss_r": 0.0,
    "avg_loss_dollars": 0.0,
    "strategy_breakdown": {},
}


@router.get("/performance/summary")
def get_performance_summary(days: int = 90):
    """R-multiple based performance summary over the trailing `days` window.

    R = realized_pnl / (abs(entry - initial_stop) × shares). Trades with
    invalid risk (zero or wrong-sided stop) are excluded from R-based metrics
    but still count toward $ totals.
    """
    engine = get_db_engine()
    cutoff_date = date.today() - timedelta(days=days)
    cutoff_dt = datetime.combine(cutoff_date, datetime.min.time())

    with get_session(engine) as session:
        closed = (
            session.query(Position)
            .filter(
                Position.is_open == False,
                Position.closed_at != None,
                Position.closed_at >= cutoff_dt,
            )
            .all()
        )

        if not closed:
            return _EMPTY_SUMMARY

        # Baseline portfolio value for total-return % — first DailyPnl in window.
        first_pnl = (
            session.query(DailyPnl)
            .filter(DailyPnl.trade_date >= cutoff_date)
            .order_by(DailyPnl.trade_date)
            .first()
        )
        baseline_value = first_pnl.portfolio_value if first_pnl else 0.0

        # Resolve A/B/C variation for EP positions in one batched query.
        variations = resolve_variations_batch(
            session,
            [(p.ticker, p.setup_type, p.opened_at) for p in closed],
        )

    # Per-trade arrays
    pnls: list[float] = [(p.realized_pnl or 0.0) for p in closed]
    r_pairs: list[tuple[Position, float]] = []  # (position, R)
    for p in closed:
        r = _r_multiple(p)
        if r is not None:
            r_pairs.append((p, r))

    rs = [r for _, r in r_pairs]
    win_pairs = [(p, r) for p, r in r_pairs if r > 0]
    loss_pairs = [(p, r) for p, r in r_pairs if r < 0]

    total_pnl = sum(pnls)
    winners_count = sum(1 for x in pnls if x > 0)
    win_rate = winners_count / len(pnls) * 100 if pnls else 0.0

    gross_wins = sum(x for x in pnls if x > 0)
    gross_losses = abs(sum(x for x in pnls if x < 0))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0.0

    total_return_pct = (total_pnl / baseline_value * 100) if baseline_value > 0 else 0.0

    expectancy_r = sum(rs) / len(rs) if rs else 0.0
    avg_win_r = sum(r for _, r in win_pairs) / len(win_pairs) if win_pairs else 0.0
    avg_loss_r = sum(r for _, r in loss_pairs) / len(loss_pairs) if loss_pairs else 0.0
    avg_win_dollars = (
        sum(p.realized_pnl or 0.0 for p, _ in win_pairs) / len(win_pairs)
        if win_pairs else 0.0
    )
    avg_loss_dollars = (
        sum(p.realized_pnl or 0.0 for p, _ in loss_pairs) / len(loss_pairs)
        if loss_pairs else 0.0
    )

    if r_pairs:
        best_pos, best_r = max(r_pairs, key=lambda x: x[1])
        worst_pos, worst_r = min(r_pairs, key=lambda x: x[1])
        best_trade_pnl = best_pos.realized_pnl or 0.0
        worst_trade_pnl = worst_pos.realized_pnl or 0.0
    else:
        best_r = worst_r = best_trade_pnl = worst_trade_pnl = 0.0

    # Strategy breakdown — group by display label that includes A/B/C variation.
    breakdown: dict[str, dict] = {}
    for p in closed:
        variation = variations.get((p.ticker, p.setup_type, p.opened_at))
        label = _strategy_display(p.setup_type, variation)
        bucket = breakdown.setdefault(
            label,
            {"trades": 0, "winners": 0, "total_pnl": 0.0, "_r_sum": 0.0, "_r_count": 0},
        )
        bucket["trades"] += 1
        pnl = p.realized_pnl or 0.0
        bucket["total_pnl"] += pnl
        if pnl > 0:
            bucket["winners"] += 1
        r = _r_multiple(p)
        if r is not None:
            bucket["_r_sum"] += r
            bucket["_r_count"] += 1

    strategy_breakdown = {
        label: {
            "trades": b["trades"],
            "win_rate": round(b["winners"] / b["trades"] * 100, 1) if b["trades"] else 0.0,
            "total_pnl": round(b["total_pnl"], 2),
            "avg_r": round(b["_r_sum"] / b["_r_count"], 2) if b["_r_count"] else 0.0,
        }
        for label, b in breakdown.items()
    }

    return {
        "total_return_pct": round(total_return_pct, 2),
        "total_pnl_dollars": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "total_trades": len(closed),
        "expectancy_r": round(expectancy_r, 2),
        "profit_factor": round(profit_factor, 2),
        "best_trade_r": round(best_r, 2),
        "best_trade_pnl": round(best_trade_pnl, 2),
        "worst_trade_r": round(worst_r, 2),
        "worst_trade_pnl": round(worst_trade_pnl, 2),
        "avg_win_r": round(avg_win_r, 2),
        "avg_win_dollars": round(avg_win_dollars, 2),
        "avg_loss_r": round(avg_loss_r, 2),
        "avg_loss_dollars": round(avg_loss_dollars, 2),
        "strategy_breakdown": strategy_breakdown,
    }
