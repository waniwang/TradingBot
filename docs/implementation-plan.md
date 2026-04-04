# Implementation Plan

> Phases are sequential. Complete and verify each phase before starting the next.

---

## Phase 1 — Foundation (Complete)

**Goal**: Working skeleton with Alpaca paper connection and data flow.

- [x] Initialize project with pip/venv
- [x] `executor/alpaca_client.py`: connect, authenticate, verify paper mode
- [x] Test: pull real-time quote for AAPL, place a paper limit order, cancel it
- [x] `db/models.py`: create SQLite DB, verify tables create on startup

**Exit criteria**: Can place and cancel a paper order via Alpaca API.

---

## Phase 2 — Pre-Market Scanners (Complete)

**Goal**: Automated watchlist generation before market open.

- [x] `scanner/gapper.py`: query Alpaca screener/snapshots for pre-market movers; filter by gap % and volume
- [x] `scanner/gapper.py`: prior-rally filter — reject stocks up 50%+ in prior 6 months
- [x] `scanner/momentum_rank.py`: rank stocks by 1m/3m/6m % change via yfinance; return top 20
- [x] `scanner/consolidation.py`: detect ATR contraction + higher lows + dual MA (10d + 20d)
- [x] `scanner/consolidation.py`: prior large move validation (30%+ in ~2 months)
- [x] `scanner/consolidation.py`: enforce min consolidation duration (10 days)
- [x] `scanner/parabolic.py`: detect multi-day runners with market-cap differentiated thresholds
- [x] Wire into `main.py` APScheduler job at 6:00 AM ET
- [x] Output: JSON watchlist of candidates with setup type label

**Exit criteria**: Runs pre-market, produces a ranked watchlist with correct labels.

---

## Phase 3 — Signal Modules (Complete)

**Goal**: Detect intraday entry signals on watchlist stocks.

- [x] `signals/base.py`: ORH/ORB computation, VWAP, SMA, ATR helpers
- [x] `signals/breakout.py`: price breaks ORH + above 10d & 20d MA + volume > 1.5x avg -> emit signal
- [x] `signals/breakout.py`: stop = LOD capped at 1x ATR(14)
- [x] `signals/episodic_pivot.py`: price breaks ORH + volume > 2x avg -> emit signal
- [x] `signals/episodic_pivot.py`: stop = LOD capped at 1.5x ATR(14)
- [x] `signals/parabolic_short.py`: price breaks ORB low + VWAP failure -> emit short signal
- [x] Unit tests with synthetic OHLCV data (positive and negative cases, ATR cap tests)
- [x] Wire into `main.py` intraday loop

**Exit criteria**: Unit tests pass; signals fire correctly in paper replay.

---

## Phase 4 — Risk Manager & Order Executor (Complete)

**Goal**: Full automated entry, stop placement, and partial exit logic.

- [x] `risk/manager.py`: `calculate_position_size()`, `check_exposure()`, `check_daily_loss()`
- [x] `executor/alpaca_client.py`: `place_limit_order()`, `place_stop_order()`, `cancel_order()`, `close_position()`
- [x] On signal: size position -> place limit entry -> on fill -> place stop -> log to DB
- [x] `monitor/position_tracker.py`: stop checks, partial exits (40% at 3+ days / 15% gain)
- [x] `monitor/position_tracker.py`: trailing MA close exits (daily close < 10d MA, not intraday)
- [x] `monitor/position_tracker.py`: parabolic profit targets (cover at 10d/20d MA)
- [x] End-of-day: compute trailing stop level, check MA close exits

**Exit criteria**: In paper trading, bot places entries, stops, and partial exits automatically.

---

## Phase 5 — Backtesting (Complete)

**Goal**: Validate each setup has positive expectancy on historical data.

- [x] `backtest/data.py`: download daily OHLCV via yfinance with parquet caching
- [x] `backtest/runner.py`: custom daily-bar-by-bar simulation engine
- [x] `backtest/metrics.py`: win rate, avg W/L, profit factor, Sharpe, max drawdown, CAGR
- [x] `run_backtest.py`: CLI with argparse for running backtests
- [x] `tests/test_backtest.py`: unit tests for metrics, runner, positions
- [x] Entry approximations: breakout (5-day high), EP (gap + ORH proxy), parabolic (reversal)
- [x] All 3 setups simulated with proper exits (stops, trailing MA close, parabolic targets)

**Target metrics** (see [backtesting.md](backtesting.md) for latest results):

| Metric | Target |
|---|---|
| Win rate | > 45% |
| Avg winner / avg loser ratio | > 3x |
| Sharpe ratio | > 1.0 |
| Max drawdown | < 20% |
| Profit factor | > 2.0 |

**Exit criteria**: Backtest runs end-to-end on historical data with meaningful results.

---

## Phase 6 — Paper Trading (Pending)

**Goal**: Validate live bot behavior on Alpaca paper account.

- [ ] Run full bot with `environment: paper` for 3-4 weeks
- [ ] Monitor: signal quality, fill behavior, stop logic, partial exits
- [ ] Track paper P&L vs backtest expectations
- [ ] Fix edge cases: pre-market halts, no-fill scenarios, early close days, thin liquidity

**Exit criteria**: 3-4 weeks of paper trading with no critical bugs; results roughly aligned with backtest.

---

## Phase 7 — Dashboard & Telegram (Complete)

**Goal**: Real-time visibility into bot activity.

- [x] Next.js 16 frontend (`dashboard/`): positions, watchlist, performance, history pages
- [x] FastAPI backend (`trading-bot/api/`): read-only API with auth, shares DB models with bot
- [x] Telegram: send alert on bot started, scan start/finish, entry fill, stop fill, trading halted, EOD summary, errors
- [x] Strategy plugin architecture: all strategies refactored into self-contained packages under `strategies/`
- [x] EP Earnings + EP News swing strategies added as plugins
- [x] GitHub Actions CI/CD auto-deploy pipeline

**Exit criteria**: Dashboard shows live data; Telegram delivers alerts within 30 seconds of events.

---

## Phase 8 — Live Trading (Pending)

**Goal**: Deploy with real capital, start small.

- [ ] Switch `environment: live` in `config.yaml`
- [ ] Start: `risk_per_trade_pct: 0.5`, `max_positions: 2`
- [ ] Run for 4-6 weeks, review performance vs paper
- [ ] Scale up to 1% risk, 4 positions max after validation

---

## Pre-Live Checklist

Before switching to live:

- [ ] All unit tests passing (`pytest tests/ -v`)
- [ ] Backtests show positive expectancy (metrics above targets)
- [ ] 3+ weeks paper trading with no critical issues
- [ ] Config for live environment reviewed (correct account, `environment: live`)
- [ ] Risk params set conservatively (0.5% risk, 2 positions max)
- [ ] Kill switch tested: manual flatten from dashboard works
- [ ] Telegram alerts confirmed working for all event types
