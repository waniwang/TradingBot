# System Architecture

## Tech Stack

### Core
| Component | Choice | Rationale |
|---|---|---|
| Language | Python 3.14 | Dominant quant/trading ecosystem |
| Deps | pip + venv | Simple virtual environment management |
| Scheduling | APScheduler | Cron-style jobs for market schedule |
| Indicators | pandas `rolling()` | SMA, ATR computed with plain pandas (pandas-ta incompatible with Python 3.14) |

### Broker & Execution
| Component | Tool | Notes |
|---|---|---|
| Broker API | **Alpaca** (`alpaca-py`) | US equities, no gateway required — pure REST + WebSocket |
| Paper trading | `environment: paper` | `TradingClient(paper=True)` — same code, config flag only |
| Live trading | `environment: live` | `TradingClient(paper=False)` — switch when validated |

Order types used:
- `LimitOrderRequest` — all entries
- `StopOrderRequest` (GTC stop-market) — initial hard stops
- `MarketOrderRequest` — emergency exits only

Alpaca API docs: https://docs.alpaca.markets
Python SDK: https://github.com/alpacahq/alpaca-py
Dashboard: https://alpaca.markets (paper account available for free)

### Market Data
| Use case | Provider | Why |
|---|---|---|
| Pre-market universe scan | **Alpaca** screener/snapshots | Built-in screener API, no extra subscription |
| Daily bars (scanners) | **yfinance** | Free, batch download for large universes |
| Intraday 1m/5m candles | **Alpaca** data API | Real-time bars for signal detection (IEX feed on free tier) |
| Historical OHLCV (backtesting) | **yfinance** | 2+ years of daily bars, cached as parquet files |

Note: Alpaca free tier uses IEX feed (~2% of stocks). yfinance used for batch daily bars where broader coverage is needed. Batch of 1500 tickers takes ~14 min in batches of 500.

### Data Storage
- **SQLite** (development / paper trading)
- **PostgreSQL** (when moving to live or running on VPS)
- ORM: **SQLAlchemy** — all DB ops use `Session` context manager via `get_session(engine)`

### Backtesting
- **Custom pandas-based engine** (`backtest/runner.py`) — daily bar-by-bar simulation
- Historical data from yfinance, cached as parquet in `backtest/cache/`
- Entry approximations from daily bars (no intraday data for history)
- See [backtesting.md](backtesting.md) for how to run backtests and interpret results

### Monitoring
- **Streamlit** — dashboard: open positions, daily P&L, signals fired, stop levels
- **python-telegram-bot** — push alerts: fill confirmed, stop hit, daily summary, errors

---

## Data Flow Diagram

```
═══════════════════════════════════════════════════════════
NIGHTLY (5:00 PM ET — prior day)
═══════════════════════════════════════════════════════════
  yfinance batch daily bars
    ├── Momentum ranker: top 1-2% by 1m/3m/6m RS         →  Breakout candidates
    └── Consolidation detector: ATR contraction + MAs     →  Breakout candidates
  → Persist to DB as "watching" / "ready" stage

═══════════════════════════════════════════════════════════
PRE-MARKET (6:00 AM ET)
═══════════════════════════════════════════════════════════
  Alpaca screener + snapshots
    ├── Gap scanner: stocks up 10%+ premarket on volume  →  EP candidates
    └── Parabolic screener: multi-day runners             →  Short candidates
  Promote ready breakout candidates → active
  Pre-fetch 130 days of daily bars for watchlist (yfinance batch)

  ↓
  Watchlist Builder
    → Subscribe to Alpaca data stream for each candidate

═══════════════════════════════════════════════════════════
MARKET OPEN (9:35 AM ET onwards)
═══════════════════════════════════════════════════════════
  Alpaca data stream (1m candles, quotes)
    ↓
  Signal Engine (per candidate)
    ├── Breakout: price > ORH + volume > 1.5x + above 20d MA + extension < 3%
    ├── EP: price > ORH + volume > 2x avg + gap 10%+ + extension < 5%
    └── Parabolic Short: price < ORB low + VWAP failure

  ↓
  Risk Manager
    ├── Calculate position size: (portfolio * 1%) / (entry - stop)
    ├── Check: total positions < 4
    ├── Check: new position < 15% of portfolio notional
    └── Check: daily/weekly loss limits not hit

  ↓
  Order Executor (Alpaca)
    ├── Place LIMIT buy/short order (entry)
    ├── On fill: immediately place GTC STOP order (initial stop)
    └── Log to DB: signal, order, position

═══════════════════════════════════════════════════════════
INTRADAY MONITOR (every 1m candle + every 5 min reconcile)
═══════════════════════════════════════════════════════════
  For each open position:
    ├── Stop hit? → market close, log exit
    ├── Parabolic short? → check MA profit targets (10d/20d)
    ├── Days in trade >= 3 AND price up >= 15%?
    │     → sell 40% as limit order
    │     → move stop to break-even
    └── End of day: check trailing MA close exits

  Reconcile (every 5 min):
    └── Poll broker for GTC stop fills, update DB

═══════════════════════════════════════════════════════════
END OF DAY (3:55 PM ET)
═══════════════════════════════════════════════════════════
  → Trailing stop updates (10d MA)
  → Check MA-close exits: close if today's close < 10d MA (for longs)
  → Compute daily P&L
  → Send Telegram summary
  → Reset daily halt for next day (weekly halt reset on Fridays)
```

