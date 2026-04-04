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
| Broker API | **Alpaca** (`alpaca-py`) | US equities, pure REST + WebSocket |
| Paper trading | `environment: paper` | `TradingClient(paper=True)` |
| Live trading | `environment: live` | `TradingClient(paper=False)` |

Order types: `LimitOrderRequest` (entries), `StopOrderRequest` (GTC stops), `MarketOrderRequest` (emergency exits).

### Market Data
| Use case | Provider | Why |
|---|---|---|
| Pre-market universe scan | **Alpaca** screener/snapshots | Built-in screener API |
| Daily bars (scanners) | **yfinance** | Free, batch download for large universes |
| Intraday 1m candles | **Alpaca** data API | Real-time bars for signal detection (IEX feed on free tier) |
| Historical OHLCV (backtesting) | **yfinance** | 2+ years of daily bars, cached as parquet |

Note: Alpaca free tier uses IEX feed (~2% of stocks). yfinance used for batch daily bars where broader coverage is needed.

### Data Storage
- **SQLite** (development / paper) / **PostgreSQL** (live / VPS)
- ORM: **SQLAlchemy** + **Alembic** migrations
- All DB ops use `get_session(engine)` context manager

### Dashboard
- **Next.js 16** frontend (deployed to Vercel) — `dashboard/`
- **FastAPI** backend (runs on Linode alongside bot) — `trading-bot/api/`
- **python-telegram-bot** — push alerts for all key events

---

## Data Flow

```
Strategy Scanners                 Strategy Signals               Monitor (intraday + EOD)
├── breakout/scanner_nightly.py   ├── breakout/signal.py         ├── stop checks
│   (5 PM: momentum + consol.)   ├── episodic_pivot/signal.py   ├── partial exits (40% @ +15%)
├── breakout/scanner_premarket.py └── parabolic_short/signal.py  ├── trailing MA close (10d)
│   (6 AM: promote to active)                                    └── parabolic targets (10d/20d MA)
├── episodic_pivot/scanner.py     EP Swing Strategies
│   (6 AM: premarket gappers)     ├── ep_earnings/strategy.py   EP Exits
├── ep_earnings/scanner.py        │   (3 PM: A/B eval)           ├── -7% stop
│   (3 PM: earnings gappers)      └── ep_news/strategy.py        ├── max 50-day hold
├── ep_news/scanner.py            │   (3 PM: A/B eval)           └── trailing MA close
│   (3 PM: news gappers)         └── execute @ 3:50 PM
└── parabolic_short/scanner.py
    (6 AM: multi-day runners)
         ↓                               ↓                              ↓
    Watchlist Manager ─────────→ Risk Manager ──────────────→ Alpaca Executor
```

---

## Scheduler Jobs (APScheduler, ET timezone)

| Time (ET) | Job | Source |
|---|---|---|
| 5:00 PM | Breakout nightly scan (momentum rank + consolidation) | `breakout/plugin.py` |
| 6:00 AM | Premarket scan (EP gappers, promote breakout, pre-fetch bars) | `main.py` |
| 9:25 AM | Finalize watchlist, subscribe to Alpaca real-time bars | `main.py` |
| 9:30 AM | Start intraday monitor (signal evaluation on 1m bars) | `main.py` |
| 3:00 PM | EP earnings + EP news scan + strategy evaluation | `ep_earnings/plugin.py`, `ep_news/plugin.py` |
| 3:50 PM | EP earnings + EP news execute entries | `ep_earnings/plugin.py`, `ep_news/plugin.py` |
| 3:55 PM | EOD tasks (trailing stops, MA-close exits, P&L, Telegram) | `main.py` |
| Every 5 min (9-15h) | Reconcile positions (broker sync) | `main.py` |
| Every 30s | Heartbeat (`bot_status.json` for dashboard) | `main.py` |

---

## Project Structure

