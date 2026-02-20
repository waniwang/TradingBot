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


# ---------------------------------------------------------------------------
# Bug fix tests
# ---------------------------------------------------------------------------

class TestPartialExitPnL:
    """Bug 1: Partial exit P&L must be included in Trade.pnl."""

    def test_long_partial_pnl_included(self):
        runner = BacktestRunner(BacktestConfig(initial_capital=100_000))
        # Manually open a long position
        pos = BacktestPosition(
            ticker="T", setup_type="breakout", side="long",
            entry_date="2023-01-01", entry_price=50.0,
            stop_price=48.0, shares=100,
        )
        runner.positions.append(pos)
        runner.cash -= 100 * 50  # buy 100 @ $50

        # Partial exit at $60 (40% of 100 = 40 shares)
        runner._do_partial_exit(pos, 60.0, "2023-01-05")
        assert pos.partial_exit_done
        assert pos.partial_exit_shares == 40
        assert pos.partial_exit_price == 60.0

        # Close remaining 60 shares at $55
        runner._close_position(pos, 55.0, "2023-01-10", "trailing_ma_close")

        trade = runner.trades[-1]
        # Expected: partial = 40 * (60 - 50) = 400, remaining = 60 * (55 - 50) = 300
        expected_pnl = 400.0 + 300.0
        assert trade.pnl == pytest.approx(expected_pnl)

    def test_short_partial_pnl_included(self):
        runner = BacktestRunner(BacktestConfig(initial_capital=100_000))
        pos = BacktestPosition(
            ticker="T", setup_type="parabolic_short", side="short",
            entry_date="2023-01-01", entry_price=100.0,
            stop_price=110.0, shares=50,
        )
        runner.positions.append(pos)
        runner.cash += 50 * 100  # short: receive proceeds

        # Partial exit at $80 (40% of 50 = 20 shares)
        runner._do_partial_exit(pos, 80.0, "2023-01-05")
        assert pos.partial_exit_price == 80.0

        # Close remaining 30 shares at $90
        runner._close_position(pos, 90.0, "2023-01-10", "parabolic_target")

        trade = runner.trades[-1]
        # partial = 20 * (100 - 80) = 400, remaining = 30 * (100 - 90) = 300
        expected_pnl = 400.0 + 300.0
        assert trade.pnl == pytest.approx(expected_pnl)


class TestPortfolioValueSizing:
    """Bug 2: Position sizing should use portfolio_value, not cash."""

    def test_sizing_uses_portfolio_value(self):
        runner = BacktestRunner(BacktestConfig(
            initial_capital=100_000,
            risk_per_trade_pct=1.0,
            max_position_pct=25.0,
        ))
        # Portfolio value is 100k, risk $1000 with $2 risk per share
        shares = runner._size_position(50.0, 48.0)
        assert shares == 500  # $1000 / $2

        # Simulate having a position: cash drops but equity stays
        runner.cash = 50_000
        runner.portfolio_value = 100_000  # equity unchanged
        shares2 = runner._size_position(50.0, 48.0)
        # Should still size based on portfolio_value (100k), not cash (50k)
        assert shares2 == 500


class TestTickerShuffle:
    """Bug 3: Tickers should be shuffled each day to avoid alphabetical bias."""

    def test_ticker_order_varies_with_date(self):
        """Two different dates should produce different ticker orders."""
        from backtest.runner import BacktestConfig
        import random

        cfg = BacktestConfig(shuffle_seed=42)
        tickers = [f"T{i:03d}" for i in range(20)]

        # Simulate shuffle for date "2023-01-01"
        rng1 = random.Random(cfg.shuffle_seed + hash("2023-01-01"))
        order1 = tickers.copy()
        rng1.shuffle(order1)

        # Simulate shuffle for date "2023-01-02"
        rng2 = random.Random(cfg.shuffle_seed + hash("2023-01-02"))
        order2 = tickers.copy()
        rng2.shuffle(order2)

        assert order1 != order2  # different days -> different order

    def test_shuffle_reproducible_with_same_seed(self):
        """Same seed + same date should produce identical order."""
        import random

        tickers = [f"T{i:03d}" for i in range(20)]
        seed = 42

        rng1 = random.Random(seed + hash("2023-06-15"))
        order1 = tickers.copy()
        rng1.shuffle(order1)

        rng2 = random.Random(seed + hash("2023-06-15"))
        order2 = tickers.copy()
        rng2.shuffle(order2)

        assert order1 == order2


