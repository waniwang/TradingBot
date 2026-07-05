"""Parallel parameter sweep runner.

Reads a grid YAML, fans out combos across CPU cores via ProcessPoolExecutor,
applies fitness gates, computes Pareto frontier, and writes a leaderboard.

Usage:
    cd /Users/sharonk/Documents/TradingBot
    .venv/bin/python -m sweeps.sweep --grid sweeps/grids/strategy_b.yaml \\
        --data "market data download/2020-2025 EP Selection EARNINGS.xlsx" \\
        --workers 8 \\
        --fitness sweeps/fitness_config.yaml
"""

from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from itertools import product
from pathlib import Path

import yaml

from sweeps.fitness import FitnessConfig, evaluate
from sweeps.harness import BacktestRequest, run_backtest
from sweeps.path_harness import PathBacktestRequest, run_path_backtest

# Metrics dimensions used for Pareto ranking. Higher is better for all three
# (we negate dd so larger value = shallower drawdown).
PARETO_DIMS = ("annual_sharpe", "neg_max_dd", "n")

# Metrics columns shown on the leaderboard.
LEADERBOARD_COLS = [
    "rank",
    "n",
    "win_rate",
    "avg",
    "profit_factor",
    "annual_sharpe",
    "max_dd_pct",
    "total_return_pct",
    "score",
]


def load_grid(path: Path) -> dict:
    with open(path) as f:
        grid = yaml.safe_load(f)
    mode = grid.get("mode")
    if mode in ("simple", "scale_out"):
        if "params" not in grid:
            raise ValueError(f"grid YAML missing 'params': {path}")
    elif mode == "path":
        if "entry_params" not in grid or "exit_params" not in grid:
            raise ValueError(f"path grid needs 'entry_params' + 'exit_params': {path}")
    else:
        raise ValueError(f"grid mode must be simple/scale_out/path, got {mode}")
    return grid


def build_combos(grid: dict, strategy_name: str, data_path: str,
                 paths_dir: str | None = None) -> list:
    if grid["mode"] == "path":
        if not paths_dir:
            raise ValueError("path-mode grids need --paths-dir")
        exclude = tuple(sorted((grid.get("exclude_entry") or {}).items()))
        # List-valued entry params are swept (crossed with exit params);
        # scalars stay fixed. Same convention for exit params.
        entry_fixed = [(k, v) for k, v in grid["entry_params"].items()
                       if not isinstance(v, list)]
        entry_sweep = [(k, v) for k, v in grid["entry_params"].items()
                       if isinstance(v, list)]
        exit_keys = list(grid["exit_params"].keys())
        exit_vals = [v if isinstance(v, list) else [v]
                     for v in grid["exit_params"].values()]
        e_keys = [k for k, _ in entry_sweep]
        e_vals = [v for _, v in entry_sweep]
        requests = []
        for e_combo in product(*e_vals) if e_vals else [()]:
            entry = tuple(sorted(entry_fixed + list(zip(e_keys, e_combo))))
            for x_combo in product(*exit_vals):
                requests.append(PathBacktestRequest(
                    strategy_name=strategy_name,
                    entry_params=entry,
                    exit_params=tuple(zip(exit_keys, x_combo)),
                    data_path=data_path,
                    paths_dir=paths_dir,
                    exclude_entry_params=exclude,
                ))
        return requests

    keys = list(grid["params"].keys())
    vals = list(grid["params"].values())
    requests = []
    for combo in product(*vals):
        params_tuple = tuple(zip(keys, combo))
        requests.append(
            BacktestRequest(
                strategy_name=strategy_name,
                mode=grid["mode"],
                params=params_tuple,
                data_path=data_path,
            )
        )
    return requests


def pareto_frontier(results: list[dict]) -> list[dict]:
    """Returns the subset of results not dominated by any other on PARETO_DIMS.
    A point dominates another if it is >= on all dims and > on at least one."""
    pts = []
    for r in results:
        pts.append((r["annual_sharpe"], -abs(r["max_dd_pct"]), r["n"], r))

    frontier = []
    for i, (a1, b1, c1, r1) in enumerate(pts):
        dominated = False
        for j, (a2, b2, c2, _) in enumerate(pts):
            if i == j:
                continue
            if a2 >= a1 and b2 >= b1 and c2 >= c1 and (a2 > a1 or b2 > b1 or c2 > c1):
                dominated = True
                break
        if not dominated:
            frontier.append(r1)
    return frontier


