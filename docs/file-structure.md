# Project File Structure

```
trading-bot/
├── config.yaml               # All tunable parameters (see config-reference.md)
├── main.py                   # APScheduler entry point — orchestrates all scheduled jobs
├── run_backtest.py           # CLI entry point for running backtests
├── bot.sh                    # Operations script: start/stop/deploy/logs
│
├── scanner/
│   ├── __init__.py
│   ├── gapper.py             # EP pre-market scanner: Alpaca snapshots for 10%+ gappers
│   ├── momentum_rank.py      # Relative strength ranking: top 1-2% by 1m/3m/6m (yfinance)
│   ├── consolidation.py      # Breakout detector: ATR contraction + higher lows + dual MA
│   └── parabolic.py          # Parabolic short scanner: multi-day runners by market cap
│
├── signals/
│   ├── __init__.py           # Strategy registry + adapter functions
│   ├── base.py               # ORH/ORB computation, VWAP, SMA, ATR helpers, SignalResult
│   ├── breakout.py           # Breakout: ORH break + above 10d & 20d MA + volume > 1.5x
│   ├── episodic_pivot.py     # EP: ORH break + volume > 2x avg + gap 10%+
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
│   └── models.py             # SQLAlchemy models: Signal, Order, Position, DailyPnl
│
├── backtest/
│   ├── __init__.py
│   ├── data.py               # yfinance downloader with parquet caching
│   ├── runner.py             # Daily-bar backtest engine: scan → enter → exit → equity
│   └── metrics.py            # Performance metrics: win rate, Sharpe, drawdown, CAGR
│
├── dashboard/
│   └── app.py                # Streamlit: positions, P&L, signals, stop levels, kill switch
│
└── tests/
    ├── __init__.py
    ├── test_signals.py       # Signal module tests: breakout, EP, parabolic, ATR caps
    ├── test_scanners.py      # Scanner tests: consolidation, gapper, parabolic, prior move
    ├── test_risk.py          # Risk manager tests: position size, exposure, loss halt
    ├── test_main.py          # Main scheduler / orchestration tests
    ├── test_backtest.py      # Backtest tests: metrics, runner, position sizing
    └── fixtures/             # Test fixture data
```

---

## Module Responsibilities

### `main.py`
- Loads `config.yaml` (env vars override: `ALPACA_API_KEY`, `DATABASE_URL`, etc.)
- Initializes DB connection via SQLAlchemy `get_session(engine)`
- Registers all APScheduler jobs (ET timezone)
- Starts the event loop

### `scanner/gapper.py`
- Uses Alpaca screener/snapshots to find pre-market gappers
- Filters: premarket change >= `ep_min_gap_pct`, volume >= threshold
- **Prior-rally filter**: rejects stocks already up 50%+ in prior 6 months (fetches 6-month bars)
- Returns: list of `{"ticker": str, "gap_pct": float, "premarket_volume": int}`

### `scanner/momentum_rank.py`
- Uses yfinance to get closing prices for 1m, 3m, 6m lookback
- Computes percent change for each window
- Returns: top 20 tickers by composite RS score

### `scanner/consolidation.py`
- Takes a ticker + daily bar DataFrame
- Checks **prior large move** (>= 30% in ~2 months before consolidation)
- Enforces **minimum consolidation duration** (default 10 days)
- Computes daily ATR, checks for contraction trend (ATR ratio < 0.85)
- Checks for higher lows in daily closes
- Verifies price is near **both 10d AND 20d MA** (within 3% tolerance)
- Returns: `{"qualifies": bool, "consolidation_days": int, "atr_ratio": float, "near_10d_ma": bool, "near_20d_ma": bool, "has_prior_move": bool}`

### `scanner/parabolic.py`
- Uses Alpaca snapshots + yfinance daily bars
- **Market-cap differentiated thresholds**: large-cap (price > $50) needs 50%+ gain, small-cap (price < $20) needs 200%+, interpolated in between
- Returns: list of parabolic short candidates

### `signals/base.py`
- `compute_orh(candles_1m, n_minutes=5)` -> float (opening range high)
- `compute_orb_low(candles_1m, n_minutes=5)` -> float (opening range low)
- `compute_vwap(candles_1m)` -> Series
- `compute_sma(closes, period)` -> float
- `compute_atr_from_list(daily_highs, daily_lows, daily_closes, period=14)` -> float
- Base dataclass `SignalResult` with fields: ticker, setup_type, entry_price, stop_price, timestamp

### `signals/breakout.py`
- Conditions: price > ORH, price > 10d MA, price > 20d MA, volume > 1.5x 20d avg
- Stop: LOD capped at 1x ATR(14)
- Returns: `SignalResult` or None

