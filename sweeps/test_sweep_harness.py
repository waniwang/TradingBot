"""Sanity tests for the sweep harness. Run with:

    cd /Users/sharonk/Documents/TradingBot
    trading-bot/.venv/bin/pytest sweeps/test_sweep_harness.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sweeps.fitness import FitnessConfig, evaluate
from sweeps.harness import (
    BacktestRequest,
    apply_filters,
    compute_metrics,
    derive_columns,
    load_data,
    run_backtest,
    run_simple,
)

DATA_PATH = (
    Path(__file__).resolve().parent.parent
    / "market data download"
    / "2020-2025 EP Selection EARNINGS.xlsx"
)


@pytest.fixture(scope="module")
def df():
    if not DATA_PATH.exists():
        pytest.skip(f"data file missing: {DATA_PATH}")
    return load_data(str(DATA_PATH))


def test_data_loads_and_derives(df):
    assert len(df) > 100
    for col in ("atr_pct", "close_in_range", "mktcap_b", "ddv", "year", "close_to_low_pct"):
        assert col in df.columns


def test_derive_columns_idempotent(df):
    again = derive_columns(df)
    assert len(again) == len(df)


def test_apply_filters_strategy_b_baseline(df):
    # Production B baseline: chg_open_min=0, cir_min=50, atr 2-5, prev_10d_max=-10
    cfg = {
        "chg_open_min": 0,
        "close_in_range_min": 50,
        "atr_pct_min": 2,
        "atr_pct_max": 5,
        "prev_10d_max": -10,
    }
    trades = apply_filters(df, cfg)
    assert len(trades) > 30, "B baseline should yield >30 trades on 5yr data"


def test_run_simple_returns_dated_trades(df):
    cfg = {"chg_open_min": 0, "close_in_range_min": 50, "prev_10d_max": -10}
    trades = apply_filters(df, cfg)
    out = run_simple(trades, stop_pct=7.0, hold_period="50D")
    assert "Date" in out.columns
    assert "Return%" in out.columns
    assert "Stopped" in out.columns
    assert len(out) > 0


def test_compute_metrics_strategy_b_baseline(df):
    """Sanity: B baseline metrics should be in the right ballpark."""
    cfg = {
        "chg_open_min": 0,
        "close_in_range_min": 50,
        "atr_pct_min": 2,
        "atr_pct_max": 5,
        "prev_10d_max": -10,
    }
    trades = apply_filters(df, cfg)
    out = run_simple(trades, stop_pct=7.0, hold_period="50D")
    m = compute_metrics(out)
    assert m is not None
    assert m["n"] >= 30
    # Sanity tolerances — not strict reproduction of any historical number.
    # Actual on 2020-2025 dataset: WR=50%, avg=+8.2%, PF=3.5.
    assert 40 <= m["win_rate"] <= 80
    assert 3 < m["avg"] < 20
    assert m["profit_factor"] is not None and m["profit_factor"] > 2
    assert m["max_dd_pct"] <= 0  # drawdown is non-positive
    assert m["annual_sharpe"] > 1.0


def test_run_backtest_via_request_object(df):
    req = BacktestRequest(
        strategy_name="B-test",
        mode="simple",
        params=tuple({
            "chg_open_min": 0.0,
            "close_in_range_min": 50.0,
            "atr_pct_min": 2.0,
            "atr_pct_max": 5.0,
            "prev_10d_max": -10.0,
            "stop_pct": 7.0,
            "hold_period": "50D",
        }.items()),
        data_path=str(DATA_PATH),
    )
    r = run_backtest(req)
    assert not r.get("skipped"), r
    assert r["strategy_name"] == "B-test"
    assert r["mode"] == "simple"
    assert r["n"] > 30
    assert r["stop_pct"] == 7.0


def test_fitness_evaluator_accepts_strong_metrics():
    cfg = FitnessConfig(min_trades=10, min_profit_factor=1.5, min_annual_sharpe=0.5)
    metrics = {
        "n": 100, "win_rate": 60, "avg": 5.0, "profit_factor": 3.0,
        "annual_sharpe": 2.0, "max_dd_pct": -10.0, "skipped": False,
    }
    ok, failures = evaluate(metrics, cfg)
    assert ok, failures


def test_fitness_evaluator_rejects_weak_metrics():
    cfg = FitnessConfig(min_trades=30, min_profit_factor=1.5, min_annual_sharpe=1.0)
    metrics = {
        "n": 10, "win_rate": 40, "avg": 0.5, "profit_factor": 1.1,
        "annual_sharpe": 0.5, "max_dd_pct": -5.0, "skipped": False,
    }
    ok, failures = evaluate(metrics, cfg)
    assert not ok
    assert any("n=" in f for f in failures)
    assert any("profit_factor" in f for f in failures)
    assert any("annual_sharpe" in f for f in failures)


def test_fitness_evaluator_handles_skipped():
    cfg = FitnessConfig()
    ok, failures = evaluate({"skipped": True, "reason": "no_trades"}, cfg)
    assert not ok
    assert "skipped" in failures[0]


def test_mini_sweep_runs_endtoend():
    """3 combos serially via run_backtest. Doesn't use ProcessPoolExecutor (that's
    tested in the smoke test)."""
    if not DATA_PATH.exists():
        pytest.skip("data file missing")

    combos = [
        {"chg_open_min": 0.0, "close_in_range_min": 50.0, "atr_pct_min": 2.0,
         "atr_pct_max": 5.0, "prev_10d_max": -10.0, "stop_pct": 7.0, "hold_period": "50D"},
        {"chg_open_min": 0.0, "close_in_range_min": 50.0, "atr_pct_min": 2.0,
         "atr_pct_max": 5.0, "prev_10d_max": -10.0, "stop_pct": 6.0, "hold_period": "50D"},
        {"chg_open_min": 0.0, "close_in_range_min": 50.0, "atr_pct_min": 2.0,
         "atr_pct_max": 5.0, "prev_10d_max": -10.0, "stop_pct": 8.0, "hold_period": "50D"},
    ]
    results = []
    for params in combos:
        req = BacktestRequest(
            strategy_name="B-mini",
            mode="simple",
            params=tuple(params.items()),
            data_path=str(DATA_PATH),
        )
        r = run_backtest(req)
        assert not r.get("skipped"), r
        results.append(r)

    # All three should yield results, and they should differ on stop_pct.
    stop_vals = sorted(r["stop_pct"] for r in results)
    assert stop_vals == [6.0, 7.0, 8.0]