def format_param(k: str, v: object) -> str:
    if isinstance(v, float):
        return f"{k}={v:g}"
    return f"{k}={v}"


def write_leaderboard(
    out_dir: Path,
    grid_path: Path,
    fitness_cfg: FitnessConfig,
    passed: list[dict],
    frontier: list[dict],
    skipped_count: int,
    failed_count: int,
    elapsed: float,
) -> Path:
    md_path = out_dir / "leaderboard.md"
    grid_name = grid_path.stem

    passed_sorted = sorted(passed, key=lambda r: r["annual_sharpe"], reverse=True)
    frontier_sorted = sorted(frontier, key=lambda r: r["annual_sharpe"], reverse=True)

    excluded_keys = {
        "strategy_name", "mode", "skipped", "reason",
        "n", "win_rate", "avg", "median", "stop_rate",
        "avg_win", "avg_loss", "profit_factor", "sharpe",
        "annual_sharpe", "max_dd_pct", "total_return_pct", "score",
    }

    with open(md_path, "w") as f:
        f.write(f"# Sweep: {grid_name}\n\n")
        f.write(f"- Grid: `{grid_path}`\n")
        f.write(f"- Total combos: {len(passed) + skipped_count + failed_count}\n")
        f.write(f"- Passed gates: {len(passed)}\n")
        f.write(f"- Failed gates: {failed_count}\n")
        f.write(f"- Skipped (no trades / too few): {skipped_count}\n")
        f.write(f"- Elapsed: {elapsed:.1f}s\n\n")
        f.write(f"## Fitness gates\n\n")
        f.write(f"- min_trades: {fitness_cfg.min_trades}\n")
        f.write(f"- min_profit_factor: {fitness_cfg.min_profit_factor}\n")
        f.write(f"- min_annual_sharpe: {fitness_cfg.min_annual_sharpe}\n")
        f.write(f"- max_drawdown_pct: {fitness_cfg.max_drawdown_pct}\n")
        f.write(f"- min_win_rate: {fitness_cfg.min_win_rate}\n\n")

        if not passed:
            f.write("## No combos passed the fitness gates.\n\n")
            f.write("Loosen the thresholds in `fitness_config.yaml` or expand the param grid.\n")
            return md_path

        f.write(f"## Pareto frontier ({len(frontier_sorted)} combos)\n\n")
        f.write("Non-dominated on (annual_sharpe, -|max_dd|, n). These are the only\n")
        f.write("combos worth considering; everything else is strictly worse on at least\n")
        f.write("one of the three axes.\n\n")
        _write_table(f, frontier_sorted, excluded_keys)

        f.write(f"\n## All passing combos ({len(passed_sorted)}), top 20 by annual_sharpe\n\n")
        _write_table(f, passed_sorted[:20], excluded_keys)

    return md_path


def _write_table(f, results: list[dict], excluded_keys: set[str]) -> None:
    if not results:
        return
    sample = results[0]
    param_keys = [k for k in sample.keys() if k not in excluded_keys]

    header = "| " + " | ".join(LEADERBOARD_COLS + ["params"]) + " |"
    sep = "|" + "|".join("---" for _ in range(len(LEADERBOARD_COLS) + 1)) + "|"
    f.write(header + "\n")
    f.write(sep + "\n")

    for i, r in enumerate(results, 1):
        params = " ".join(format_param(k, r[k]) for k in param_keys)
        cells = [
            str(i),
            str(r["n"]),
            f"{r['win_rate']:.0f}%",
            f"{r['avg']:+.2f}%",
            f"{r['profit_factor']:.2f}",
            f"{r['annual_sharpe']:.2f}",
            f"{r['max_dd_pct']:.1f}%",
            f"{r['total_return_pct']:+.0f}%",
            f"{r['score']:.2f}",
            params,
        ]
        f.write("| " + " | ".join(cells) + " |\n")


