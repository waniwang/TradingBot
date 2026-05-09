"""
EP Earnings spreadsheet backtest.

Applies Strategy B filters to historical EP earnings gap candidates and
simulates trade outcomes using forward return checkpoints. Strategy A and C
were dropped 2026-05-08 — see strategies/ep_earnings/strategy.py.

Usage:
    python run_ep_backtest.py --type earnings
    python run_ep_backtest.py --type earnings --year 2025
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yaml

from backtest.ep_data_loader import (
    load_ep_spreadsheet,
    simulate_trades,
    compute_ep_stats,
    year_by_year_breakdown,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.yaml"
DEFAULT_DATA = Path(__file__).parents[2] / "backtest/data/2020-2025 EP Selection EARNINGS.xlsx"


def _load_config() -> dict:
    """Load strategy config from config.yaml."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def apply_filters(df: pd.DataFrame, variant: str = "B", config: dict | None = None) -> pd.DataFrame:
    """
    Apply EP earnings Strategy B filters (vectorized).

    Matches the logic in strategy.py evaluate_strategy_b. The `variant`
    argument is retained for API compatibility but only "B" is supported.

    Args:
        df: DataFrame from load_ep_spreadsheet().
        variant: must be "B" (only remaining variant).
        config: Strategy config dict. Loaded from config.yaml if None.

    Returns:
        Filtered DataFrame of candidates that pass the strategy rules.
    """
    if config is None:
        config = _load_config()

    if variant.upper() != "B":
        raise ValueError(f"Only variant 'B' supported (Strategy A and C were dropped 2026-05-08); got {variant}")

    min_cir = float(config.get("b_min_close_in_range", 50.0))
    atr_min = float(config.get("b_atr_pct_min", 2.0))
    atr_max = float(config.get("b_atr_pct_max", 5.0))

    mask = (
        (df["chg_open_pct"] > 0) &
        (df["close_in_range"] >= min_cir) &
        (df["atr_pct"] >= atr_min) &
        (df["atr_pct"] <= atr_max)
    )

    filtered = df[mask].copy()
    logger.info("EP Earnings Strategy B: %d / %d candidates pass filters",
                len(filtered), len(df))
    return filtered


def run_backtest(
    data_path: str | Path | None = None,
    strategy: str = "B",
    year: int | None = None,
) -> dict:
    """
    Run EP earnings backtest end-to-end.

    Args:
        data_path: Path to Excel file. Uses default if None.
        strategy: must be "B" (only remaining variant).
        year: Filter to a single year. None = all years.

    Returns:
        Dict with results keyed by strategy variant.
    """
    path = Path(data_path) if data_path else DEFAULT_DATA
    config = _load_config()
    stop_pct = float(config.get("stop_loss_pct", 7.0))
    hold_period = "50D"

    df = load_ep_spreadsheet(path)

    if year is not None:
        df = df[df["date"].dt.year == year].copy()
        logger.info("Filtered to year %d: %d rows", year, len(df))

    v = "B"
    filtered = apply_filters(df, v, config)
    trades = simulate_trades(
        filtered, stop_pct=stop_pct, hold_period=hold_period,
        setup_type=f"ep_earnings_{v.lower()}",
    )
    stats = compute_ep_stats(trades, filtered)
    yearly = year_by_year_breakdown(trades)

    return {
        v: {
            "trades": trades,
            "stats": stats,
            "yearly": yearly,
            "config": {
                "variant": v,
                "stop_pct": stop_pct,
                "hold_period": hold_period,
                "data_file": path.name,
                "total_candidates": len(df),
            },
        }
    }