### `signals/episodic_pivot.py`
- Conditions: price > ORH, volume > 2x avg, premarket gap >= 10%
- Stop: LOD capped at 1.5x ATR(14)
- Accepts `daily_highs`, `daily_lows`, `daily_closes` for ATR calculation
- Returns: `SignalResult` or None

### `signals/parabolic_short.py`
- Conditions: price < ORB low, price < VWAP (VWAP failure)
- Returns: `SignalResult` (side=SHORT) or None

### `risk/manager.py`
- `calculate_position_size(portfolio_value, entry, stop, config)` -> int (shares)
- `check_exposure(open_positions, new_notional, portfolio_value, config)` -> bool
- `check_daily_loss(daily_pnl, portfolio_value, config)` -> bool
- `check_weekly_loss(weekly_pnl, portfolio_value, config)` -> bool

### `executor/alpaca_client.py`
- Wraps `alpaca-py` `TradingClient` + `StockHistoricalDataClient`
- No gateway process required — pure REST + WebSocket
- `get_portfolio_value()` -> float
- `place_limit_order(ticker, side, shares, price)` -> order_id
- `place_stop_order(ticker, side, shares, stop_price)` -> order_id (GTC stop-market)
- `close_position(ticker, shares, side)` -> market order
- `get_candles_1m(ticker, count)` -> list of OHLCV dicts
- `get_daily_bars(ticker, days)` -> list of OHLCV dicts
- `get_daily_bars_batch(tickers, days)` -> dict of DataFrames
- `run_screener(criteria)` -> list of tickers
- `get_snapshots(tickers)` -> dict of snapshot data

### `monitor/position_tracker.py`
- Maintains state for all open positions
- **Stop checks**: hard stop hit detection
- **Parabolic targets**: cover 50% at 10d MA, rest at 20d MA (for parabolic shorts)
- **Partial exit**: sell 40% after 3+ days if gain >= 15%, move stop to break-even
- **Trailing MA close**: at EOD, exit positions where daily close < 10d MA (after partial exit done)
- Called by scheduler at 4:00 PM ET for EOD tasks

### `db/models.py`
Four SQLAlchemy models:
- `Signal` — every signal fired (ticker, setup type, entry/stop prices, timestamp)
- `Order` — every order sent to broker (order_id, ticker, side, qty, price, status)
- `Position` — open/closed positions (entry, stop, shares, partial exits, P&L)
- `DailyPnl` — end-of-day P&L summary per day

Exit reasons: `stop_hit`, `partial_exit`, `trailing_stop`, `trailing_ma_close`, `parabolic_target`, `manual`, `eod_close`

### `backtest/data.py`
- `fetch_historical_bars(tickers, start_date, end_date)` -> dict[str, DataFrame]
- Downloads via yfinance in batches of 500, caches to parquet in `backtest/cache/`
- `get_sp500_tickers()` -> list of S&P 500 tickers from Wikipedia

### `backtest/runner.py`
- `BacktestConfig` dataclass with all tunable parameters
- `BacktestRunner.run(bars, setups=None)` -> metrics dict
- Daily bar-by-bar simulation: process exits -> scan & enter -> record equity
- Entry approximations from daily bars (breakout: 5-day high, EP: gap proxy, parabolic: reversal)
- Tracks `runner.trades` (list of Trade) and `runner.daily_equity` (list of float)

### `backtest/metrics.py`
- `Trade` dataclass: ticker, setup_type, side, entry/exit dates, prices, shares, pnl, exit_reason
- `compute_metrics(trades, daily_equity)` -> dict with total_trades, win_rate, avg_winner, avg_loser, wl_ratio, profit_factor, sharpe, max_drawdown_pct, total_return_pct, cagr
- `compute_max_drawdown(equity)` -> float (percentage)

### `run_backtest.py`
- CLI with argparse: `--tickers`, `--sp500`, `--start`, `--end`, `--setup`, `--capital`, `--max-positions`, `--verbose`
- Pretty-prints: metrics, trade log, setup breakdown, equity curve, target check

### `dashboard/app.py`
- Reads from DB via SQLAlchemy
- Tables: open positions, recent signals, today's orders
- Charts: daily P&L curve, portfolio exposure gauge
- Buttons: "Flatten [ticker]" -> calls executor to close position

---

## Environment Variables (alternative to config.yaml)

Sensitive keys can also be set as environment variables (recommended for production):
```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DATABASE_URL=...
```

The config loader checks env vars first, falls back to `config.yaml`.
