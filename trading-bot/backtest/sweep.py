"""
Parameter sweep and analysis orchestrator for backtesting.

Runs systematic evaluations: baseline comparison, one-at-a-time (OAT)
parameter sweeps, grid search on top-sensitive params, and out-of-sample
validation.

Usage:
    python -m backtest.sweep                     # quick: 20 tickers, baseline only
    python -m backtest.sweep --full              # full analysis with sweeps
    python -m backtest.sweep --sp500             # use S&P 500 universe
    python -m backtest.sweep --output results/   # save to custom dir
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.data import fetch_historical_bars, get_sp500_tickers
from backtest.runner import BacktestRunner, BacktestConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

DEFAULT_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
    "META", "TSLA", "AMD", "NFLX", "CRM",
    "AVGO", "ORCL", "SHOP", "SQ", "SNOW",
    "PLTR", "COIN", "MARA", "SMCI", "ARM",
]

SETUPS = ["breakout", "episodic_pivot", "parabolic_short"]

# OAT sweep definitions: param_name -> list of values
GENERAL_SWEEPS = {
    "risk_per_trade_pct": [0.5, 0.75, 1.0, 1.5, 2.0],
    "max_positions": [2, 3, 4, 6, 8],
    "partial_exit_after_days": [2, 3, 5, 7, 10],
    "partial_exit_gain_pct": [5.0, 10.0, 15.0, 20.0, 30.0],
    "partial_exit_fraction": [0.25, 0.33, 0.40, 0.50, 0.67],
    "trailing_ma_period": [5, 8, 10, 15, 20],
}

BREAKOUT_SWEEPS = {
    "breakout_consolidation_days": [10, 15, 20, 30, 40],
    "breakout_volume_multiplier": [1.0, 1.25, 1.5, 2.0, 2.5],
    "breakout_prior_move_pct": [15.0, 20.0, 30.0, 40.0, 50.0],
    "breakout_atr_contraction_ratio": [0.70, 0.80, 0.85, 0.90, 0.95],
    "breakout_stop_atr_mult": [0.75, 1.0, 1.25, 1.5, 2.0],
}

EP_SWEEPS = {
    "ep_min_gap_pct": [5.0, 7.5, 10.0, 15.0, 20.0],
    "ep_volume_multiplier": [1.5, 2.0, 2.5, 3.0],
    "ep_prior_rally_max_pct": [30.0, 40.0, 50.0, 75.0, 100.0],
    "ep_stop_atr_mult": [1.0, 1.25, 1.5, 2.0, 2.5],
}

PARABOLIC_SWEEPS = {
    "parabolic_min_gain_pct": [30.0, 40.0, 50.0, 75.0, 100.0],
    "parabolic_min_days": [2, 3, 5, 7],
    "parabolic_target_ma_short": [5, 8, 10, 15],
    "parabolic_target_ma_long": [15, 20, 30, 50],
}


def run_single(bars: dict, config: BacktestConfig, setups: list[str] | None = None) -> dict:
    """Run one backtest and return metrics + trade count."""
    runner = BacktestRunner(config)
    metrics = runner.run(bars, setups=setups)
    metrics["trades_list"] = runner.trades
    return metrics


def run_sweep(
    bars: dict,
    base_config: BacktestConfig,
    param_name: str,
    values: list,
    setups: list[str] | None = None,
) -> list[dict]:
    """Vary one parameter, return metrics for each value."""
    results = []
    for val in values:
        cfg = replace(base_config, **{param_name: val})
        runner = BacktestRunner(cfg)
        metrics = runner.run(bars, setups=setups)
        results.append({
            "param": param_name,
            "value": val,
            **metrics,
        })
    return results


def compute_spy_benchmark(start_date: str, end_date: str) -> dict:
    """Compute SPY buy-and-hold metrics for comparison."""
    import yfinance as yf

    try:
        df = yf.download("SPY", start=start_date, end=end_date, auto_adjust=True, progress=False)
    except Exception as e:
        return {"error": f"SPY download failed: {e}"}

    if df.empty:
        return {"error": "SPY data empty"}

    df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
    closes = df["close"].values

    initial = float(closes[0])
    final = float(closes[-1])
    total_return_pct = (final - initial) / initial * 100

    n_days = len(closes)
    years = n_days / 252.0
    cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0

    # Max drawdown
    peak = np.maximum.accumulate(closes)
    drawdowns = (closes - peak) / peak * 100
    max_dd = float(abs(drawdowns.min()))

    # Sharpe from daily returns
    daily_ret = np.diff(closes) / closes[:-1]
    sharpe = 0.0
    if daily_ret.std() > 0:
        sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252))

    calmar = abs(cagr / max_dd) if max_dd > 0 else 0.0

    return {
        "total_return_pct": round(total_return_pct, 2),
        "cagr": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "calmar": round(calmar, 2),
    }


def find_sensitive_params(sweep_results: list[dict], top_n: int = 5) -> list[tuple[str, float]]:
    """Rank parameters by their impact on Sharpe ratio (range of Sharpe across sweep)."""
    from collections import defaultdict
    by_param: dict[str, list[float]] = defaultdict(list)
    for r in sweep_results:
        by_param[r["param"]].append(r["sharpe"])

    impacts = []
    for param, sharpes in by_param.items():
        impact = max(sharpes) - min(sharpes)
        impacts.append((param, impact))

    impacts.sort(key=lambda x: x[1], reverse=True)
    return impacts[:top_n]


def run_grid_search(
    bars: dict,
    base_config: BacktestConfig,
    params: dict[str, list],
    setups: list[str] | None = None,
) -> list[dict]:
    """Run grid search over combinations of parameters."""
    import itertools

    keys = list(params.keys())
    combos = list(itertools.product(*[params[k] for k in keys]))
    results = []

    logger.info("Grid search: %d combinations over %s", len(combos), keys)
    for combo in combos:
        overrides = dict(zip(keys, combo))
        cfg = replace(base_config, **overrides)
        runner = BacktestRunner(cfg)
        metrics = runner.run(bars, setups=setups)
        results.append({
            **overrides,
            **metrics,
        })

    return results


def print_comparison_table(rows: list[dict], title: str = ""):
    """Print a formatted comparison table."""
    if title:
        print(f"\n{'=' * 80}")
        print(f"  {title}")
        print(f"{'=' * 80}")

    headers = ["Label", "Trades", "Win%", "Sharpe", "CAGR%", "MaxDD%", "PF", "Calmar", "AvgDays"]
    widths = [28, 7, 6, 7, 7, 7, 6, 7, 8]
    header_line = "".join(h.rjust(w) for h, w in zip(headers, widths))
    print(header_line)
    print("-" * sum(widths))

    for r in rows:
        label = r.get("label", "?")[:28]
        vals = [
            label,
            str(r.get("total_trades", 0)),
            f"{r.get('win_rate', 0):.1f}",
            f"{r.get('sharpe', 0):.2f}",
            f"{r.get('cagr', 0):.1f}",
            f"{r.get('max_drawdown_pct', 0):.1f}",
            f"{r.get('profit_factor', 0):.2f}",
            f"{r.get('calmar', 0):.2f}",
            f"{r.get('avg_days_held', 0):.0f}",
        ]
        line = "".join(v.rjust(w) for v, w in zip(vals, widths))
        print(line)
    print()


def print_sweep_table(results: list[dict], param_name: str):
    """Print sweep results for one parameter."""
    print(f"\n  Sweep: {param_name}")
    print(f"  {'Value':>10} {'Trades':>7} {'Win%':>6} {'Sharpe':>7} {'CAGR%':>7} {'MaxDD%':>7} {'PF':>6}")
    print(f"  {'-'*50}")
    for r in results:
        print(f"  {str(r['value']):>10} {r['total_trades']:>7} {r['win_rate']:>6.1f} "
              f"{r['sharpe']:>7.2f} {r['cagr']:>7.1f} {r['max_drawdown_pct']:>7.1f} "
              f"{r['profit_factor']:>6.2f}")


def save_results(data: dict, output_dir: Path):
    """Save results to JSON files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    for key, value in data.items():
        path = output_dir / f"{key}.json"
        # Convert Trade objects if present
        serializable = _make_serializable(value)
        with open(path, "w") as f:
            json.dump(serializable, f, indent=2, default=str)
        logger.info("Saved %s", path)


