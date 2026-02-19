# Project File Structure

```
trading-bot/
├── config.yaml               # All tunable parameters (see config-reference.md)
├── pyproject.toml            # Poetry dependencies and project metadata
├── main.py                   # APScheduler entry point — orchestrates all scheduled jobs
│
├── scanner/
│   ├── __init__.py
│   ├── gapper.py             # EP pre-market scanner: Polygon.io gappers (10%+ premarket)
│   ├── momentum_rank.py      # Relative strength ranking: top 1-2% by 1m/3m/6m performance
│   └── consolidation.py     # Breakout setup detector: ATR contraction + higher lows
│
├── signals/
│   ├── __init__.py
│   ├── base.py               # Base Signal class: common ORH/ORB logic, shared helpers
│   ├── breakout.py           # Breakout: ORH break + above 20d MA + volume > 1.5x avg
│   ├── episodic_pivot.py     # EP: ORH break + volume > 2x premarket avg + gap 10%+
│   └── parabolic_short.py   # Parabolic: ORB low break + VWAP failure (short signal)
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
│   └── position_tracker.py  # Intraday loop: stop checks, partial exits, trailing stops
│
├── db/
│   ├── __init__.py
│   └── models.py             # SQLAlchemy models: Signal, Order, Position, DailyPnl
│
├── dashboard/
│   └── app.py                # Streamlit: positions, P&L, signals, stop levels, kill switch
│
└── tests/
    ├── test_signals.py       # Signal module unit tests with fixture OHLCV
    ├── test_risk.py          # Risk manager unit tests (position size, exposure, loss halt)
    └── fixtures/             # Historical OHLCV samples for deterministic unit tests
        ├── breakout_setup.csv
        ├── ep_setup.csv
        └── parabolic_setup.csv
```

---

## Module Responsibilities

### `main.py`
- Loads `config.yaml`
- Initializes DB connection
- Registers all APScheduler jobs
- Starts the event loop

### `scanner/gapper.py`
- Calls Polygon.io `/v2/snapshot/locale/us/markets/stocks/gainers` or similar endpoint
- Filters: premarket change ≥ `ep_min_gap_pct`, volume ≥ threshold
- Returns: list of `{"ticker": str, "gap_pct": float, "premarket_volume": int}`

### `scanner/momentum_rank.py`
- Calls Polygon.io to get closing prices for 1m, 3m, 6m lookback
- Computes percent change for each window
- Returns: top 20 tickers by composite RS score

### `scanner/consolidation.py`
- Takes a ticker + lookback window
- Computes daily ATR, checks for contraction trend
- Checks for higher lows in daily closes
- Returns: `{"qualifies": bool, "consolidation_days": int, "atr_ratio": float}`

### `signals/base.py`
- `compute_orh(candles_1m, n_minutes=5)` → float (opening range high)
- `compute_orb_low(candles_1m, n_minutes=5)` → float (opening range low)
- `compute_vwap(candles_1m)` → Series
- Base dataclass `SignalResult` with fields: ticker, setup_type, entry_price, stop_price, timestamp

### `signals/breakout.py`
- Inherits from base
- Conditions: price > ORH, price > 20d MA, volume > 1.5x 20d avg volume
- Returns: `SignalResult` or None

### `signals/episodic_pivot.py`
- Inherits from base
- Conditions: price > ORH, volume > 2x premarket avg, premarket gap ≥ 10%
- Returns: `SignalResult` or None

### `signals/parabolic_short.py`
- Inherits from base
- Conditions: price < ORB low, price < VWAP (VWAP failure)
- Returns: `SignalResult` (side=SHORT) or None

### `risk/manager.py`
- `calculate_position_size(portfolio_value, entry, stop, config)` → int (shares)
- `check_exposure(open_positions, new_notional, portfolio_value, config)` → bool
- `check_daily_loss(daily_pnl, portfolio_value, config)` → bool
- `check_weekly_loss(weekly_pnl, portfolio_value, config)` → bool

### `executor/alpaca_client.py`
- Wraps `alpaca-py` `TradingClient` + `StockHistoricalDataClient` + `StockDataStream`
- No gateway process required — pure REST + WebSocket
- `connect()` / `disconnect()`
- `get_portfolio_value()` → float
- `place_limit_order(ticker, side, shares, price)` → order_id
- `place_stop_order(ticker, side, shares, stop_price)` → order_id (GTC stop-market)
- `modify_stop_order(order_id, new_stop_price)` → replaces order via Alpaca replace API
- `cancel_order(order_id)`
- `close_position(ticker, shares, side)` → market order
- `get_candles_1m(ticker, count)` → list of OHLCV dicts
- `get_daily_bars(ticker, days)` → list of OHLCV dicts
- `subscribe_quotes(tickers, callback)` → starts background WebSocket stream
- `unsubscribe_quotes(tickers)`

### `monitor/position_tracker.py`
- Maintains state for all open positions
- Called every 1m by the Moomoo push callback or a timer
- For each position:
  - Check if stop hit → call `executor.close_position()`
  - Check partial exit conditions → call `executor.place_limit_order()` for partial
  - At 3:55 PM ET: compute new trailing stop level, call `executor.update_stop()`

### `db/models.py`
Four SQLAlchemy models:
- `Signal` — every signal fired (ticker, setup type, entry/stop prices, timestamp)
- `Order` — every order sent to broker (order_id, ticker, side, qty, price, status)
- `Position` — open/closed positions (entry, stop, shares, partial exits, P&L)
- `DailyPnl` — end-of-day P&L summary per day

### `dashboard/app.py`
- Reads from DB in real-time via SQLAlchemy
- Tables: open positions, recent signals, today's orders
- Charts: daily P&L curve, portfolio exposure gauge
- Buttons: "Flatten [ticker]" → calls `executor.close_position()` directly

---

## Environment Variables (alternative to config.yaml)

Sensitive keys can also be set as environment variables (recommended for production):
```
POLYGON_API_KEY=...
MOOMOO_HOST=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DATABASE_URL=...
```

The config loader checks env vars first, falls back to `config.yaml`.