def write_csv(out_dir: Path, results: list[dict]) -> Path:
    csv_path = out_dir / "all_results.csv"
    if not results:
        return csv_path
    all_keys: list[str] = []
    seen = set()
    for r in results:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                all_keys.append(k)
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_keys)
        w.writeheader()
        for r in results:
            w.writerow(r)
    return csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel backtest sweep runner")
    parser.add_argument("--grid", required=True, type=Path, help="Path to grid YAML")
    parser.add_argument("--data", required=True, type=str, help="Path to data Excel (relative to TradingBot/ ok)")
    parser.add_argument("--strategy", default=None, help="Strategy name label (default: grid stem)")
    parser.add_argument("--workers", type=int, default=8, help="Number of worker processes")
    parser.add_argument("--fitness", type=Path, default=None, help="Path to fitness_config.yaml (default: sweeps/fitness_config.yaml)")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory (default: sweeps/runs/<timestamp>)")
    parser.add_argument("--paths-dir", type=str,
                        default="market data download/massive_daily",
                        help="Massive daily-path cache dir (path-mode grids)")
    args = parser.parse_args()

    grid = load_grid(args.grid)
    strategy_name = args.strategy or args.grid.stem

    data_path = Path(args.data)
    if not data_path.is_absolute():
        # Resolve relative to the TradingBot project root.
        project_root = Path(__file__).resolve().parent.parent
        data_path = (project_root / args.data).resolve()
    if not data_path.exists():
        print(f"ERROR: data file not found: {data_path}", file=sys.stderr)
        return 2

    fitness_path = args.fitness or (Path(__file__).resolve().parent / "fitness_config.yaml")
    fitness_cfg = FitnessConfig.from_yaml(fitness_path)

    out_dir = args.out_dir or (
        Path(__file__).resolve().parent
        / "runs"
        / (datetime.now().strftime("%Y%m%d_%H%M%S_") + strategy_name)
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    paths_dir = args.paths_dir
    if paths_dir and not Path(paths_dir).is_absolute():
        paths_dir = str((Path(__file__).resolve().parent.parent / paths_dir).resolve())
    requests = build_combos(grid, strategy_name, str(data_path), paths_dir)
    runner = run_path_backtest if grid["mode"] == "path" else run_backtest
    print(f"Sweep: {strategy_name}")
    print(f"  Grid: {args.grid}")
    print(f"  Data: {data_path}")
    print(f"  Combos: {len(requests)}")
    print(f"  Workers: {args.workers}")
    print(f"  Fitness gates: {fitness_path}")
    print(f"  Output: {out_dir}")
    print()

    start = datetime.now()
    all_results: list[dict] = []
    passed: list[dict] = []
    failed_count = 0
    skipped_count = 0

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(runner, req): req for req in requests}
        done = 0
        total = len(futures)
        for fut in as_completed(futures):
            done += 1
            try:
                r = fut.result()
            except Exception as exc:
                print(f"  [{done}/{total}] ERROR: {exc}", file=sys.stderr)
                failed_count += 1
                continue

            if r.get("skipped"):
                skipped_count += 1
            else:
                all_results.append(r)
                ok, _reasons = evaluate(r, fitness_cfg)
                if ok:
                    passed.append(r)
                else:
                    failed_count += 1

            if done % max(1, total // 20) == 0 or done == total:
                print(f"  [{done}/{total}] passed={len(passed)} failed={failed_count} skipped={skipped_count}")

    elapsed = (datetime.now() - start).total_seconds()
    frontier = pareto_frontier(passed) if passed else []

    csv_path = write_csv(out_dir, all_results)
    md_path = write_leaderboard(
        out_dir, args.grid, fitness_cfg, passed, frontier,
        skipped_count, failed_count, elapsed,
    )

    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Passed gates: {len(passed)} / {len(requests)}")
    print(f"  Pareto frontier: {len(frontier)}")
    print(f"  Leaderboard: {md_path}")
    print(f"  All results CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
