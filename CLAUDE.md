# Qullamaggie Trading Bot

Automated momentum trading bot inspired by Kristjan Kullamagi's setups: **Breakout** (long), **Episodic Pivot** (long), **EP Earnings** (swing), **EP News** (swing), and **Parabolic Short**. Trades US equities via Alpaca. Runs on a Linode VPS with a Next.js dashboard.

## Quick Reference

| What | Where |
|------|-------|
| Bot code | `trading-bot/` |
| Strategy plugins | `trading-bot/strategies/` — breakout, ep_earnings, ep_news, episodic_pivot, parabolic_short |
| Core framework | `trading-bot/core/` — plugin loader, scheduler, data cache |
| Documentation | `docs/` (7 docs — read these for deep context) |
| Entry point | `trading-bot/main.py` — APScheduler orchestrator |
| Config | `trading-bot/config.yaml` (env vars override: `ALPACA_API_KEY`, etc.) |
| Tests | `trading-bot/tests/` — 345 tests across 9 files |
| Backtest | `trading-bot/backtest/` + `trading-bot/run_backtest.py` |
| Dashboard (FE) | `dashboard/` — Next.js + Tailwind + shadcn/ui (deploys to Vercel) |
| Dashboard API | `trading-bot/api/` — FastAPI (runs on Linode alongside bot) |
| Legacy dashboard | `trading-bot/dashboard_legacy/app.py` (Streamlit, deprecated) |
| Health check | `trading-bot/api/routes/doctor.py` — `/api/doctor` endpoint (no auth required) |
| Verification | `trading-bot/verify_day.py` — daily execution verification |
| Operations | `trading-bot/bot.sh` — start/stop/deploy/logs/status/verify |
| CI/CD | `.github/workflows/deploy.yml` — auto-deploy on push to main |
| Server | Linode at `root@172.235.216.175`, code at `/opt/trading-bot` |
| Dashboard URL | `https://dashboard-blond-iota-80.vercel.app` / Local: `http://localhost:3000` |
| API URL | Server: `http://172.235.216.175:8000/api` / Local: `http://localhost:8000/api` |

## Architecture

### Strategy Plugin System

Each strategy is a self-contained package under `strategies/<name>/` with its own `plugin.py`, `config.yaml`, scanner, signal/strategy, and backtest modules. Plugins are discovered and loaded by `core/loader.py` and registered with the scheduler via `core/scheduler.py`.

```
strategies/
├── breakout/          — scanner_nightly.py, scanner_premarket.py, signal.py, backtest.py
├── ep_earnings/       — scanner.py, strategy.py (Strategy A+B swing entries)
├── ep_news/           — scanner.py, strategy.py (news gap swing entries)
├── episodic_pivot/    — scanner.py, signal.py, backtest.py
└── parabolic_short/   — scanner.py, signal.py, exits.py, backtest.py
```

### Data Flow

```
Strategy Scanners (premarket)     Strategy Signals (market open)    Monitor (intraday + EOD)
├── breakout/scanner_*.py         ├── breakout/signal.py            ├── stop checks
├── ep_earnings/scanner.py        ├── episodic_pivot/signal.py      ├── partial exits (40% @ +15%)
├── ep_news/scanner.py            └── parabolic_short/signal.py     ├── trailing MA close (10d)
├── episodic_pivot/scanner.py                                       └── parabolic targets (10d/20d MA)
└── parabolic_short/scanner.py
         ↓                               ↓                              ↓
    Watchlist Manager ─────────→ Risk Manager ──────────────→ Alpaca Executor
    (scanner/watchlist_manager.py)  (1% risk/trade,             (limit entries,
                                     max 4 positions,            GTC stop orders)
                                     15% max position)
```

**Data sources:** Alpaca screener/snapshots for scanning, yfinance for daily bars (Alpaca free tier IEX covers ~2% of stocks), Alpaca 1m candles for intraday signals.

**Scheduler (ET timezone):** 5:00 PM nightly scan → 6:00 AM premarket scan → 9:25 AM finalize watchlist → 9:30 AM intraday monitor → 3:00 PM EP earnings scan + strategy eval → 3:05 PM EP news scan + strategy eval → 3:50 PM EP earnings/news execute → 3:55 PM EOD tasks → every 5 min reconcile → every 30s heartbeat.

**Database:** SQLAlchemy ORM, SQLite for dev/paper, PostgreSQL for live. All DB ops use `get_session(engine)` context manager.

## CI/CD: GitHub Actions Auto-Deploy

On every push to `main`, `.github/workflows/deploy.yml` SSHs into the Linode server and runs `scripts/server-deploy.sh`, which pulls the latest code, runs DB migrations, and restarts the bot + dashboard services. Secrets (`SERVER_HOST`, `SERVER_SSH_KEY`) are stored in GitHub repo settings.

## Key Modules

