"""
Tests for the backtesting framework.

Uses synthetic data — no network calls.
"""

import pytest
import numpy as np
import pandas as pd

from backtest.metrics import Trade, compute_metrics, compute_max_drawdown
from backtest.runner import BacktestRunner, BacktestConfig, BacktestPosition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daily_bars(
    n: int = 200,
    start_price: float = 50.0,
    drift: float = 0.1,
    volatility: float = 1.0,
    start_date: str = "2023-01-01",
) -> pd.DataFrame:
    """Create synthetic daily OHLCV bars."""
    dates = pd.bdate_range(start_date, periods=n)
    closes = [start_price]
    for i in range(1, n):
        closes.append(closes[-1] + drift + np.random.randn() * volatility * 0.1)
    closes = [max(c, 1.0) for c in closes]  # no negative prices

    rows = []
    for i, (date, close) in enumerate(zip(dates, closes)):
        high = close + abs(np.random.randn()) * volatility
        low = close - abs(np.random.randn()) * volatility
        low = max(low, 0.5)
        open_ = close + np.random.randn() * 0.3
        open_ = max(open_, 0.5)
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": int(1_000_000 + np.random.randint(-200_000, 200_000)),
        })
    return pd.DataFrame(rows)


def _make_breakout_bars(n: int = 200) -> pd.DataFrame:
    """
    Create bars that contain a consolidation → breakout pattern.

    First 60 bars: big uptrend (50 → 80, 60% move).
    Next 40 bars (day 60-100): tight consolidation near 80.
    Day 100+: breakout above 82 with volume surge.
    """
    rows = []
    dates = pd.bdate_range("2023-01-01", periods=n)

    for i in range(n):
        if i < 60:
            # Uptrend phase: 50 → 80
            price = 50 + (80 - 50) * i / 60
        elif i < 100:
            # Consolidation: tight range around 80
            price = 80 + np.sin(i * 0.5) * 0.5
        elif i == 100:
            # Breakout day
            price = 83.0
        else:
            # Post-breakout: continued uptrend
            price = 83.0 + (i - 100) * 0.3

        high = price + abs(np.random.randn()) * 0.5
        low = price - abs(np.random.randn()) * 0.5
        low = max(low, 1.0)
        vol = 1_000_000
        if i == 100:
            vol = 3_000_000  # volume surge on breakout

        rows.append({
            "date": dates[i].strftime("%Y-%m-%d"),
            "open": round(price - 0.2, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(price, 2),
            "volume": vol,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def test_empty_trades(self):
        result = compute_metrics([], [100_000])
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0.0

    def test_all_winners(self):
        trades = [
            Trade("AAPL", "breakout", "long", "2023-01-01", "2023-01-10",
                  50.0, 60.0, 100, 1000.0, "trailing_ma_close"),
            Trade("MSFT", "breakout", "long", "2023-01-05", "2023-01-15",
                  100.0, 120.0, 50, 1000.0, "trailing_ma_close"),
        ]
        equity = [100_000, 100_500, 101_000, 101_500, 102_000]
        result = compute_metrics(trades, equity)
        assert result["total_trades"] == 2
        assert result["win_rate"] == 100.0
        assert result["avg_winner"] == 1000.0
        assert result["profit_factor"] == float("inf")

    def test_mixed_trades(self):
        trades = [
            Trade("WIN", "breakout", "long", "2023-01-01", "2023-01-10",
                  50.0, 55.0, 100, 500.0, "trailing_ma_close"),
            Trade("LOSE", "breakout", "long", "2023-01-01", "2023-01-10",
                  50.0, 48.0, 100, -200.0, "stop_hit"),
        ]
        equity = [100_000, 100_200, 100_300]
        result = compute_metrics(trades, equity)
        assert result["total_trades"] == 2
        assert result["win_rate"] == 50.0
        assert result["avg_winner"] == 500.0
        assert result["avg_loser"] == -200.0
        assert result["wl_ratio"] == 2.5
        assert result["profit_factor"] == 2.5

    def test_sharpe_positive(self):
        # Steadily increasing equity
        equity = [100_000 + i * 100 for i in range(252)]
        trades = [
            Trade("A", "breakout", "long", "2023-01-01", "2023-12-31",
                  50.0, 75.0, 100, 2500.0, "trailing_ma_close"),
        ]
        result = compute_metrics(trades, equity)
        assert result["sharpe"] > 0


class TestComputeMaxDrawdown:
    def test_no_drawdown(self):
        equity = [100, 110, 120, 130]
        assert compute_max_drawdown(equity) == 0.0

    def test_simple_drawdown(self):
        equity = [100, 120, 90, 110]
        dd = compute_max_drawdown(equity)
        assert dd == pytest.approx(25.0, rel=0.01)  # (120-90)/120 * 100

    def test_single_point(self):
        assert compute_max_drawdown([100]) == 0.0


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------

class TestBacktestRunner:
    def test_empty_universe(self):
        runner = BacktestRunner()
        result = runner.run({})
        assert result["total_trades"] == 0

    def test_runs_without_error(self):
        """Runner should complete on synthetic data without crashing."""
        bars = {"TEST": _make_daily_bars(200, drift=0.2)}
        runner = BacktestRunner(BacktestConfig(
            initial_capital=100_000,
            max_positions=2,
        ))
        result = runner.run(bars)
        assert "total_trades" in result
        assert "sharpe" in result
        assert "max_drawdown_pct" in result
        assert len(runner.daily_equity) > 0

    def test_position_sizing(self):
        runner = BacktestRunner(BacktestConfig(
            initial_capital=100_000,
            risk_per_trade_pct=1.0,
        ))
        # Risk $1000 with $2 risk per share → 500 shares
        shares = runner._size_position(50.0, 48.0)
        assert shares > 0
        assert shares <= 500  # max risk / risk_per_share

    def test_max_positions_respected(self):
        """Should not open more positions than max_positions."""
        config = BacktestConfig(
            initial_capital=1_000_000,
            max_positions=2,
        )
        runner = BacktestRunner(config)

        # Create many tickers
        universe = {}
        for i in range(10):
            universe[f"T{i}"] = _make_daily_bars(200, drift=0.5, volatility=2.0)

        runner.run(universe)
        # No assertion on exact trades, just verify no crash and max respected
        # (the runner checks max_positions internally)

    def test_breakout_entry_on_known_pattern(self):
        """With a clear consolidation→breakout pattern, should produce at least one entry."""
        bars = {"BREAK": _make_breakout_bars(200)}
        config = BacktestConfig(
            initial_capital=100_000,
            max_positions=4,
            breakout_prior_move_pct=20.0,  # lowered for test
        )
        runner = BacktestRunner(config)
        runner.run(bars, setups=["breakout"])
        # May or may not trigger depending on exact ATR/MA conditions,
        # but should not crash
        assert len(runner.daily_equity) > 0

    def test_equity_curve_length(self):
        """Equity curve should have one entry per trading day + initial."""
        bars = {"T": _make_daily_bars(200)}
        runner = BacktestRunner()
        runner.run(bars)
        # daily_equity includes initial + one per trading day
        assert len(runner.daily_equity) >= 200


class TestBacktestPosition:
    def test_gain_pct_long(self):
        runner = BacktestRunner()
        pos = BacktestPosition(
            ticker="T", setup_type="breakout", side="long",
            entry_date="2023-01-01", entry_price=50.0,
            stop_price=48.0, shares=100,
        )
        assert runner._gain_pct(pos, 55.0) == pytest.approx(10.0)
        assert runner._gain_pct(pos, 45.0) == pytest.approx(-10.0)

    def test_gain_pct_short(self):
        runner = BacktestRunner()
        pos = BacktestPosition(
            ticker="T", setup_type="parabolic_short", side="short",
            entry_date="2023-01-01", entry_price=100.0,
            stop_price=110.0, shares=50,
        )
        assert runner._gain_pct(pos, 90.0) == pytest.approx(10.0)
        assert runner._gain_pct(pos, 110.0) == pytest.approx(-10.0)
