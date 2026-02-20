# Verification Plan

---

## Unit Tests

Run all tests: `cd trading-bot && .venv/bin/pytest tests/ -v`

Current test count: **155 tests** across 5 test files, all passing.

### Signal tests (`tests/test_signals.py`)

| Test area | What's tested |
|---|---|
| Breakout entry | ORH break + volume + above 10d/20d MA |
| Breakout stop | LOD-based stop, ATR cap at 1x ATR(14) |
| EP entry | ORH break + gap >= 10% + volume > 2x |
| EP stop | LOD-based stop, ATR cap at 1.5x ATR(14) |
| Parabolic entry | ORB low break + VWAP failure |
| ORH computation | Correct float from 5m window |
| ATR computation | `compute_atr_from_list()` with known values |
| Negative cases | Below MA, insufficient volume, no gap, etc. |

### Scanner tests (`tests/test_scanners.py`)

| Test area | What's tested |
|---|---|
| Consolidation | ATR contraction, higher lows, dual MA (10d + 20d) |
| Consolidation prior move | Rejects stocks without 30%+ prior move |
| Consolidation min duration | Rejects consolidations shorter than 10 days |
| Gapper | Gap detection, prior-rally filter (rejects 50%+ in 6m) |
| Momentum rank | RS scoring and ranking |
| Parabolic | Multi-day runner detection, market-cap thresholds |

### Risk manager tests (`tests/test_risk.py`)

| Test area | What's tested |
|---|---|
| Position sizing | R-based formula, 10% notional cap, rounding down |
| Exposure checks | Max positions, notional limits |
| Loss limits | Daily and weekly loss halt logic |

### Backtest tests (`tests/test_backtest.py`)

| Test area | What's tested |
|---|---|
| Metrics computation | Empty trades, all winners, mixed trades, Sharpe, calmar, avg days held, max consec losses, trades/month |
| Max drawdown | No drawdown, simple drawdown, single point |
| Runner | Empty universe, synthetic data run, position sizing, max positions, equity curve |
| Position | Gain % for long and short positions |
| Partial exit P&L | Long and short partial P&L included in trade total |
| Portfolio sizing | Position sizing uses portfolio_value, not cash |
| Ticker shuffle | Different order per day, reproducible with seed |
| Short cash accounting | Cash added on short open, deducted on close, correct equity |
| Slippage | Entry/exit slippage applied correctly for long and short |

### Main tests (`tests/test_main.py`)

| Test area | What's tested |
|---|---|
| Scheduler | Job registration, trading day checks |
| Signal evaluation | Adapter wiring, config passing |

---

## Backtesting

### How to Run

```bash
cd trading-bot

# Default: 20 liquid stocks, 2022-2024, $100k capital
.venv/bin/python run_backtest.py

# Specific tickers
.venv/bin/python run_backtest.py --tickers AAPL MSFT NVDA --start 2023-01-01 --end 2024-12-31

# Single strategy
.venv/bin/python run_backtest.py --setup breakout
.venv/bin/python run_backtest.py --setup episodic_pivot
.venv/bin/python run_backtest.py --setup parabolic_short

# S&P 500 universe (slow, ~500 tickers)
.venv/bin/python run_backtest.py --sp500 --start 2023-01-01 --end 2024-06-30

# Custom capital and position limits
.venv/bin/python run_backtest.py --capital 50000 --max-positions 6

# Verbose debug logging
.venv/bin/python run_backtest.py -v
```

### How It Works

The backtest engine (`backtest/runner.py`) simulates trading day-by-day using daily OHLCV bars:

1. **Data**: downloads historical bars via yfinance, caches as parquet in `backtest_cache/`
2. **Daily loop** (skips warmup period for indicator history):
   - Process exits on open positions (stops, trailing MA close, parabolic targets, partials)
   - Recompute portfolio equity for position sizing
   - Scan tickers in randomized order for entry signals (breakout, EP, parabolic)
   - Record daily equity (cash + mark-to-market positions)
3. **Entry approximations** (since we only have daily bars, not intraday):
   - Breakout: today's high breaks 5-day resistance with volume surge
   - EP: 10%+ gap up with 2x volume, entry at `open + fraction*(high-open)`
   - Parabolic: 50%+ gain in 3 days + red reversal candle