| Module | Key functions / classes |
|--------|----------------------|
| `core/loader.py` | `load_strategies()`, `get_plugin()`, `get_registry()` — plugin discovery and registry |
| `core/scheduler.py` | `register_strategy_jobs()` — registers each plugin's scheduled jobs |
| `core/data_cache.py` | Shared data cache for cross-strategy data reuse |
| `scanner/watchlist_manager.py` | `persist_candidates()`, `get_active_watchlist()`, `run_nightly_scan()`, `expire_stale_active()` — DB-backed watchlist lifecycle |
| `signals/base.py` | `compute_orh()`, `compute_orb_low()`, `compute_vwap()`, `compute_sma()`, `compute_atr_from_list()`, `SignalResult` |
| `risk/manager.py` | `calculate_position_size()`, `check_exposure()`, `check_daily_loss()`, `check_weekly_loss()` |
| `executor/alpaca_client.py` | `place_limit_order()`, `place_stop_order()`, `close_position()`, `get_candles_1m()`, `run_screener()`, `get_snapshots()` |
| `strategies/ep_earnings/scanner.py` | `scan_ep_earnings()` — universe filters: gap >8%, prev close >$3, mcap >$800M, open > prev high, open > 200d SMA, RVOL >1 |
| `strategies/ep_earnings/strategy.py` | `evaluate_ep_earnings_strategies()`, `evaluate_strategy_a()`, `evaluate_strategy_b()`, `compute_features()` — Strategy A+B entry filters |
| `strategies/ep_news/scanner.py` | EP news gap scanner |
| `strategies/ep_news/strategy.py` | News gap swing strategy evaluation |
| `monitor/position_tracker.py` | Stop checks, partial exits, trailing MA close (daily close not intraday), parabolic profit targets, max hold period exit (50d for EP earnings) |
| `db/models.py` | `Signal`, `Order`, `Position`, `Watchlist`, `DailyPnl`, `JobExecution` — exit reasons: `stop_hit`, `trailing_stop`, `trailing_ma_close`, `parabolic_target`, `max_hold_period`, `manual`, `daily_loss_limit` |
| `backtest/runner.py` | `BacktestConfig`, `BacktestRunner.run()` — daily bar-by-bar simulation |
| `backtest/metrics.py` | `compute_metrics()` — win_rate, Sharpe, max_drawdown, CAGR, calmar, profit_factor |

## Conventions

- **Docs first**: Write/update docs before implementing code changes. After any code change, update the relevant README.md (strategy, module, or `docs/`) to keep docs in sync with code
- **Plain pandas**: SMA/ATR use `pandas.rolling()` — no pandas-ta (incompatible with Python 3.14)
- **Python 3.14**: numba-dependent libraries (pandas-ta, vectorbt) won't work
- **Alpaca BarSet**: use `bars.data` dict, NOT `bars.get()` (BarSet lacks `.get`)
- **yfinance batch**: 1500 tickers ~14 min in batches of 500
- **Strategy plugins**: Each strategy lives in `strategies/<name>/` with `plugin.py`, `config.yaml`, scanner, signal/strategy modules
- **Tests**: `cd trading-bot && .venv/bin/pytest tests/ -v`

## Operations (bot.sh)

```bash
# Server (default target)
./bot.sh status              # systemd status + phase + heartbeat + next job
./bot.sh start               # start bot + dashboard services
./bot.sh stop                # stop both
./bot.sh restart             # restart both
./bot.sh logs                # tail server logs
./bot.sh deploy              # rsync code → migrate DB → restart (warns during market hours)
./bot.sh scan                # trigger manual scan
./bot.sh verify              # run daily verification (last trading day)
./bot.sh verify 2026-02-27   # verify specific date

# Local (for development)
./bot.sh local status
./bot.sh local start         # start as background processes with PID files
./bot.sh local stop
./bot.sh local logs
./bot.sh local verify        # run daily verification locally
```

## Health Check (Doctor)

When the user asks to check bot health, or when diagnosing issues:

```bash
# No auth required — works without API key
curl http://172.235.216.175:8000/api/doctor    # Server
curl http://localhost:8000/api/doctor           # Local
```

Returns `status`: `healthy`, `degraded`, or `critical` with three sub-checks:
- **heartbeat**: Is `bot_status.json` heartbeat < 2 minutes old?
- **systemd**: Is the `trading-bot` systemd service active?
- **jobs**: Any successful jobs? Jobs today? Stale running jobs? Recent failures?

`degraded` = some checks failing (e.g. failed jobs but bot still running). `critical` = both heartbeat AND systemd down. Investigate the `checks` object in the response to see which specific checks failed and why.

## Daily Verification

When the user asks to "verify yesterday's results" or "check yesterday's trading":

1. **Run the script**: `cd trading-bot && .venv/bin/python verify_day.py` (or specific date)
2. **Review automated checks**: Any FAIL items are immediate action items
3. **Follow the playbook**: Read `docs/daily-verification.md` for the full review process
4. **Key judgment checks** (beyond what the script automates):
   - Scanner quality: Do the watchlist candidates make sense? Check for missed obvious movers
   - Signal quality: Were entries at correct technical levels? Any chasing?
   - Exit quality: Were stops correct per strategy? Any premature exits?
   - Market context: What did SPY/QQQ do? Trending or choppy day?
