# Parallel Backtest Sweeps

Parameter sweep harness for EP Earnings/News strategies. Replaces the sequential
grid search in `ep_optimize_5yr.py` with a parallel ProcessPoolExecutor + fitness
gates + Pareto frontier ranking. ~1500x faster (648 combos in 1.2s vs ~30 min).

## Usage

```bash
cd /Users/sharonk/Documents/TradingBot

# Strategy B sweep (648 combos)
trading-bot/.venv/bin/python -m sweeps.sweep \
    --grid sweeps/grids/strategy_b.yaml \
    --data "market data download/2020-2025 EP Selection EARNINGS.xlsx" \
    --workers 8

# Strategy C sweep
trading-bot/.venv/bin/python -m sweeps.sweep \
    --grid sweeps/grids/strategy_c.yaml \
    --data "market data download/2020-2025 EP Selection EARNINGS.xlsx" \
    --workers 8

# Sanity tests
trading-bot/.venv/bin/pytest sweeps/test_sweep_harness.py -v
```

Outputs land in `sweeps/runs/<timestamp>_<strategy>/`:
- `leaderboard.md` — Pareto frontier + top 20 by annual_sharpe
- `all_results.csv` — every combo's full metrics

## Files

| File | Role |
|------|------|
| `harness.py` | Shared filter/exit/metrics. Pure functions; ProcessPool-safe. |
| `fitness.py` | Gate definitions + evaluator. Combos must pass ALL gates. |
| `fitness_config.yaml` | Default gate thresholds. Edit to tune. |
| `sweep.py` | CLI runner. Reads grid, fans out, ranks. |
| `grids/strategy_b.yaml` | B param grid (translated from `ep_optimize_5yr.py`). |
| `grids/strategy_c.yaml` | C param grid. |
| `test_sweep_harness.py` | 10 sanity tests. |

## Adding a new grid

Drop a YAML in `grids/`:

```yaml
mode: simple   # or "scale_out" for Strategy D-style
params:
  stop_pct: [5.0, 6.0, 7.0]
  hold_period: ["20D", "50D"]
  # ... any keys apply_filters() understands
```

`itertools.product()` runs the cartesian product. Keep grids small enough that
`product` doesn't blow up memory (< ~5000 combos is fine).

## Tuning gates

`fitness_config.yaml` sets four gates. Most combos fail on `max_drawdown_pct`
because the equity-curve math assumes 1 unit of capital per trade (no
parallel positions). Real account drawdown is much smaller. If you want to
see more combos pass, raise `max_drawdown_pct` to 50 or higher.

`min_annual_sharpe` is a per-trade Sharpe scaled by sqrt(trades_per_year).
Approximate. Use to rank, not to certify ship-readiness.

## What this is NOT

- A walk-forward / out-of-sample validator. The Pareto frontier shows what
  worked best **in-sample**. Always re-test top candidates on held-out data
  before considering them production-worthy.
- A position-sized portfolio backtest. The harness models each trade as
  100% of capital, sequentially. For real portfolio metrics, use
  `trading-bot/backtest/runner.py`.
- Locked to EP. Any per-trade DataFrame with the right columns
  (CHG-OPEN%, close_in_range, atr_pct, prev_10d, hold-period checkpoints)
  works.
