"""Portfolio metrics endpoint."""

from __future__ import annotations

from datetime import datetime, date

from fastapi import APIRouter

from db.models import DailyPnl, Position, get_session
from api.deps import get_db_engine, get_alpaca, get_config

router = APIRouter()


@router.get("/portfolio")
def get_portfolio():
    engine = get_db_engine()
    config = get_config()

    # Account data from Alpaca
    try:
        alpaca = get_alpaca()
        portfolio_value = alpaca.get_portfolio_value()
        cash = alpaca.get_cash()
    except Exception:
        portfolio_value = 0.0
        cash = 0.0

    # Open positions
    with get_session(engine) as session:
        open_positions = session.query(Position).filter_by(is_open=True).all()
        today_start = datetime.combine(date.today(), datetime.min.time())
        closed_today = (
            session.query(Position)
            .filter(Position.is_open == False, Position.closed_at >= today_start)
            .all()
        )

        daily_realized = sum(p.realized_pnl or 0.0 for p in closed_today)

        # Unrealized P&L from open positions
        daily_unrealized = 0.0
        for p in open_positions:
            try:
                bar = alpaca.get_latest_bar(p.ticker)
                price = bar["last_price"]
                if price and price > 0:
                    daily_unrealized += p.unrealized_pnl(price)
            except Exception:
                pass

        total_daily_pnl = daily_realized + daily_unrealized
        daily_pnl_pct = (total_daily_pnl / portfolio_value * 100) if portfolio_value else 0.0

        # YTD realized P&L
        year_start = datetime.combine(date(date.today().year, 1, 1), datetime.min.time())
        closed_ytd = (
            session.query(Position)
            .filter(Position.is_open == False, Position.closed_at >= year_start)
            .all()
        )
        ytd_realized = sum(p.realized_pnl or 0.0 for p in closed_ytd)

        # YTD % uses first DailyPnl portfolio_value of the year as the baseline.
        # Falls back to current portfolio value minus YTD realized if no daily history.
        first_pnl_of_year = (
            session.query(DailyPnl)
            .filter(DailyPnl.trade_date >= year_start.date())
            .order_by(DailyPnl.trade_date)
            .first()
        )
        if first_pnl_of_year and first_pnl_of_year.portfolio_value:
            ytd_baseline = first_pnl_of_year.portfolio_value
        else:
            ytd_baseline = portfolio_value - ytd_realized
        ytd_realized_pct = (ytd_realized / ytd_baseline * 100) if ytd_baseline else 0.0

        return {
            "portfolio_value": portfolio_value,
            "cash": cash,
            "daily_pnl": total_daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "daily_realized": daily_realized,
            "daily_unrealized": daily_unrealized,
            "ytd_realized": ytd_realized,
            "ytd_realized_pct": ytd_realized_pct,
            "open_positions": len(open_positions),
            # max_positions == 0 → cap is disabled; surface as None so the
            # dashboard can render "—" rather than "X / 0".
            "max_positions": (config["risk"]["max_positions"] or None),
            "trades_today": len(closed_today),
        }
