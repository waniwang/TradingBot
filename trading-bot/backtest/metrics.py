"""
Backtest performance metrics calculator.

Computes standard trading statistics from a list of closed trades
and a daily equity curve.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Trade:
    """Represents a single closed trade."""

    ticker: str
    setup_type: str
    side: str            # "long" or "short"
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    exit_reason: str


def compute_metrics(
    trades: list[Trade],
    daily_equity: list[float],
    initial_capital: float = 100_000.0,
    trading_days_per_year: int = 252,
) -> dict:
    """
    Compute performance metrics from backtest results.

    Args:
        trades: list of closed Trade objects
        daily_equity: list of end-of-day portfolio values
        initial_capital: starting portfolio value
        trading_days_per_year: used for annualization

    Returns:
        dict of metrics
    """
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_winner": 0.0,
            "avg_loser": 0.0,
            "wl_ratio": 0.0,
            "profit_factor": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "total_return_pct": 0.0,
            "cagr": 0.0,
            "calmar": 0.0,
            "avg_days_held": 0.0,
            "max_consecutive_losses": 0,
            "avg_trades_per_month": 0.0,
        }

    pnls = [t.pnl for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]

    total_trades = len(trades)
    win_rate = len(winners) / total_trades * 100 if total_trades > 0 else 0.0

    avg_winner = float(np.mean(winners)) if winners else 0.0
    avg_loser = float(np.mean(losers)) if losers else 0.0
    wl_ratio = avg_winner / abs(avg_loser) if avg_loser != 0 else float("inf")

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe ratio from daily equity
    sharpe = 0.0
    if len(daily_equity) >= 2:
        eq = np.array(daily_equity, dtype=float)
        daily_returns = np.diff(eq) / eq[:-1]
        if daily_returns.std() > 0:
            sharpe = float(
                daily_returns.mean() / daily_returns.std()
                * math.sqrt(trading_days_per_year)
            )

    # Max drawdown
    max_dd_pct = compute_max_drawdown(daily_equity)

    # Total return
    final = daily_equity[-1] if daily_equity else initial_capital
    total_return_pct = (final - initial_capital) / initial_capital * 100

    # CAGR
    n_days = len(daily_equity) - 1 if len(daily_equity) > 1 else 1
    years = n_days / trading_days_per_year
    cagr = 0.0
    if years > 0 and final > 0:
        cagr = (final / initial_capital) ** (1 / years) - 1
        cagr *= 100  # as percentage

    # Calmar ratio (CAGR / max drawdown)
    calmar = abs(cagr / max_dd_pct) if max_dd_pct > 0 else 0.0

    # Average days held
    days_held = []
    for t in trades:
        try:
            d0 = pd.Timestamp(t.entry_date)
            d1 = pd.Timestamp(t.exit_date)
            days_held.append(max(1, (d1 - d0).days))
        except Exception:
            days_held.append(1)
    avg_days_held = float(np.mean(days_held)) if days_held else 0.0

    # Max consecutive losses
    max_consec_losses = 0
    current_streak = 0
    for p in pnls:
        if p < 0:
            current_streak += 1
            max_consec_losses = max(max_consec_losses, current_streak)
        else:
            current_streak = 0

    # Avg trades per month
    if len(daily_equity) > 1:
        months = n_days / 21.0  # ~21 trading days per month
        avg_trades_per_month = total_trades / months if months > 0 else 0.0
    else:
        avg_trades_per_month = 0.0

    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "avg_winner": round(avg_winner, 2),
        "avg_loser": round(avg_loser, 2),
        "wl_ratio": round(wl_ratio, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "total_return_pct": round(total_return_pct, 2),
        "cagr": round(cagr, 2),
        "calmar": round(calmar, 2),
        "avg_days_held": round(avg_days_held, 1),
        "max_consecutive_losses": max_consec_losses,
        "avg_trades_per_month": round(avg_trades_per_month, 2),
    }


def compute_max_drawdown(equity: list[float]) -> float:
    """Return the maximum drawdown as a positive percentage."""
    if len(equity) < 2:
        return 0.0
    eq = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    drawdowns = (eq - peak) / peak * 100
    return float(abs(drawdowns.min()))
