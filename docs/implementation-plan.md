# Implementation Plan

> Phases are sequential. Complete and verify each phase before starting the next.
> Check off items as they are completed.

---

## Phase 1 — Foundation (Week 1-2)

**Goal**: Working skeleton with Moomoo paper connection and data flow.

- [ ] Initialize project with Poetry (`pyproject.toml`, `config.yaml`)
- [ ] Install and run Moomoo OpenD gateway locally; verify connectivity
- [ ] `executor/moomoo_client.py`: connect, authenticate, verify `TrdEnv.SIMULATE`
- [ ] Test: pull real-time quote for AAPL, place a paper limit order, cancel it
- [ ] Polygon.io: test REST call, pull 1 month of OHLCV for a symbol
- [ ] `db/models.py`: create SQLite DB, verify tables create on startup

**Exit criteria**: Can place and cancel a paper order via API; can pull real-time quotes from Polygon.io.

---

## Phase 2 — Pre-Market Scanners (Week 2-3)

**Goal**: Automated watchlist generation before market open.

- [ ] `scanner/gapper.py`: query Polygon.io for pre-market movers; filter by gap % and volume
- [ ] `scanner/momentum_rank.py`: rank stocks by 1m/3m/6m % change; return top 20
- [ ] `scanner/consolidation.py`: for a given ticker, detect ATR contraction + higher lows over N days
- [ ] Wire into `main.py` APScheduler job at 6:00 AM ET
- [ ] Output: JSON watchlist of candidates with setup type label

**Exit criteria**: Runs pre-market, produces a ranked watchlist with correct labels.

---

## Phase 3 — Signal Modules (Week 3-4)

**Goal**: Detect intraday entry signals on watchlist stocks.

- [ ] `signals/base.py`: ORH/ORB computation from first N minutes of 1m candles
- [ ] `signals/breakout.py`: price breaks ORH + above 20d MA + volume > 1.5x avg → emit signal
- [ ] `signals/episodic_pivot.py`: price breaks ORH + volume > 2x premarket avg → emit signal
- [ ] `signals/parabolic_short.py`: price breaks ORB low + VWAP failure → emit short signal
- [ ] Unit tests with fixture OHLCV data (both positive and negative cases)
- [ ] Wire into `main.py` intraday loop, subscribe to Moomoo push callbacks

**Exit criteria**: Unit tests pass; signals fire correctly in paper replay.

---

## Phase 4 — Risk Manager & Order Executor (Week 4-5)

**Goal**: Full automated entry, stop placement, and partial exit logic.

- [ ] `risk/manager.py`: `calculate_position_size()`, `check_exposure()`, `check_daily_loss()`
- [ ] `executor/moomoo_client.py`: `place_limit_order()`, `place_stop_order()`, `cancel_order()`, `close_position()`
- [ ] On signal: size position → place limit entry → on fill → place stop → log to DB
- [ ] `monitor/position_tracker.py`: loop every 1m — check stops, check partial exit conditions
- [ ] Partial exit: sell fraction, move stop to break-even
- [ ] End-of-day: compute trailing stop level, update stop order

**Exit criteria**: In paper trading, bot places entries, stops, and partial exits automatically.

---

## Phase 5 — Backtesting (Week 5)

**Goal**: Validate each setup has positive expectancy on historical data.

- [ ] Pull 2022-2024 daily OHLCV for S&P 1500 from Polygon.io
- [ ] vectorbt: implement Breakout backtest — find consolidations, simulate ORH entries
- [ ] vectorbt: implement EP backtest — find gap-up days, simulate entries
- [ ] Compute: win rate, avg winner R, avg loser R, Sharpe ratio, max drawdown
- [ ] Tune parameters: consolidation length, MA period, gap threshold

**Target metrics**:

| Metric | Target |
|---|---|
| Win rate | > 45% |
| Avg winner / avg loser ratio | > 3x |
| Sharpe ratio | > 1.0 |
| Max drawdown | < 20% |

**Exit criteria**: All 3 setups show positive expectancy in backtest.

---

## Phase 6 — Paper Trading (Week 6-7)

**Goal**: Validate live bot behavior on Moomoo Simulate environment.

- [ ] Run full bot with `environment: simulate` for 3-4 weeks
- [ ] Monitor: signal quality, fill behavior, stop logic, partial exits
- [ ] Track paper P&L vs backtest expectations
- [ ] Fix edge cases: pre-market halts, no-fill scenarios, early close days, thin liquidity

**Exit criteria**: 3-4 weeks of paper trading with no critical bugs; results roughly aligned with backtest.

---

## Phase 7 — Streamlit Dashboard & Telegram (Week 7)

**Goal**: Real-time visibility into bot activity.

- [ ] `dashboard/app.py`: live positions table (entry, stop, current P&L, days held)
- [ ] Daily P&L chart, signal log, portfolio exposure gauge
- [ ] Telegram: send alert on entry fill, stop hit, partial exit, EOD summary
- [ ] Manual override: button in dashboard to flatten a position

**Exit criteria**: Dashboard shows live data; Telegram delivers alerts within 30 seconds of events.

---

## Phase 8 — Live Trading (Week 8+)

**Goal**: Deploy with real capital, start small.

- [ ] Switch `environment: real` in `config.yaml`
- [ ] Start: `risk_per_trade_pct: 0.5`, `max_positions: 2`
- [ ] Run for 4-6 weeks, review performance vs paper
- [ ] Scale up to 1% risk, 4 positions max after validation

---

## Pre-Live Checklist

Before switching to `TrdEnv.REAL`:

- [ ] All unit tests passing
- [ ] Backtests show positive expectancy (metrics above targets)
- [ ] 3+ weeks paper trading with no critical issues
- [ ] Config for live environment reviewed (correct account, `TrdEnv.REAL`)
- [ ] Risk params set conservatively (0.5% risk, 2 positions max)
- [ ] Kill switch tested: manual flatten from dashboard works
- [ ] Telegram alerts confirmed working for all event types
- [ ] OpenD watchdog process running; reconnect handler tested
