"""
Shared data loader and trade simulator for EP spreadsheet backtests.

Loads Excel files from the EP research dataset (2020-2025 gap candidates
with pre-computed features and forward returns), and simulates trade
outcomes using checkpoint-based stop/hold logic.

Used by strategies/ep_earnings/backtest.py and strategies/ep_news/backtest.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.metrics import Trade

logger = logging.getLogger(__name__)

# Spreadsheet column -> internal name
COLUMN_MAP = {
    "Symbol": "ticker",
    "Date": "date",
    "Open": "open",
    "Close": "close",
    "High": "high",
    "Low": "low",
    "Volume": "volume",
    "Market Cap": "market_cap",
    "10 Day ATR": "atr_10d",
    "21D ATR": "atr_21d",
    "Prev 10D change%": "prev_10d_change_pct",
    "CHG-OPEN%": "chg_open_pct",
    "Second day change%": "return_1d",
    "10thD change%": "return_10d",
    "20thD change%": "return_20d",
    "50thD change%": "return_50d",
    # Only in 2020-2025 multi-year files
    "OpenDay2": "open_day2",
    "HighDay2": "high_day2",
    "LowDay2": "low_day2",
    "CloseDay2": "close_day2",
}

# Forward return checkpoints in chronological order
CHECKPOINTS = ["return_1d", "return_10d", "return_20d", "return_50d"]
CP_LABELS = ["1D", "10D", "20D", "50D"]
HOLD_PERIOD_MAP = {"1D": "return_1d", "10D": "return_10d", "20D": "return_20d", "50D": "return_50d"}
HOLD_PERIOD_DAYS = {"1D": 1, "10D": 10, "20D": 20, "50D": 50}


def load_ep_spreadsheet(path: str | Path) -> pd.DataFrame:
    """
    Load an EP research Excel file and prepare it for backtesting.

    Renames columns to internal names, computes derived features,
    and validates required columns exist.

    Args:
        path: Path to the Excel file.

    Returns:
        DataFrame with standardized column names and derived features.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    logger.info("Loading %s ...", path.name)
    df = pd.read_excel(path)
    logger.info("  %d rows loaded", len(df))

    # Rename columns that exist in the file
    rename = {k: v for k, v in COLUMN_MAP.items() if k in df.columns}
    df = df.rename(columns=rename)

    # Validate required columns
    required = ["ticker", "date", "open", "close", "high", "low", "volume",
                 "chg_open_pct", "prev_10d_change_pct", "return_50d"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df["date"] = pd.to_datetime(df["date"])

    # Compute derived features
    df["atr_pct"] = df["atr_10d"] / df["close"] * 100
    df["downside_from_open"] = (df["open"] - df["low"]) / df["open"] * 100
    hl = df["high"] - df["low"]
    df["close_in_range"] = np.where(hl > 0, (df["close"] - df["low"]) / hl * 100, 50.0)
    df["ddv"] = df["close"] * df["volume"]
    df["mktcap_b"] = df["market_cap"] / 1e9

    logger.info("  Date range: %s to %s", df["date"].min().date(), df["date"].max().date())
    return df


def simulate_trades(
    filtered_df: pd.DataFrame,
    stop_pct: float,
    hold_period: str = "50D",
    setup_type: str = "ep_earnings",
    notional: float = 10_000.0,
) -> list[Trade]:
    """
    Simulate trade outcomes using forward return checkpoints.

    For each trade, checks forward returns at 1D, 10D, 20D, 50D in order.
    If any checkpoint return breaches the stop level, exits at -stop_pct.
    Otherwise exits at the hold period's forward return.

    Args:
        filtered_df: DataFrame of candidates that passed strategy filters.
        stop_pct: Stop loss percentage (e.g., 7.0 for -7%).
        hold_period: Hold period label ("1D", "10D", "20D", "50D").
        setup_type: Strategy name for Trade objects.
        notional: Dollar amount per trade for position sizing.

    Returns:
        List of Trade objects compatible with backtest.metrics.compute_metrics().
    """
    hold_col = HOLD_PERIOD_MAP[hold_period]
    hold_idx = CHECKPOINTS.index(hold_col)
    hold_days = HOLD_PERIOD_DAYS[hold_period]

    trades = []

    for _, row in filtered_df.iterrows():
        stopped = False
        exit_return = None
        exit_at_label = None

        # Check each checkpoint up to hold period
        for i, cp in enumerate(CHECKPOINTS):
            if i > hold_idx:
                break
            ret = row.get(cp)
            if pd.isna(ret):
                continue
            if ret <= -stop_pct:
                exit_return = -stop_pct
                exit_at_label = CP_LABELS[i]
                stopped = True
                break

        if not stopped:
            hold_ret = row.get(hold_col)
            if pd.notna(hold_ret):
                exit_return = hold_ret
                exit_at_label = hold_period
            else:
                continue  # no data for hold period, skip

        if exit_return is None:
            continue

        entry_price = row["close"]
        exit_price = entry_price * (1 + exit_return / 100)
        shares = max(1, int(notional / entry_price))
        pnl = (exit_price - entry_price) * shares

        entry_date = row["date"]
        exit_days = HOLD_PERIOD_DAYS.get(exit_at_label, hold_days)
        exit_date = entry_date + pd.Timedelta(days=exit_days)

        trades.append(Trade(
            ticker=row["ticker"],
            setup_type=setup_type,
            side="long",
            entry_date=str(entry_date.date()),
            exit_date=str(exit_date.date()),
            entry_price=round(entry_price, 2),
            exit_price=round(exit_price, 2),
            shares=shares,
            pnl=round(pnl, 2),
            exit_reason="stop_hit" if stopped else "max_hold_period",
        ))

    return trades


def compute_ep_stats(trades: list[Trade], filtered_df: pd.DataFrame) -> dict:
    """
    Compute EP-specific summary stats (percentage-based metrics).

    Complements backtest.metrics.compute_metrics() with stats that match
    the friend's output format (avg return %, median return %, stop rate).
    """
    if not trades:
        return {"n": 0, "win_rate": 0, "avg_return": 0, "med_return": 0,
                "stop_rate": 0, "avg_winner": 0, "avg_loser": 0, "pf": 0}

    returns = [(t.exit_price - t.entry_price) / t.entry_price * 100 for t in trades]
    winners = [r for r in returns if r > 0]
    losers = [r for r in returns if r <= 0]
    stopped = sum(1 for t in trades if t.exit_reason == "stop_hit")

    gross_win = sum(winners)
    gross_loss = abs(sum(losers))

    return {
        "n": len(trades),
        "win_rate": len(winners) / len(trades) * 100,
        "avg_return": float(np.mean(returns)),
        "med_return": float(np.median(returns)),
        "stop_rate": stopped / len(trades) * 100,
        "avg_winner": float(np.mean(winners)) if winners else 0,
        "avg_loser": float(np.mean(losers)) if losers else 0,
        "best": max(returns),
        "worst": min(returns),
        "pf": gross_win / gross_loss if gross_loss > 0 else float("inf"),
    }


def year_by_year_breakdown(trades: list[Trade]) -> dict[int, dict]:
    """Group trades by entry year and compute per-year stats."""
    by_year: dict[int, list[float]] = {}
    for t in trades:
        year = int(t.entry_date[:4])
        ret = (t.exit_price - t.entry_price) / t.entry_price * 100
        by_year.setdefault(year, []).append(ret)

    result = {}
    for year in sorted(by_year):
        returns = by_year[year]
        winners = [r for r in returns if r > 0]
        result[year] = {
            "n": len(returns),
            "win_rate": len(winners) / len(returns) * 100 if returns else 0,
            "avg_return": float(np.mean(returns)),
            "med_return": float(np.median(returns)),
        }
    return result