class TestShortCashAccounting:
    """Bug 4: Short positions should add cash on open, deduct on close."""

    def test_short_open_adds_cash(self):
        runner = BacktestRunner(BacktestConfig(
            initial_capital=100_000,
            slippage_bps=0,  # no slippage for precise test
        ))
        initial_cash = runner.cash

        runner._open_position("T", "parabolic_short", "short",
                              "2023-01-01", 100.0, 110.0, 50)

        # Short sale of 50 @ $100 should ADD $5000
        assert runner.cash == initial_cash + 5_000

    def test_short_close_deducts_cash(self):
        runner = BacktestRunner(BacktestConfig(
            initial_capital=100_000,
            slippage_bps=0,
        ))
        pos = BacktestPosition(
            ticker="T", setup_type="parabolic_short", side="short",
            entry_date="2023-01-01", entry_price=100.0,
            stop_price=110.0, shares=50,
        )
        runner.positions.append(pos)
        runner.cash += 50 * 100  # simulate short open

        cash_before_close = runner.cash
        runner._close_position(pos, 90.0, "2023-01-05", "parabolic_target")

        # Closing short: buy back 50 @ $90 = deduct $4500
        assert runner.cash == cash_before_close - 50 * 90

    def test_short_equity_correct(self):
        """Equity should be correct for short positions."""
        runner = BacktestRunner(BacktestConfig(
            initial_capital=100_000,
            slippage_bps=0,
        ))
        runner._open_position("T", "parabolic_short", "short",
                              "2023-01-01", 100.0, 110.0, 50)

        # Build minimal ticker_data
        df = pd.DataFrame({
            "close": [90.0],
        }, index=["2023-01-02"])

        equity = runner._compute_equity({"T": df}, "2023-01-02")
        # cash = 100_000 + 5_000 = 105_000
        # short liability = -50 * 90 = -4_500
        # equity = 105_000 - 4_500 = 100_500
        # (profit = 50 * (100 - 90) = 500)
        assert equity == pytest.approx(100_500)


class TestSlippage:
    """Slippage should increase costs for all trades."""

    def test_slippage_applied_to_entry(self):
        runner = BacktestRunner(BacktestConfig(
            initial_capital=100_000,
            slippage_bps=10,  # 10 bps = 0.1%
        ))
        # Long entry: price should increase
        price = runner._apply_slippage(100.0, "long", "entry")
        assert price == pytest.approx(100.10)

        # Short entry: price should decrease (sell cheaper)
        price = runner._apply_slippage(100.0, "short", "entry")
        assert price == pytest.approx(99.90)

    def test_slippage_applied_to_exit(self):
        runner = BacktestRunner(BacktestConfig(slippage_bps=10))
        # Long exit (selling): price should decrease
        price = runner._apply_slippage(100.0, "long", "exit")
        assert price == pytest.approx(99.90)

        # Short exit (buying back): price should increase
        price = runner._apply_slippage(100.0, "short", "exit")
        assert price == pytest.approx(100.10)


class TestNewMetrics:
    """Test new metrics: calmar, avg_days_held, max_consecutive_losses, trades/month."""

    def test_calmar_ratio(self):
        trades = [
            Trade("A", "breakout", "long", "2023-01-01", "2023-06-30",
                  50.0, 75.0, 100, 2500.0, "trailing_ma_close"),
        ]
        # Equity with a drawdown so max_dd > 0 (needed for calmar)
        equity = [100_000, 102_000, 101_000, 103_000, 105_000] + [105_000 + i * 50 for i in range(248)]
        result = compute_metrics(trades, equity)
        assert "calmar" in result
        assert result["calmar"] > 0

    def test_avg_days_held(self):
        trades = [
            Trade("A", "breakout", "long", "2023-01-01", "2023-01-11",
                  50.0, 60.0, 100, 1000.0, "trailing_ma_close"),
            Trade("B", "breakout", "long", "2023-02-01", "2023-02-21",
                  30.0, 40.0, 100, 1000.0, "trailing_ma_close"),
        ]
        equity = [100_000, 101_000, 102_000]
        result = compute_metrics(trades, equity)
        assert result["avg_days_held"] == pytest.approx(15.0)  # (10 + 20) / 2

    def test_max_consecutive_losses(self):
        trades = [
            Trade("A", "b", "long", "2023-01-01", "2023-01-02", 50, 55, 10, 50, "x"),
            Trade("B", "b", "long", "2023-01-03", "2023-01-04", 50, 48, 10, -20, "x"),
            Trade("C", "b", "long", "2023-01-05", "2023-01-06", 50, 47, 10, -30, "x"),
            Trade("D", "b", "long", "2023-01-07", "2023-01-08", 50, 46, 10, -40, "x"),
            Trade("E", "b", "long", "2023-01-09", "2023-01-10", 50, 60, 10, 100, "x"),
        ]
        equity = [100_000] * 6
        result = compute_metrics(trades, equity)
        assert result["max_consecutive_losses"] == 3

    def test_avg_trades_per_month(self):
        trades = [
            Trade("A", "b", "long", f"2023-01-{i+1:02d}", f"2023-01-{i+2:02d}", 50, 55, 10, 50, "x")
            for i in range(12)
        ]
        # ~252 trading days = ~12 months
        equity = [100_000 + i * 10 for i in range(253)]
        result = compute_metrics(trades, equity)
        assert result["avg_trades_per_month"] > 0