4. **Output**: metrics, trade log, setup breakdown, equity curve, target check

### CLI Output

The `run_backtest.py` script prints:
- **Metrics table**: total trades, win rate, avg winner/loser, W/L ratio, profit factor, Sharpe, max drawdown, CAGR, calmar, avg days held, max consecutive losses, trades/month
- **Trade log**: each trade with ticker, setup, side, entry/exit dates, prices, P&L, exit reason
- **Setup breakdown**: per-strategy summary stats
- **Equity curve**: start/end/peak/trough values
- **Target check**: pass/fail against 5 performance targets

### Parameter Sweep Analysis

Full parameter sweep and optimization via `backtest/sweep.py`:

```bash
# Baseline only (quick)
.venv/bin/python -m backtest.sweep

# Full analysis with OAT sweeps, grid search, OOS validation
.venv/bin/python -m backtest.sweep --full

# With S&P 500 universe
.venv/bin/python -m backtest.sweep --full --sp500
```

Results are saved to `backtest_results/` as JSON files.

### Cached Data

Historical bars are cached in `backtest_cache/` as parquet files (e.g., `AAPL_2019-01-01_2024-12-31.parquet`). Delete the cache directory to force re-download.

### Programmatic Access

```python
from backtest.data import fetch_historical_bars
from backtest.runner import BacktestRunner, BacktestConfig

bars = fetch_historical_bars(["AAPL", "MSFT"], "2023-01-01", "2024-12-31")
runner = BacktestRunner(BacktestConfig(initial_capital=100_000))
metrics = runner.run(bars)

# Individual trades
for t in runner.trades:
    print(f"{t.ticker} {t.setup_type} P&L=${t.pnl:+,.2f}")

# Equity curve
print(runner.daily_equity[:10])
```

### Backtest Targets

| Metric | Target | Notes |
|---|---|---|
| Win rate | > 45% | Momentum strategies typically 35-50% |
| Avg winner / avg loser ratio | > 3x | Asymmetric risk/reward is the edge |
| Sharpe ratio | > 1.0 | Risk-adjusted return |
| Max drawdown | < 20% | Capital preservation |
| Profit factor | > 2.0 | Gross profit / gross loss |

### Results — 6-Year Analysis (20 tickers, 2019-2024)

**Baseline: In-Sample (2019-2021) vs Out-of-Sample (2022-2024)**

| Strategy | Period | Trades | Win% | Sharpe | CAGR% | MaxDD% | PF | Calmar |
|---|---|---|---|---|---|---|---|---|
| EP | IS | 13 | 30.8 | 0.41 | 2.4 | 6.1 | 1.78 | 0.39 |
| EP | OOS | 36 | 36.1 | 1.08 | 10.7 | 8.7 | 2.55 | 1.23 |
| Breakout | IS | 7 | 14.3 | 0.20 | 0.6 | 3.4 | 1.75 | 0.19 |
| Breakout | OOS | 8 | 12.5 | -0.03 | -0.2 | 3.8 | 0.88 | 0.05 |
| Parabolic Short | IS | 9 | 22.2 | -0.83 | -2.0 | 7.7 | 0.14 | 0.26 |
| Parabolic Short | OOS | 6 | 33.3 | -0.39 | -1.0 | 5.8 | 0.24 | 0.18 |
| Combined | IS | 27 | 25.9 | 0.26 | 1.7 | 10.2 | 1.29 | 0.16 |
| Combined | OOS | 46 | 32.6 | 0.99 | 10.4 | 9.8 | 2.17 | 1.06 |
| SPY buy&hold | IS | — | — | 1.17 | 26.0 | 33.7 | — | 0.77 |
| SPY buy&hold | OOS | — | — | 0.57 | 8.8 | 24.5 | — | 0.36 |

**Tuned vs Default (OOS validation)**

| Config | Trades | Win% | Sharpe | CAGR% | MaxDD% | PF |
|---|---|---|---|---|---|---|
| Default (OOS) | 46 | 32.6 | 0.99 | 10.4 | 9.8 | 2.17 |
| Tuned (OOS) | 42 | 30.9 | 1.29 | 18.1 | 9.3 | 3.29 |
| SPY (OOS) | — | — | 0.57 | 8.8 | 24.5 | — |

