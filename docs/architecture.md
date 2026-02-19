# System Architecture

## Tech Stack

### Core
| Component | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Dominant quant/trading ecosystem |
| Deps | Poetry | Reproducible virtual environments |
| Scheduling | APScheduler | Cron-style jobs for market schedule |

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
| Pre-market universe scan | **Polygon.io** REST API | Scans all US equities; Moomoo charges per-symbol subscription |
| Intraday 1m/5m candles | **Moomoo** push callbacks | Real-time, already subscribed for execution |
| Level 2 / order book | **Moomoo** | Available via `get_order_book()` |
| Historical OHLCV (backtesting) | **Polygon.io** `/v2/aggs/` | 2+ years of history |

Polygon.io docs: https://polygon.io/docs/stocks

### Technical Indicators
- **pandas-ta** — MA (10d/20d), ATR, VWAP, RSI — all computed in-process
- No TA-Lib dependency (install headaches)

### Data Storage
- **SQLite** (development / early paper trading)
- **PostgreSQL** (when moving to live or running on VPS)
- ORM: **SQLAlchemy**

### Backtesting
- **vectorbt** — fast, vectorized, pandas-native
- Historical data from Polygon.io `/v2/aggs/ticker/{ticker}/range/1/day/`

### Monitoring
- **Streamlit** — dashboard: open positions, daily P&L, signals fired, stop levels
- **python-telegram-bot** — push alerts: fill confirmed, stop hit, daily summary

---

## Data Flow Diagram

```
═══════════════════════════════════════════════════════════
PRE-MARKET (6:00 – 9:25 AM ET)
═══════════════════════════════════════════════════════════
  Polygon.io REST API
    ├── Gap scanner: stocks up 10%+ premarket on volume  →  EP candidates
    ├── Momentum ranker: top 1-2% by 1m/3m/6m RS         →  Breakout candidates
    └── Parabolic screener: 50%+ in 5 days, 3+ up days   →  Short candidates

  ↓
  Watchlist Builder
    → Subscribe to real-time Moomoo data for each candidate

═══════════════════════════════════════════════════════════
MARKET OPEN (9:30 AM ET)
═══════════════════════════════════════════════════════════
  Moomoo push callbacks (1m candles, quotes)
    ↓
  Signal Engine (per candidate)
    ├── Breakout: price > ORH + volume spike + above 20d MA
    ├── EP: price > ORH + volume > 2x premarket avg
    └── Parabolic Short: price < ORB low + VWAP failure

  ↓
  Risk Manager
    ├── Calculate position size: (portfolio * 1%) / (entry - stop)
    ├── Check: total positions < 4
    └── Check: new position < 10% of portfolio notional

  ↓
  Order Executor
    ├── Place LIMIT buy/short order (entry)
    ├── On fill: immediately place STOP order (initial stop)
    └── Log to DB: signal, order, position

═══════════════════════════════════════════════════════════
INTRADAY MONITOR LOOP (every 1m candle)
═══════════════════════════════════════════════════════════
  For each open position:
    ├── Stop hit? → market close, log exit, unsubscribe
    ├── Days in trade >= 3 AND price up >= 15%?
    │     → sell 1/3-1/2 as limit order
    │     → move stop to break-even
    └── End of day: update trailing stop to prior 10d/20d MA close

═══════════════════════════════════════════════════════════
END OF DAY (4:00 PM ET)
═══════════════════════════════════════════════════════════
  → Compute daily P&L
  → Check: any position closed below 10/20d MA today? → schedule close at open tomorrow
  → Send Telegram daily summary
  → Update Streamlit dashboard
```

---

## Scheduler Jobs (APScheduler)

| Time (ET) | Job | Description |
|---|---|---|
| 6:00 AM | `run_premarket_scan` | Polygon.io gap + momentum + parabolic scan |
| 9:25 AM | `finalize_watchlist` | Subscribe Moomoo push for final candidates |
| 9:30 AM | `start_intraday_monitor` | Activate signal engine + position tracker |
| 4:00 PM | `run_eod_tasks` | Trail stops, compute P&L, send Telegram summary |

---

## Key Design Decisions

### Why Polygon.io for scanning (not Moomoo)?
Moomoo charges a per-symbol subscription fee for real-time quotes. Scanning the entire US equities universe (~8,000 symbols) would be prohibitively expensive. Polygon.io's REST API allows cheap bulk scans. Only the final watchlist (~10-20 symbols) gets subscribed in Moomoo.

### Why SQLite first?
Simpler dev/paper trading setup. SQLAlchemy ORM abstracts the database; switching to PostgreSQL later requires only a connection string change.

### Why pandas-ta over TA-Lib?
TA-Lib requires compiled C extensions that are painful to install on different OS/architecture combinations. pandas-ta is pure Python/pandas, installs cleanly with pip/poetry.

### Why limit orders for all entries?
Momentum names can move extremely fast around the ORH level. A market order could result in significant slippage. Limit orders with a small tolerance (entry ≤ ORH + 0.5%) ensure we don't chase.
