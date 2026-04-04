"""
EP News spreadsheet backtest.

Applies Strategy A/B filters to historical EP news gap candidates
and simulates trade outcomes using forward return checkpoints.

Data source: 2020-2025 EP Selection NEWS V2 spreadsheet (4,714 candidates).

Usage:
    python run_ep_backtest.py --type news
    python run_ep_backtest.py --type news --strategy A
    python run_ep_backtest.py --type news --year 2025
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
DEFAULT_DATA = Path(__file__).parents[2] / "backtest/data/2020-2025 EP Selection NEWS V2.xlsx"


def _load_config() -> dict:
    """Load strategy config from config.yaml."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def apply_filters(df: pd.DataFrame, variant: str, config: dict | None = None) -> pd.DataFrame:
    """
    Apply EP news strategy filters (vectorized).

    Matches the logic in strategy.py evaluate_strategy_a/b.

    Args:
        df: DataFrame from load_ep_spreadsheet().
        variant: "A" or "B".
        config: Strategy config dict. Loaded from config.yaml if None.

    Returns:
        Filtered DataFrame of candidates that pass the strategy rules.
    """
    if config is None:
        config = _load_config()

    if variant.upper() == "A":
        # Strategy A (NEWS-Tight): matches strategy.py lines 99-158
        # Note: chg_open_min uses strict > (not >=), matching strategy.py line 110
        chg_min = float(config.get("a_chg_open_min", 2.0))
        chg_max = float(config.get("a_chg_open_max", 10.0))
        min_cir = float(config.get("a_min_close_in_range", 50.0))
        max_downside = float(config.get("a_max_downside_from_open", 3.0))
        prev_10d_max = float(config.get("a_prev_10d_max", -20.0))
        atr_min = float(config.get("a_atr_pct_min", 3.0))
        atr_max = float(config.get("a_atr_pct_max", 7.0))
        max_vol_m = float(config.get("a_max_volume_m", 3.0))

        mask = (
            (df["chg_open_pct"] > chg_min) &
            (df["chg_open_pct"] <= chg_max) &
            (df["close_in_range"] >= min_cir) &
            (df["downside_from_open"] < max_downside) &
            (df["prev_10d_change_pct"] <= prev_10d_max) &
            (df["atr_pct"] >= atr_min) &
            (df["atr_pct"] <= atr_max) &
            (df["volume"] / 1e6 < max_vol_m)
        )

    elif variant.upper() == "B":
        # Strategy B (NEWS-Relaxed): matches strategy.py lines 161-224
        chg_min = float(config.get("b_chg_open_min", 2.0))
        chg_max = float(config.get("b_chg_open_max", 10.0))
        cir_min = float(config.get("b_min_close_in_range", 30.0))
        cir_max = float(config.get("b_max_close_in_range", 80.0))
        max_downside = float(config.get("b_max_downside_from_open", 6.0))
        prev_10d_max = float(config.get("b_prev_10d_max", -10.0))
        atr_min = float(config.get("b_atr_pct_min", 3.0))
        atr_max = float(config.get("b_atr_pct_max", 7.0))
        max_vol_m = float(config.get("b_max_volume_m", 5.0))

        mask = (
            (df["chg_open_pct"] > chg_min) &
            (df["chg_open_pct"] <= chg_max) &
            (df["close_in_range"] >= cir_min) &
            (df["close_in_range"] <= cir_max) &
            (df["downside_from_open"] < max_downside) &
            (df["prev_10d_change_pct"] <= prev_10d_max) &
            (df["atr_pct"] >= atr_min) &
            (df["atr_pct"] <= atr_max) &
            (df["volume"] / 1e6 < max_vol_m)
        )

    else:
        raise ValueError(f"Unknown variant: {variant}. Use 'A' or 'B'.")

    filtered = df[mask].copy()
    logger.info("EP News Strategy %s: %d / %d candidates pass filters",
                variant.upper(), len(filtered), len(df))
    return filtered


def run_backtest(
    data_path: str | Path | None = None,
    strategy: str = "all",
    year: int | None = None,
) -> dict:
    """
    Run EP news backtest end-to-end.

    Args:
        data_path: Path to Excel file. Uses default if None.
        strategy: "A", "B", or "all".
        year: Filter to a single year. None = all years.

    Returns:
        Dict with results keyed by strategy variant.
    """
    path = Path(data_path) if data_path else DEFAULT_DATA
    config = _load_config()
    hold_period = "50D"

    df = load_ep_spreadsheet(path)

    if year is not None:
        df = df[df["date"].dt.year == year].copy()
        logger.info("Filtered to year %d: %d rows", year, len(df))

    variants = ["A", "B"] if strategy.lower() == "all" else [strategy.upper()]
    results = {}

    for v in variants:
        # Strategy A and B have different stop losses
        if v == "A":
            stop_pct = float(config.get("a_stop_loss_pct", 7.0))
        else:
            stop_pct = float(config.get("b_stop_loss_pct", 10.0))

        filtered = apply_filters(df, v, config)
        trades = simulate_trades(
            filtered, stop_pct=stop_pct, hold_period=hold_period,
            setup_type=f"ep_news_{v.lower()}",
        )
        stats = compute_ep_stats(trades, filtered)
        yearly = year_by_year_breakdown(trades)

        results[v] = {
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

    return results