IS/OOS Sharpe ratio: 0.86 (no overfitting detected).

**Top sensitive parameters** (by Sharpe impact):
1. `breakout_volume_multiplier` (2.31) — lowering from 1.5x to 1.0x dramatically increases breakout trades
2. `breakout_consolidation_days` (1.19) — longer consolidation = more/better setups
3. `partial_exit_gain_pct` (0.92) — raising from 15% to 30% lets winners run longer
4. `partial_exit_after_days` (0.18) — 10-day wait improves Sharpe from 0.26 to 0.44

**Key findings**:
- EP is the strongest strategy (Sharpe 1.08 OOS, Calmar 1.23)
- Parabolic short is unprofitable on this universe — negative Sharpe in all periods
- Tuned combined config (partial_exit_gain=30%, partial_exit_after_days=10) improves OOS Sharpe from 0.99 to 1.29
- Strategy beats SPY on Sharpe (1.29 vs 0.57) and max drawdown (9.3% vs 24.5%), but underperforms on raw CAGR (18.1% vs 8.8% — note SPY's 26% CAGR in 2019-2021 is hard to beat)
- Breakout strategy needs lower volume threshold (1.0x vs 1.5x) to generate sufficient trades on 20 tickers

**Known limitations**:
- Survivorship bias: S&P 500 list is current, not historical
- No slippage assumed (configurable via `slippage_bps`)
- No borrow cost for short positions
- EP entry approximated from daily bars (`open + fraction*(high-open)`)
- Daily bars only — all entries/exits approximated from OHLCV

---

## Paper Trading Checklist

Run `environment: paper` for 3-4 weeks. Verify each item:

### Signal & Entry
- [ ] EP signal fires correctly the morning after a real earnings gap-up
- [ ] Breakout signal fires on valid ORH break with volume confirmation
- [ ] No signals fire in the first 5 minutes (before 9:35 AM ET)
- [ ] No entries placed if 4 positions already open

### Stop Placement
- [ ] Stop order placed within 5 seconds of fill confirmation
- [ ] Stop price = LOD, capped by ATR (1x for breakout, 1.5x for EP)
- [ ] Stop order is on the correct side (sell stop for long, buy stop for short)

### Partial Exit
- [ ] Partial exit fires automatically at day 3+ when gain >= 15%
- [ ] Correct fraction of shares sold (40%)
- [ ] Stop moves to break-even after partial exit

### Trailing Stop
- [ ] Trailing MA close check runs at 4:00 PM ET
- [ ] Exit fires on daily close below 10d MA (not on intraday touch)
- [ ] Position closes correctly when close < 10d MA

### Parabolic Targets
- [ ] Cover 50% at 10d MA for short positions
- [ ] Cover remaining at 20d MA
- [ ] Exit reason logged as `parabolic_target`

### Risk Controls
- [ ] Daily loss limit halts all trading correctly
- [ ] Weekly loss limit halts correctly
- [ ] Bot resumes next day / next week after halt (not permanently halted)

### Infrastructure
- [ ] Telegram alerts arrive within 30 seconds for all event types
- [ ] Dashboard shows correct live position data
- [ ] Manual flatten from dashboard closes position correctly
- [ ] Edge case: no fills (order timeout, thin liquidity) handled gracefully
- [ ] Edge case: early market close (e.g., day before Thanksgiving) handled
- [ ] Edge case: trading halt on a position symbol handled

---

## Pre-Live Checklist

- [ ] All unit tests passing (`pytest tests/ -v`)
- [ ] Backtests show positive expectancy (all metrics above targets)
- [ ] 3+ weeks paper trading with no critical bugs
- [ ] Paper P&L aligned with backtest expectations (within reason)
- [ ] Config reviewed: correct Alpaca account, `environment: live`
- [ ] Risk params set conservatively: `risk_per_trade_pct: 0.5`, `max_positions: 2`
- [ ] Kill switch tested: manual flatten from dashboard closes position in Alpaca
- [ ] Telegram bot confirmed active and responsive
- [ ] Database backup procedure in place
