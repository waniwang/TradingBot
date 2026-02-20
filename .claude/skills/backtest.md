---
description: Run a backtest with specified parameters
user_invocable: true
---

# Run Backtest

Run a backtest using the trading bot's backtesting engine.

## Steps

1. Ask the user for parameters (or use defaults):
   - Tickers: specific list or `--sp500` for full S&P 500
   - Date range: `--start YYYY-MM-DD --end YYYY-MM-DD` (default: last 2 years)
   - Setup filter: `--setup breakout|episodic_pivot|parabolic_short` (default: all enabled)
   - Capital: `--capital N` (default: 100000)
   - Max positions: `--max-positions N` (default: 4)

2. Run: `cd /Users/hanlin/Developer/Trading/trading-bot && .venv/bin/python run_backtest.py [args]`
   - Note: S&P 500 backtest with yfinance takes ~14 min for data download (batches of 500)

3. Present results:
   - Key metrics: total return, CAGR, Sharpe, max drawdown, win rate, profit factor
   - Setup breakdown if multiple setups ran
   - Compare to known benchmarks: EP Sharpe ~1.08 OOS, combined tuned ~1.29 OOS

## For parameter sweeps
Use the sweep module directly:
```bash
.venv/bin/python -c "from backtest.sweep import run_sweep; run_sweep(...)"
```
See `backtest/sweep.py` for OAT + grid + OOS analysis options.
