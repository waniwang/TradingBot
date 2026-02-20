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
- See [verification.md](verification.md) for how to run backtests and interpret results

### Monitoring
- **Streamlit** — dashboard: open positions, daily P&L, signals fired, stop levels
- **python-telegram-bot** — push alerts: fill confirmed, stop hit, daily summary (Phase 7)

---

## Data Flow Diagram

```
═══════════════════════════════════════════════════════════
PRE-MARKET (6:00 - 9:25 AM ET)
═══════════════════════════════════════════════════════════
  Alpaca screener + snapshots
    ├── Gap scanner: stocks up 10%+ premarket on volume  →  EP candidates
    └── Parabolic screener: multi-day runners             →  Short candidates

  yfinance batch daily bars
    ├── Momentum ranker: top 1-2% by 1m/3m/6m RS         →  Breakout candidates
    └── Consolidation detector: ATR contraction + MAs     →  Breakout candidates

  ↓
  Watchlist Builder
    → Subscribe to Alpaca data stream for each candidate

═══════════════════════════════════════════════════════════
MARKET OPEN (9:30 AM ET)
═══════════════════════════════════════════════════════════
  Alpaca data stream (1m candles, quotes)
    ↓
  Signal Engine (per candidate)
    ├── Breakout: price > ORH + volume > 1.5x + above 10d & 20d MA
    ├── EP: price > ORH + volume > 2x avg + gap 10%+
    └── Parabolic Short: price < ORB low + VWAP failure

  ↓
  Risk Manager
    ├── Calculate position size: (portfolio * 1%) / (entry - stop)
    ├── Check: total positions < 4
    └── Check: new position < 10% of portfolio notional

  ↓
  Order Executor (Alpaca)
    ├── Place LIMIT buy/short order (entry)
    ├── On fill: immediately place STOP order (initial stop)
    └── Log to DB: signal, order, position

═══════════════════════════════════════════════════════════
INTRADAY MONITOR LOOP (every 1m candle)
═══════════════════════════════════════════════════════════
  For each open position:
    ├── Stop hit? → market close, log exit
    ├── Parabolic short? → check MA profit targets (10d/20d)
    ├── Days in trade >= 3 AND price up >= 15%?
    │     → sell 40% as limit order
    │     → move stop to break-even
    └── End of day: check trailing MA close exits

═══════════════════════════════════════════════════════════
END OF DAY (4:00 PM ET)
═══════════════════════════════════════════════════════════
  → Compute daily P&L
  → Check trailing MA exits: close if today's close < 10d MA (for longs)
  → Send Telegram daily summary
  → Update Streamlit dashboard
```

---

## Scheduler Jobs (APScheduler)

| Time (ET) | Job | Description |
|---|---|---|
| 6:00 AM | `job_premarket_scan` | Alpaca screener + yfinance gap/momentum/consolidation/parabolic scan |
| 9:25 AM | `finalize_watchlist` | Prepare final candidates for signal monitoring |
| 9:30 AM | `start_intraday_monitor` | Activate signal engine + position tracker |
| 4:00 PM | `run_eod_tasks` | Trail stops (MA close check), compute P&L, send Telegram summary |

All scheduled jobs run in ET timezone (`America/New_York`).

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