---

## Scheduler Jobs (APScheduler)

| Time (ET) | Job ID | Description |
|---|---|---|
| 5:00 PM | `nightly_watchlist_scan` | Heavy breakout scan: momentum rank + consolidation via yfinance |
| 6:00 AM | `premarket_scan` | Alpaca screener for EP gappers, promote breakout candidates, pre-fetch daily bars |
| 9:25 AM | `subscribe_watchlist` | Subscribe to Alpaca real-time stream for watchlist tickers |
| 9:30 AM | `intraday_monitor` | Log confirmation that the stream is running |
| 3:55 PM | `eod_tasks` | Trailing stop updates, MA-close exits, P&L summary, Telegram |
| Mon-Fri 9-15h, every 5 min | `reconcile_positions` | Poll broker for GTC stop fills, detect unprotected positions |
| Every 30s | `heartbeat` | Write bot_status.json for dashboard (phase, next job, progress) |

All scheduled jobs run in ET timezone (`America/New_York`).

---

## Project Structure

```
trading-bot/
├── config.yaml               # All tunable parameters (see config-reference.md)
├── main.py                   # APScheduler entry point — orchestrates all scheduled jobs
├── run_backtest.py           # CLI entry point for running backtests
├── bot.sh                    # Operations script: start/stop/deploy/logs/verify/scan
├── verify_day.py             # Daily execution verification script
│
├── scanner/
│   ├── __init__.py
│   ├── gapper.py             # EP pre-market scanner: Alpaca snapshots for 10%+ gappers
│   ├── momentum_rank.py      # Relative strength ranking: top 1-2% by 1m/3m/6m (yfinance)
│   ├── consolidation.py      # Breakout detector: ATR contraction + higher lows + dual MA
│   ├── parabolic.py          # Parabolic short scanner: multi-day runners by market cap
│   └── watchlist_manager.py  # Unified watchlist persistence, lifecycle, promotion
│
├── signals/
│   ├── __init__.py           # Strategy registry + adapter functions
│   ├── base.py               # ORH/ORB computation, VWAP, SMA, ATR helpers, SignalResult
│   ├── breakout.py           # Breakout: ORH break + above 20d MA + RVOL > 1.5x + extension guard
│   ├── episodic_pivot.py     # EP: ORH break + RVOL > 2x + gap 10%+ + extension guard
│   └── parabolic_short.py    # Parabolic: ORB low break + VWAP failure (short signal)
│
├── risk/
│   ├── __init__.py
│   └── manager.py            # Position sizer, exposure checker, daily/weekly loss limits
│
├── executor/
│   ├── __init__.py
│   └── alpaca_client.py      # alpaca-py wrapper — paper/live via config, no gateway needed
│
├── monitor/
│   ├── __init__.py
│   └── position_tracker.py   # Stop checks, partial exits, trailing MA close, parabolic targets
│
├── db/
│   ├── __init__.py
│   └── models.py             # SQLAlchemy models: Signal, Order, Position, Watchlist, DailyPnl
│
├── backtest/
│   ├── __init__.py
│   ├── data.py               # yfinance downloader with parquet caching
│   ├── runner.py             # Daily-bar backtest engine: scan → enter → exit → equity
│   ├── metrics.py            # Performance metrics: win rate, Sharpe, drawdown, CAGR
│   └── sweep.py              # Parameter sweep: OAT + grid search + OOS validation
│
├── dashboard/
│   └── app.py                # Streamlit: positions, P&L, signals, stop levels, kill switch
│
└── tests/
    ├── __init__.py
    ├── test_signals.py              # Signal module tests: breakout, EP, parabolic, ATR caps
    ├── test_scanners.py             # Scanner tests: consolidation, gapper, parabolic, prior move
    ├── test_risk.py                 # Risk manager tests: position size, exposure, loss halt
    ├── test_main.py                 # Main scheduler / orchestration tests
    ├── test_backtest.py             # Backtest tests: metrics, runner, position sizing
    ├── test_watchlist_manager.py    # Watchlist lifecycle and persistence tests
    ├── test_alpaca_liquidity_filter.py  # Alpaca liquidity filter tests
    └── fixtures/                    # Test fixture data
```