5. **Summarize**: Working as expected, or issues found? List any code/parameter changes needed

## Current Status

- Phases 1-5 complete: foundation, scanners, signals, risk/execution, backtesting
- Strategy plugin architecture refactor complete — all strategies modularized under `strategies/`
- EP News swing strategy added
- Backtest results: EP is best strategy (Sharpe 1.08 OOS), tuned combined Sharpe 1.29 OOS, parabolic short unprofitable (disabled)
- GitHub Actions CI/CD auto-deploy pipeline active
- **Phase 6 (paper trading)**: next up
- **Phase 7 (Dashboard & Telegram)**: complete — dashboard rebuilt as Next.js + FastAPI (Streamlit deprecated)
- See `docs/implementation-plan.md` for full phase checklist

## Dashboard Architecture

```
dashboard/          → Next.js frontend (Vercel)
trading-bot/api/    → FastAPI backend (Linode, port 8000)
```

**Frontend** (`dashboard/`): Next.js 16, TypeScript, Tailwind v4, shadcn/ui, Recharts. Dark theme. Auto-refresh (30s market hours, 5m off hours). Pages: Overview (pipeline timeline, portfolio, risk meter, positions, equity chart, signals), Positions, Watchlist, Performance, History.

**API** (`trading-bot/api/`): Read-only FastAPI endpoints. Shares DB models with bot. Auth via `X-API-Key` header. Endpoints: `/api/status`, `/api/portfolio`, `/api/positions`, `/api/positions/closed`, `/api/watchlist`, `/api/signals/today`, `/api/performance/pnl`, `/api/performance/summary`, `/api/pipeline`, `/api/pipeline/history`, `/api/pipeline/job-detail`, `/api/risk`, `/api/market`.

**Dev workflow:**
```bash
# Terminal 1: API
cd trading-bot && .venv/bin/uvicorn api.main:app --reload --port 8000

# Terminal 2: Frontend
cd dashboard && npm run dev
```

## EP Swing Strategy (Integrated)

EP earnings and EP news swing strategies are now integrated into the bot as strategy plugins (`strategies/ep_earnings/` and `strategies/ep_news/`).

### EP Earnings Strategy A (Tight Filters): 69% WR, +9.18% avg, PF 5.68
1. CHG-OPEN% > 0 (positive intraday)
2. close_in_range >= 50 (closed in top half of range)
3. downside_from_open < 3% (didn't dip much)
4. Prev 10D change% between -30% and -10% (sold off before earnings)
5. Stop: -7% | Hold: 50D

### EP Earnings Strategy B (Relaxed): 61% WR, +11.75% avg, PF 5.62
1. CHG-OPEN% > 0
2. close_in_range >= 50
3. ATR% between 2-5%
4. Prev 10D change% < -10%
5. Stop: -7% | Hold: 50D

### Kill Zones (Avoid)
- Prev 10D > 0% (ran up into earnings): 31% WR, -7.4% mean
- CHG-OPEN% < 0 AND close_in_range < 50: 40% WR

### Entry Timing
All filters require the gap day to complete. Entry = Close on gap day (~3:50 PM ET). Forward returns measured from gap day Close.

## Documentation Index

### Main Docs

| Doc | Contents |
|-----|----------|
| `README.md` | Strategy overview, risk rules, exit rules, bot flow, backtest results, getting started |
| `docs/architecture.md` | Tech stack, data flow, project structure, module reference, design decisions |
| `docs/config-reference.md` | Full config.yaml schema with all parameters |
| `docs/operations.md` | Bot operations: start/stop/deploy/verify/scan commands, troubleshooting |
| `docs/backtesting.md` | Test plan, backtest procedures, results, paper trading checklist |
| `docs/daily-verification.md` | Daily verification playbook, diagnostics, parameter tuning reference |
| `docs/risks-and-mitigations.md` | Known risks and how they're handled |
| `docs/implementation-plan.md` | Phase-by-phase build plan with checklists |

### Strategy & Module Docs

Each strategy and shared module has its own README.md with scanner filters, signal conditions, and config details.

| Doc | Contents |
|-----|----------|
| `strategies/episodic_pivot/README.md` | EP scanner filters, ORH signal, config |
| `strategies/ep_earnings/README.md` | Earnings gap swing: A/B rules, kill zones |
| `strategies/ep_news/README.md` | News gap swing: A/B rules |
| `strategies/breakout/README.md` | Two-phase scan, consolidation requirements, signal |
| `strategies/parabolic_short/README.md` | Parabolic short reference (disabled) |
| `core/README.md` | Plugin loader, scheduler, data cache |
| `scanner/README.md` | Watchlist lifecycle, consolidation, momentum ranking |
| `signals/README.md` | SignalResult, ORH/ORB, VWAP, SMA, ATR, RVOL |
