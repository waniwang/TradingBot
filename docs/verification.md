# Verification Plan

---

## Unit Tests

Run all tests: `cd trading-bot && .venv/bin/pytest tests/ -v`

Current test count: **141 tests** across 5 test files, all passing.

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
| Metrics computation | Empty trades, all winners, mixed trades, Sharpe |
| Max drawdown | No drawdown, simple drawdown, single point |
| Runner | Empty universe, synthetic data run, position sizing, max positions, equity curve |
| Position | Gain % for long and short positions |

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

1. **Data**: downloads historical bars via yfinance, caches as parquet in `backtest/cache/`
2. **Daily loop** (skips first 130 days for indicator history):
   - Process exits on open positions (stops, trailing MA close, parabolic targets, partials)
   - Scan all tickers for entry signals (breakout, EP, parabolic)
   - Record daily equity (cash + mark-to-market positions)
3. **Entry approximations** (since we only have daily bars, not intraday):
   - Breakout: today's high breaks 5-day resistance with volume surge
   - EP: 10%+ gap up with 2x volume, entry at `open + 0.3*(high-open)`
   - Parabolic: 50%+ gain in 3 days + red reversal candle
4. **Output**: metrics, trade log, setup breakdown, equity curve, target check

### CLI Output

The `run_backtest.py` script prints:
- **Metrics table**: total trades, win rate, avg winner/loser, W/L ratio, profit factor, Sharpe, max drawdown, CAGR
- **Trade log**: each trade with ticker, setup, side, entry/exit dates, prices, P&L, exit reason
- **Setup breakdown**: per-strategy summary stats
- **Equity curve**: start/end/peak/trough values
- **Target check**: pass/fail against 5 performance targets

### Cached Data

Historical bars are cached in `backtest/cache/` as parquet files (e.g., `AAPL_2022-01-01_2024-12-31.parquet`). Delete the cache directory to force re-download.

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

### Recent Results (20 default tickers, 2022-2024)

| Metric | All Setups | EP Only | Breakout Only |
|---|---|---|---|
| Total trades | 46 | 36 | 8 |
| Win rate | 32.6% | 36.1% | 12.5% |
| W/L ratio | 3.11 | 3.19 | 3.78 |
| Sharpe | 1.05 | 1.13 | -0.03 |
| Max drawdown | 9.7% | 9.0% | 2.0% |
| Total return | 31.4% | 31.8% | -0.5% |

**Key findings**: EP is the primary edge driver. Breakout needs a larger universe (S&P 500+) to find more setups. W/L ratio and max drawdown targets are met; win rate and profit factor need more universe breadth.

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