---

## Module Reference

### `main.py`
- Loads `config.yaml` (env vars override: `ALPACA_API_KEY`, `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
- Initializes DB connection via SQLAlchemy `get_session(engine)`
- Creates Telegram notifier (`make_notifier()`) — sends alerts for all key events
- Runs startup reconciliation to detect unprotected positions from prior crash
- Registers all 7 APScheduler jobs (ET timezone)
- Pre-fetches daily bars for watchlist tickers via yfinance batch download

### `scanner/gapper.py`
- Uses Alpaca screener/snapshots to find pre-market gappers
- Filters: premarket change >= `ep_min_gap_pct`, volume >= threshold
- **Prior-rally filter**: rejects stocks already up 50%+ in prior 6 months
- Returns: list of `{"ticker": str, "gap_pct": float, "premarket_volume": int}`

### `scanner/momentum_rank.py`
- Uses yfinance to get closing prices for 1m, 3m, 6m lookback
- Computes percent change for each window
- Returns: top 20 tickers by composite RS score

### `scanner/consolidation.py`
- Takes a ticker + daily bar DataFrame
- Checks **prior large move** (>= 30% in ~2 months before consolidation)
- Enforces **minimum consolidation duration** (default 10 days)
- Computes daily ATR, checks for contraction trend (ATR ratio < 0.95)
- Checks for higher lows in daily closes
- Verifies price is near **both 10d AND 20d MA** (within 3% tolerance)

### `scanner/parabolic.py`
- Uses Alpaca snapshots + yfinance daily bars
- **Market-cap differentiated thresholds**: large-cap (price > $50) needs 50%+ gain, small-cap (price < $20) needs 200%+, interpolated in between

### `signals/base.py`
- `compute_orh(candles_1m, n_minutes=5)` -> float (opening range high)
- `compute_orb_low(candles_1m, n_minutes=5)` -> float (opening range low)
- `compute_vwap(candles_1m)` -> Series
- `compute_sma(closes, period)` -> float
- `compute_atr_from_list(daily_highs, daily_lows, daily_closes, period=14)` -> float
- `SignalResult` dataclass: ticker, setup_type, side, entry_price, stop_price, orh, orb_low, gap_pct, risk_per_share

### `signals/breakout.py`
- Conditions: price > ORH, price > 20d MA, volume > 1.5x 20d avg, extension < 3% above ORH
- Stop: LOD capped at 1x ATR(14)

### `signals/episodic_pivot.py`
- Conditions: price > ORH, volume > 2x avg, gap >= 10%, extension < 5% above ORH
- Stop: LOD capped at 1.5x ATR(14)

### `signals/parabolic_short.py`
- Conditions: price < ORB low, price < VWAP (VWAP failure)
- Returns: `SignalResult` (side=short)

### `risk/manager.py`
- `calculate_position_size(portfolio_value, entry, stop)` -> int (shares)
- `check_max_positions(open_count)` -> bool
- `check_position_notional(shares, entry, portfolio)` -> bool
- `check_daily_loss(daily_pnl, portfolio)` -> bool
- `check_weekly_loss(weekly_pnl, portfolio)` -> bool
- `can_enter(open_count, daily_pnl, weekly_pnl, portfolio)` -> (bool, reason)
- `tighten_stop(current, new, side)` -> float (never widens)
- `compute_trailing_stop(ma_value, current_stop, side)` -> float

### `executor/alpaca_client.py`
- Wraps `alpaca-py` `TradingClient` + `StockHistoricalDataClient`
- No gateway process required — pure REST + WebSocket
- `get_portfolio_value()`, `place_limit_order()`, `place_stop_order()`, `close_position()`
- `get_candles_1m()`, `get_daily_bars()`, `get_daily_bars_batch()` (yfinance)
- `run_screener()`, `get_snapshots()`, `subscribe_quotes()`, `is_market_open()`, `is_trading_day()`

### `monitor/position_tracker.py`
- **Stop checks**: hard stop hit detection
- **Parabolic targets**: cover 50% at 10d MA, rest at 20d MA (for shorts)
- **Partial exit**: sell 40% after 3+ days if gain >= 15%, move stop to break-even
- **Trailing MA close**: at EOD, exit positions where daily close < 10d MA (after partial exit)

### `db/models.py`
Five SQLAlchemy models:
- `Signal` — every signal fired (ticker, setup type, entry/stop prices, timestamp)
- `Order` — every order sent to broker (order_id, ticker, side, qty, price, status)
- `Position` — open/closed positions (entry, stop, shares, partial exits, P&L)
- `Watchlist` — unified watchlist for all setup types (lifecycle stages)
- `DailyPnl` — end-of-day P&L summary per day

Exit reasons: `stop_hit`, `trailing_stop`, `trailing_ma_close`, `parabolic_target`, `manual`, `daily_loss_limit`

### `backtest/data.py`
- `fetch_historical_bars(tickers, start, end)` -> dict[str, DataFrame]
- Downloads via yfinance in batches of 500, caches to parquet in `backtest_cache/`

### `backtest/runner.py`
- `BacktestConfig` dataclass with all tunable parameters
- `BacktestRunner.run(bars, setups=None)` -> metrics dict
- Daily bar-by-bar simulation: process exits -> scan & enter -> record equity

### `backtest/metrics.py`
- `compute_metrics(trades, daily_equity)` -> dict with total_trades, win_rate, avg_winner, avg_loser, wl_ratio, profit_factor, sharpe, max_drawdown_pct, total_return_pct, cagr, calmar, avg_days_held, max_consec_losses, trades_per_month

### `dashboard/app.py`
- Reads from DB via SQLAlchemy
- Tables: open positions, recent signals, today's orders
- Charts: daily P&L curve, portfolio exposure gauge
- Buttons: "Flatten [ticker]" -> calls executor to close position

---

## Position Sizing Formula

```python
risk_per_share = abs(entry_price - stop_price)
max_risk_dollars = portfolio_value * (risk_per_trade_pct / 100)  # default 1%
raw_shares = floor(max_risk_dollars / risk_per_share)

# Cap by max_position_pct (default 15%)
max_notional = portfolio_value * (max_position_pct / 100)
max_shares_by_notional = floor(max_notional / entry_price)

shares = min(raw_shares, max_shares_by_notional)
```

### ATR Cap Logic

Stops are capped to prevent excessively wide risk:
- **Breakout**: if `entry - LOD > ATR(14)`, stop is tightened to `entry - ATR`
- **EP**: if `entry - LOD > 1.5 * ATR(14)`, stop is tightened to `entry - 1.5 * ATR`
- ATR is computed over 14 periods using daily highs, lows, and closes

### Loss Halt Pseudocode

```python
daily_pnl = sum(realized_pnl_today) + sum(unrealized_pnl_open_positions)
if daily_pnl / portfolio_value < -0.03:
    halt_trading(reason="daily_loss_limit")
    send_telegram_alert("Daily loss limit hit. Trading halted for the day.")

weekly_pnl = sum(realized_pnl_this_week) + unrealized
if weekly_pnl / portfolio_value < -0.05:
    halt_trading(reason="weekly_loss_limit")
    send_telegram_alert("Weekly loss limit hit. Trading halted for the week.")
```

---

## Environment Variables

Sensitive keys can be set as environment variables (recommended for production):

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DATABASE_URL=...
```

The config loader checks env vars first, falls back to `config.yaml`.

---

## Key Design Decisions

### Why Alpaca?
Pure REST + WebSocket API, no gateway process needed. Free paper trading account. Native Python SDK (`alpaca-py`). Supports both paper and live with a single config flag.

### Why yfinance for daily bars?
Alpaca's free tier (IEX feed) covers only ~2% of stocks. yfinance provides free daily OHLCV for the full US equity universe. Used for scanner daily bars and backtesting historical data.

### Why SQLite first?
Simpler dev/paper trading setup. SQLAlchemy ORM abstracts the database; switching to PostgreSQL later requires only a connection string change.

### Why plain pandas instead of pandas-ta?
pandas-ta depends on numba, which is incompatible with Python 3.14. SMA and ATR are computed with `pandas.rolling()` — simple and dependency-free.

### Why custom backtest engine instead of vectorbt?
vectorbt also has numba dependency issues with Python 3.14. The custom engine in `backtest/runner.py` provides full control over entry approximations from daily bars and matches our exact signal/exit logic.

### Why limit orders for all entries?
Momentum names can move extremely fast around the ORH level. A market order could result in significant slippage. Limit orders with a small tolerance (entry <= ORH + 0.5%) ensure we don't chase.