```
trading-bot/
├── main.py                    # APScheduler entry point
├── config.yaml                # All tunable parameters (see config-reference.md)
├── bot.sh                     # Operations: start/stop/deploy/logs/verify
├── verify_day.py              # Daily execution verification
├── run_backtest.py            # Backtest CLI
│
├── core/                      # Plugin framework (see core/README.md)
│   ├── loader.py              # Strategy plugin discovery & registry
│   ├── scheduler.py           # Register plugin cron jobs with APScheduler
│   └── data_cache.py          # Thread-safe shared daily bar cache
│
├── strategies/                # Strategy plugins (see each README.md)
│   ├── episodic_pivot/        # Premarket gap → ORH breakout (single-day)
│   ├── ep_earnings/           # EOD earnings gap → A/B swing entry
│   ├── ep_news/               # EOD news gap → A/B swing entry
│   ├── breakout/              # Nightly consol. → premarket promote → ORH breakout
│   └── parabolic_short/       # Multi-day runner → ORB low short (DISABLED)
│
├── scanner/                   # Shared scanner infra (see scanner/README.md)
│   ├── watchlist_manager.py   # DB-backed watchlist lifecycle
│   ├── consolidation.py       # ATR contraction + higher lows + MA proximity
│   └── momentum_rank.py       # Relative strength ranking
│
├── signals/                   # Shared signal infra (see signals/README.md)
│   └── base.py                # ORH/ORB, VWAP, SMA, ATR, RVOL, SignalResult
│
├── risk/
│   └── manager.py             # Position sizing, exposure checks, loss limits
│
├── executor/
│   └── alpaca_client.py       # Alpaca trading + data + screener wrapper
│
├── monitor/
│   └── position_tracker.py    # Stop checks, partials, trailing MA, parabolic targets
│
├── db/
│   └── models.py              # Signal, Order, Position, Watchlist, DailyPnl
│
├── api/                       # FastAPI dashboard backend
│   ├── main.py                # App + CORS + auth middleware
│   ├── deps.py                # Dependency injection
│   └── routes/                # /status, /portfolio, /positions, /watchlist, /signals, /performance
│
├── backtest/
│   ├── runner.py              # Daily bar-by-bar simulation engine
│   ├── metrics.py             # Win rate, Sharpe, drawdown, CAGR, calmar
│   ├── data.py                # yfinance downloader with parquet caching
│   └── sweep.py               # Parameter sweep: OAT + grid + OOS validation
│
├── alembic/                   # Database migrations
├── scripts/
│   └── server-deploy.sh       # Linode deployment script
├── dashboard_legacy/
│   └── app.py                 # Streamlit (deprecated)
│
└── tests/                     # 338 tests across 9 files
    ├── test_signals.py
    ├── test_scanners.py
    ├── test_risk.py
    ├── test_main.py
    ├── test_backtest.py
    ├── test_watchlist_manager.py
    ├── test_ep_earnings_scanner.py
    ├── test_ep_news_scanner.py
    └── test_alpaca_liquidity_filter.py

dashboard/                     # Next.js frontend (separate from trading-bot/)
├── app/                       # App Router pages: overview, positions, watchlist, performance, history
├── components/                # React components (shadcn/ui + Recharts)
└── lib/                       # API client, types, utils
```

---

## Key Module Reference

Detailed docs live in each module's README.md. Summary below.

| Module | Key exports |
|--------|-------------|
| `core/loader.py` | `load_strategies()`, `get_plugin()`, `StrategyPlugin` protocol |
| `core/data_cache.py` | `prefetch_daily_bars()`, `clear_daily_caches()` |
| `signals/base.py` | `compute_orh()`, `compute_vwap()`, `compute_sma()`, `compute_atr_from_list()`, `compute_rvol()`, `SignalResult` |
| `scanner/watchlist_manager.py` | `persist_candidates()`, `get_active_watchlist()`, `run_nightly_scan()`, `expire_stale_active()` |
| `risk/manager.py` | `calculate_position_size()`, `check_exposure()`, `check_daily_loss()`, `can_enter()` |
| `executor/alpaca_client.py` | `place_limit_order()`, `place_stop_order()`, `close_position()`, `get_candles_1m()`, `run_screener()` |
| `monitor/position_tracker.py` | Stop checks, partial exits, trailing MA close, parabolic targets |
| `db/models.py` | `Signal`, `Order`, `Position`, `Watchlist`, `DailyPnl` |

---

## Position Sizing

```python
risk_per_share = abs(entry_price - stop_price)
max_risk_dollars = portfolio_value * (risk_per_trade_pct / 100)  # default 1%
raw_shares = floor(max_risk_dollars / risk_per_share)

max_notional = portfolio_value * (max_position_pct / 100)        # default 15%
max_shares_by_notional = floor(max_notional / entry_price)

shares = min(raw_shares, max_shares_by_notional)
```

**ATR caps** prevent excessively wide stops:
- Breakout: stop capped at 1x ATR(14) from entry
- EP: stop capped at 1.5x ATR(14) from entry

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Alpaca | Pure REST + WebSocket, no gateway. Free paper trading. Native Python SDK. |
| yfinance for daily bars | Alpaca IEX feed covers ~2% of stocks. yfinance provides free daily OHLCV for full US universe. |
| SQLite first | Simpler dev setup. SQLAlchemy abstracts DB; PostgreSQL = connection string change only. |
| Plain pandas (no pandas-ta) | pandas-ta depends on numba, incompatible with Python 3.14. |
| Custom backtest (no vectorbt) | vectorbt also has numba issues. Custom engine matches exact signal/exit logic. |
| Limit orders for entries | Momentum names move fast. Limit orders prevent chasing (entry <= ORH + tolerance). |
| Strategy plugin architecture | Each strategy is self-contained with its own scanner, signal, config, and backtest logic. New strategies added without modifying core. |