def _make_serializable(obj):
    """Recursively convert non-serializable objects."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items() if k != "trades_list"}
    elif isinstance(obj, list):
        return [_make_serializable(item) for item in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backtest parameter sweep & analysis")
    parser.add_argument("--full", action="store_true", help="Run full analysis with sweeps")
    parser.add_argument("--sp500", action="store_true", help="Use S&P 500 universe")
    parser.add_argument("--output", default="backtest_results", help="Output directory")
    parser.add_argument("--is-start", default="2019-01-01", help="In-sample start date")
    parser.add_argument("--is-end", default="2021-12-31", help="In-sample end date")
    parser.add_argument("--oos-start", default="2022-01-01", help="Out-of-sample start date")
    parser.add_argument("--oos-end", default="2024-12-31", help="Out-of-sample end date")
    args = parser.parse_args()

    output_dir = Path(args.output)
    all_results = {}

    # Determine tickers
    if args.sp500:
        logger.info("Fetching S&P 500 tickers...")
        tickers = get_sp500_tickers()
        if not tickers:
            logger.error("Could not fetch S&P 500 tickers")
            sys.exit(1)
        logger.info("Got %d S&P 500 tickers", len(tickers))
    else:
        tickers = DEFAULT_TICKERS

    # Download data for full period (cached)
    full_start = args.is_start
    full_end = args.oos_end
    logger.info("Downloading data for %d tickers: %s to %s", len(tickers), full_start, full_end)
    t0 = time.time()
    bars = fetch_historical_bars(tickers, full_start, full_end)
    logger.info("Data ready: %d tickers in %.1fs", len(bars), time.time() - t0)

    if not bars:
        logger.error("No data — aborting")
        sys.exit(1)

    # Split bars into IS/OOS periods
    def split_bars(all_bars: dict, start: str, end: str) -> dict:
        result = {}
        for ticker, df in all_bars.items():
            df_copy = df.copy()
            if "date" in df_copy.columns:
                dates = pd.to_datetime(df_copy["date"])
                mask = (dates >= start) & (dates <= end)
                sub = df_copy.loc[mask].copy()
            else:
                sub = df_copy.loc[start:end].copy()
            if len(sub) >= 30:
                result[ticker] = sub
        return result

    bars_is = split_bars(bars, args.is_start, args.is_end)
    bars_oos = split_bars(bars, args.oos_start, args.oos_end)
    bars_full = bars

    logger.info("IS period: %d tickers, OOS period: %d tickers", len(bars_is), len(bars_oos))

    base_config = BacktestConfig()

    # ---------------------------------------------------------------
    # Step 1: Baseline runs
    # ---------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  STEP 1: BASELINE RUNS")
    print("=" * 80)

    baseline_rows = []

    for setup in SETUPS + [None]:
        setup_name = setup or "combined"
        setup_list = [setup] if setup else None

        for period_name, period_bars in [("IS", bars_is), ("OOS", bars_oos), ("Full", bars_full)]:
            t0 = time.time()
            metrics = run_single(period_bars, base_config, setups=setup_list)
            elapsed = time.time() - t0
            label = f"{setup_name} ({period_name})"
            metrics["label"] = label
            baseline_rows.append(metrics)
            logger.info("  %s: %d trades in %.1fs", label, metrics["total_trades"], elapsed)

    # SPY benchmark
    for period_name, start, end in [
        ("IS", args.is_start, args.is_end),
        ("OOS", args.oos_start, args.oos_end),
        ("Full", full_start, full_end),
    ]:
        spy = compute_spy_benchmark(start, end)
        spy["label"] = f"SPY buy&hold ({period_name})"
        spy.setdefault("total_trades", 1)
        spy.setdefault("win_rate", 100.0)
        spy.setdefault("profit_factor", float("inf"))
        spy.setdefault("avg_days_held", 0)
        baseline_rows.append(spy)

    print_comparison_table(baseline_rows, "BASELINE COMPARISON")
    all_results["baseline"] = baseline_rows

    if not args.full:
        save_results(all_results, output_dir)
        print(f"Results saved to {output_dir}/")
        print("Run with --full for parameter sweeps and optimization.")
        return

    # ---------------------------------------------------------------
    # Step 2: OAT Parameter Sweeps (on IS data)
    # ---------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  STEP 2: ONE-AT-A-TIME PARAMETER SWEEPS (In-Sample)")
    print("=" * 80)

    all_sweep_results = []

    # General params (all setups combined)
    print("\n--- General Parameters ---")
    for param, values in GENERAL_SWEEPS.items():
        t0 = time.time()
        results = run_sweep(bars_is, base_config, param, values)
        all_sweep_results.extend(results)
        print_sweep_table(results, param)
        logger.info("  Swept %s: %.1fs", param, time.time() - t0)

    # Breakout-specific
    print("\n--- Breakout Parameters ---")
    for param, values in BREAKOUT_SWEEPS.items():
        t0 = time.time()
        results = run_sweep(bars_is, base_config, param, values, setups=["breakout"])
        all_sweep_results.extend(results)
        print_sweep_table(results, param)
        logger.info("  Swept %s: %.1fs", param, time.time() - t0)

    # EP-specific
    print("\n--- Episodic Pivot Parameters ---")
    for param, values in EP_SWEEPS.items():
        t0 = time.time()
        results = run_sweep(bars_is, base_config, param, values, setups=["episodic_pivot"])
        all_sweep_results.extend(results)
        print_sweep_table(results, param)
        logger.info("  Swept %s: %.1fs", param, time.time() - t0)

    # Parabolic-specific
    print("\n--- Parabolic Short Parameters ---")
    for param, values in PARABOLIC_SWEEPS.items():
        t0 = time.time()
        results = run_sweep(bars_is, base_config, param, values, setups=["parabolic_short"])
        all_sweep_results.extend(results)
        print_sweep_table(results, param)
        logger.info("  Swept %s: %.1fs", param, time.time() - t0)

    all_results["oat_sweeps"] = all_sweep_results

    # ---------------------------------------------------------------
    # Step 3: Identify most sensitive parameters
    # ---------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  STEP 3: PARAMETER SENSITIVITY RANKING")
    print("=" * 80)

    sensitive = find_sensitive_params(all_sweep_results, top_n=10)
    print(f"\n  {'Parameter':<35} {'Sharpe Impact':>14}")
    print(f"  {'-'*49}")
    for param, impact in sensitive:
        print(f"  {param:<35} {impact:>14.3f}")
    all_results["sensitivity"] = [{"param": p, "sharpe_impact": i} for p, i in sensitive]

    # ---------------------------------------------------------------
    # Step 4: Grid search on top 3 most sensitive general params
    # ---------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  STEP 4: GRID SEARCH (Top 3 General Params, In-Sample)")
    print("=" * 80)

    # Pick top 3 general params that are in GENERAL_SWEEPS
    general_sensitive = [p for p, _ in sensitive if p in GENERAL_SWEEPS][:3]
    if len(general_sensitive) >= 2:
        # Use 3 values per param (low, default, high) for tractability
        grid_params = {}
        for p in general_sensitive:
            vals = GENERAL_SWEEPS[p]
            # Pick low, mid, high
            grid_params[p] = [vals[0], vals[len(vals) // 2], vals[-1]]

        grid_results = run_grid_search(bars_is, base_config, grid_params)
        grid_results.sort(key=lambda r: r.get("sharpe", 0), reverse=True)

        print(f"\n  Top 10 combos by Sharpe (out of {len(grid_results)}):")
        param_keys = list(grid_params.keys())
        header = "  " + "".join(f"{p:>20}" for p in param_keys) + f"{'Sharpe':>8}{'CAGR%':>8}{'MaxDD%':>8}{'Win%':>7}{'Trades':>8}"
        print(header)
        print("  " + "-" * len(header))
        for r in grid_results[:10]:
            vals = "  " + "".join(f"{str(r.get(p, '?')):>20}" for p in param_keys)
            vals += f"{r.get('sharpe', 0):>8.2f}{r.get('cagr', 0):>8.1f}{r.get('max_drawdown_pct', 0):>8.1f}{r.get('win_rate', 0):>7.1f}{r.get('total_trades', 0):>8}"
            print(vals)

        all_results["grid_search"] = grid_results[:20]  # save top 20

    # ---------------------------------------------------------------
    # Step 5: Validate best params on OOS
    # ---------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  STEP 5: OUT-OF-SAMPLE VALIDATION")
    print("=" * 80)

    validation_rows = []

    # Default config on OOS
    metrics_default_oos = run_single(bars_oos, base_config)
    metrics_default_oos["label"] = "default (OOS)"
    validation_rows.append(metrics_default_oos)

    # Default config on IS
    metrics_default_is = run_single(bars_is, base_config)
    metrics_default_is["label"] = "default (IS)"
    validation_rows.append(metrics_default_is)

    # Best grid config on OOS (if grid search was run)
    if len(general_sensitive) >= 2 and grid_results:
        best = grid_results[0]
        best_overrides = {p: best[p] for p in general_sensitive if p in best}
        best_config = replace(base_config, **best_overrides)

        metrics_tuned_is = run_single(bars_is, best_config)
        metrics_tuned_is["label"] = "tuned (IS)"
        validation_rows.append(metrics_tuned_is)

        metrics_tuned_oos = run_single(bars_oos, best_config)
        metrics_tuned_oos["label"] = "tuned (OOS)"
        validation_rows.append(metrics_tuned_oos)

        # Overfit check
        is_sharpe = metrics_tuned_is.get("sharpe", 0)
        oos_sharpe = metrics_tuned_oos.get("sharpe", 0)
        if oos_sharpe > 0:
            ratio = is_sharpe / oos_sharpe
            overfit_flag = "OVERFIT WARNING" if ratio > 2 else "OK"
            print(f"\n  IS/OOS Sharpe ratio: {ratio:.2f} [{overfit_flag}]")
        else:
            print(f"\n  OOS Sharpe <= 0 — strategy may not generalize")

    # SPY benchmark
    spy_oos = compute_spy_benchmark(args.oos_start, args.oos_end)
    spy_oos["label"] = "SPY buy&hold (OOS)"
    spy_oos.setdefault("total_trades", 1)
    spy_oos.setdefault("win_rate", 100.0)
    spy_oos.setdefault("profit_factor", float("inf"))
    spy_oos.setdefault("avg_days_held", 0)
    validation_rows.append(spy_oos)

    print_comparison_table(validation_rows, "OUT-OF-SAMPLE VALIDATION")
    all_results["validation"] = validation_rows

    # ---------------------------------------------------------------
    # Step 6: Per-strategy tuned vs default comparison
    # ---------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  STEP 6: PER-STRATEGY COMPARISON")
    print("=" * 80)

    strategy_rows = []
    for setup in SETUPS:
        # Find best param for this strategy from OAT sweeps
        setup_sweeps = [r for r in all_sweep_results if r.get("param", "").startswith(setup.split("_")[0])]
        if setup_sweeps:
            best_sweep = max(setup_sweeps, key=lambda r: r.get("sharpe", 0))
            best_param = best_sweep["param"]
            best_val = best_sweep["value"]
            tuned_config = replace(base_config, **{best_param: best_val})
        else:
            tuned_config = base_config

        # Default on IS and OOS
        m_is = run_single(bars_is, base_config, setups=[setup])
        m_is["label"] = f"{setup} default (IS)"
        strategy_rows.append(m_is)

        m_oos = run_single(bars_oos, base_config, setups=[setup])
        m_oos["label"] = f"{setup} default (OOS)"
        strategy_rows.append(m_oos)

        # Tuned on IS and OOS
        m_is_t = run_single(bars_is, tuned_config, setups=[setup])
        m_is_t["label"] = f"{setup} tuned (IS)"
        strategy_rows.append(m_is_t)

        m_oos_t = run_single(bars_oos, tuned_config, setups=[setup])
        m_oos_t["label"] = f"{setup} tuned (OOS)"
        strategy_rows.append(m_oos_t)

    print_comparison_table(strategy_rows, "PER-STRATEGY: DEFAULT vs TUNED")
    all_results["strategy_comparison"] = strategy_rows

    # ---------------------------------------------------------------
    # Save all results
    # ---------------------------------------------------------------
    save_results(all_results, output_dir)
    print(f"\nAll results saved to {output_dir}/")
    print("Done.")


if __name__ == "__main__":
    main()
