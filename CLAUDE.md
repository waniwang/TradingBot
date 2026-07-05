# Qullamaggie Trading Bot

Automated momentum trading bot inspired by Kristjan Kullamagi's setups: **Breakout** (long), **Episodic Pivot** (long), **EP Earnings** (swing), **EP News** (swing), **EP Breakout** (EP 2.0 swing, added 2026-07-05), and **Parabolic Short**. Trades US equities via Alpaca. Runs on a Linode VPS with a Next.js dashboard.

## Quick Reference

| What | Where |
|------|-------|
| Bot code | `trading-bot/` |
| Strategy plugins | `trading-bot/strategies/` — breakout, ep_breakout, ep_earnings, ep_news, episodic_pivot, parabolic_short |
| Core framework | `trading-bot/core/` — plugin loader, scheduler, data cache |
| Documentation | `docs/` (7 docs — read these for deep context) |
| Entry point (Alpaca) | `trading-bot/main.py` — APScheduler orchestrator |
| Entry point (IBKR paper, passive executor) | `trading-bot/main_ib.py` — runs only EP execute jobs; reads watchlist from Alpaca DB |
| Config | `trading-bot/config.yaml` (env vars override: `ALPACA_API_KEY`, etc.) |
| IB config (gitignored) | `trading-bot/config.ib.local.yaml` — sets `ibkr:`, `database_ib:`, and `watchlist_source_db_url` |
| Tests | `trading-bot/tests/` — 15 test files |
| Backtest | `trading-bot/backtest/` + `trading-bot/run_backtest.py` |
| Dashboard (FE) | `dashboard/` — Next.js + Tailwind + shadcn/ui (deploys to Vercel) |
| Dashboard API | `trading-bot/api/` — FastAPI (runs on Linode alongside bot) |
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
├── ep_earnings/       — scanner.py, strategy.py (Strategy B swing entries; A and C dropped 2026-05-08)
├── ep_news/           — scanner.py, strategy.py (Strategy A and B swing entries; C dropped 2026-05-08)
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
    (scanner/watchlist_manager.py)  (0.3% risk/trade,           (limit entries,
                                     8% max position notional,   GTC stop orders)
                                     position cap + daily/weekly
                                     loss limits DISABLED)
```

**Data sources:** Alpaca snapshots for gap scanning (IEX daily-snapshot coverage is ~99.7% — the "~2%" figure applies only to realtime intraday trade streams), yfinance for fundamentals (market cap, quoteType, earnings calendar), Alpaca 1m candles for intraday signals. Full Alpaca capability + quirks cheat sheet: [docs/alpaca-api.md](docs/alpaca-api.md).

**Scheduler (ET timezone):** 5:00 PM nightly scan → 6:00 AM premarket scan → 8:30 AM pre-market EP preview (read-only, Discord-only — see `core/premarket_preview.py`) → 9:25 AM finalize watchlist → 9:30 AM–4:00 PM intraday monitor (long-running window driven by the Alpaca 1-min bar stream registered at 9:25) → 9:40 AM EP time-partial check (D19+ in-profit positions: 40% off + stop→entry×1.05) + EP breakout +30% target partial (33% off, stop→max(stop, entry)) → 3:00 PM EP earnings scan + strategy eval → 3:05 PM EP news scan + strategy eval → 3:10 PM Discord candidate summary (read-only, decoupled from scans — see `core/discord.py`) → 3:15 PM EP breakout scan (gap events → stage="watching") → 3:50–3:59 PM EP earnings/news execute + EP breakout confirm state machine (retries every minute, idempotent) → 3:55 PM EOD tasks (incl. EP breakout breakeven move + MA10-close trail) → every 5 min reconcile → every 30s heartbeat.

Pipeline job metadata (descriptions, categories, `time`/`end_time` window, phase) lives exclusively in `api/constants.py::PIPELINE_SCHEDULE`. Edit entries there to change what the dashboard Pipeline page displays. `end_time` is set on jobs that run as a window (intraday monitor, execute retry loops); omit it for point-in-time jobs.

**Pipeline job visibility (dashboard):** jobs are only scheduled/displayed if an enabled strategy needs them. `premarket_scan` and `subscribe_watchlist` are owned by `breakout` + `episodic_pivot`; if neither is enabled, they don't register. `intraday_monitor` and `eod_tasks` are always-on. Ownership lives in `api/constants.py::JOB_OWNERS`; dashboard tabs: **All** / per-strategy / **Shared** (multi-owner + always-on).

**Database:** SQLAlchemy ORM, SQLite for dev/paper, PostgreSQL for live. All DB ops use `get_session(engine)` context manager.

### IBKR Paper (Passive Executor)

A second bot process (`main_ib.py` → `trading-bot-ib.service` on the Linode) runs the EP earnings + EP news strategies against an IBKR paper account in **passive-executor mode**: it does NOT scan. It reads `stage IN ("ready","triggered")` `Watchlist` rows from the Alpaca DB (`trading_bot.db`) and only places orders on IBKR. Every scanner/strategy fix shipped to the Alpaca code automatically applies to IB execution — single source of truth for trade ideas.

```
Alpaca bot                         IB bot
─────────────                      ──────
3:00 PM scan        ────────►   reads ready/triggered rows
3:37 PM execute (Alpaca)        executes on IBKR (parallel, idempotent)
```

- **Watchlist source**: configured via `config["watchlist_source_db_url"]` (or env `WATCHLIST_SOURCE_DB_URL`). Server config: `sqlite:////opt/trading-bot/trading-bot/trading_bot.db`. Without this key the IB bot falls back to local-DB reads and won't trade (since it doesn't scan).
- **Idempotency**: each bot's `job_execute` checks ITS OWN local DB (`Order` + `Position` tables) for an existing entry on `(ticker, setup_type)`. Both bots can safely run concurrently — the Alpaca bot tracks Alpaca executions in `trading_bot.db`, IB bot tracks IB executions in `trading_bot_ib.db`.
- **Skipped jobs**: `main_ib.py` passes `skip_jobs=("ep_earnings_scan", "ep_news_scan")` to `register_strategy_jobs()`. The IB scheduler runs only `ep_earnings_execute`, `ep_news_execute`, `eod_tasks`, `reconcile_positions`, `ib_watchdog`, `heartbeat`.
- **Cross-DB reads**: `executor/watchlist_source.py` opens a second SQLAlchemy engine pointing at `trading_bot.db`. Read-only — IB never writes to the Alpaca DB. Trade-paths must remain per-bot (Position/Order/Signal are local).
- **SQLite WAL**: `trading_bot.db` runs in WAL mode so the IB reader doesn't block Alpaca writes. Set once via `PRAGMA journal_mode=WAL`.
- **Plan + risks**: see `~/.claude/projects/.../memory/project_ib_passive_executor.md` for the detailed plan, deferred fallback enhancements, and operational runbook.

## CI/CD: GitHub Actions Auto-Deploy

On every push to `main`, `.github/workflows/deploy.yml` SSHs into the Linode server and runs `scripts/server-deploy.sh`, which pulls the latest code, runs DB migrations, and restarts the bot + dashboard services. Secrets (`SERVER_HOST`, `SERVER_SSH_KEY`) are stored in GitHub repo settings.

## Key Modules

| Module | Key functions / classes |
|--------|----------------------|
| `core/loader.py` | `load_strategies()`, `get_plugin()`, `get_registry()` — plugin discovery and registry |
| `core/scheduler.py` | `register_strategy_jobs()` — registers each plugin's scheduled jobs; supports `skip_jobs=` to opt out of named job_ids (used by `main_ib.py` to skip scan jobs) |
| `executor/watchlist_source.py` | `read_ready_entries()` — IB passive executor reads ready/triggered Watchlist rows from the Alpaca DB across processes (no scanning in IB bot) |
| `core/alerts.py` | `notify_job_failure()` — shared Telegram "JOB FAILED" sender with escalation on notify failure; invoked by both job wrappers |
| `core/discord.py` | `make_discord_notifier()`, `format_candidate_summary()` — Discord webhook transport + 3:10 PM candidate-summary formatter (no A/B/C tags, with catalyst headline). Hard 5s POST timeout. No-op when `discord.webhook_url` is unset |
| `core/news.py` | `fetch_headline()` — yfinance → Finnhub fallback for catalyst headlines. Hard 5s per-source timeout, partial-failure-in-batch (returns `None` rather than raising). Used by `job_discord_candidate_summary` (3:10 PM) + `job_premarket_ep_preview` (8:30 AM) |
| `core/earnings.py` | `fetch_recent_earnings_dates()` — yfinance → Finnhub fallback for earnings calendar. Hard 5s per-source timeout, raises `EarningsLookupError` if both fail (caller decides per-ticker skip vs batch-wide raise). Used by `strategies/ep_earnings/scanner._check_earnings_today` + `strategies/ep_news/scanner._confirm_no_earnings` |
| `core/premarket_preview.py` | `scan_premarket_gappers()`, `enrich_candidates()`, `format_premarket_preview()` — 8:30 AM ET pre-market scan using Alpaca `latest_trade` (since `daily_bar.open` is None pre-9:30). Filters gap>=5%, prev_close>=$3, mcap>=$800M; classifies earnings vs news via yfinance earnings calendar; top 10 to Discord. Read-only, never writes Watchlist |
| `core/data_cache.py` | Shared data cache for cross-strategy data reuse |
| `scanner/watchlist_manager.py` | `persist_candidates()`, `get_active_watchlist()`, `run_nightly_scan()`, `expire_stale_active()` — DB-backed watchlist lifecycle |
| `signals/base.py` | `compute_orh()`, `compute_orb_low()`, `compute_vwap()`, `compute_sma()`, `compute_atr_from_list()`, `SignalResult` |
| `risk/manager.py` | `calculate_position_size()`, `check_exposure()`, `check_daily_loss()`, `check_weekly_loss()` |
| `executor/alpaca_client.py` | `place_limit_order()`, `place_stop_order()`, `close_position()`, `get_candles_1m()`, `run_screener()`, `get_snapshots()` |
| `strategies/ep_earnings/scanner.py` | `scan_ep_earnings()` — universe filters: gap >8%, prev close >$3, mcap >$800M, open > prev high, open > 200d SMA, RVOL >1 |
| `strategies/ep_earnings/strategy.py` | `evaluate_ep_earnings_strategies()`, `evaluate_strategy_b()`, `compute_features()` — Strategy B only |
| `strategies/ep_earnings/plugin.py` | `job_scan`, `job_execute` — DB-driven Strategy B lifecycle |
| `strategies/ep_news/scanner.py` | EP news gap scanner |
| `strategies/ep_news/strategy.py` | News gap swing strategy evaluation — A and B mutually exclusive (A wins on overlap) |
| `strategies/ep_news/plugin.py` | `job_scan`, `job_execute` — DB-driven A/B scheduled lifecycle |
| `monitor/position_tracker.py` | Stop checks, partial exits, trailing MA close (daily close not intraday), parabolic profit targets, max hold period exit (50d for EP earnings) |
| `db/models.py` | `Signal`, `Order`, `Position`, `Watchlist`, `DailyPnl`, `JobExecution` — exit reasons: `stop_hit`, `trailing_stop`, `trailing_ma_close`, `parabolic_target`, `max_hold_period`, `manual`, `daily_loss_limit` |
| `backtest/runner.py` | `BacktestConfig`, `BacktestRunner.run()` — daily bar-by-bar simulation |
| `backtest/metrics.py` | `compute_metrics()` — win_rate, Sharpe, max_drawdown, CAGR, calmar, profit_factor |

## Conventions

- **No silent error swallowing — every failure must surface**: Errors must fail the pipeline and trigger a Telegram alert. **Do not** write `try/except Exception: logger.warning(...); fallback = {}` (or `= 0.0 / False / None / []`). That pattern marks the job `success` in `JobExecution` and hides the fault from the operator. Every scheduled job is wrapped by `_track_job` (main.py) or `_tracked_strategy_job` (core/scheduler.py), both of which call `core/alerts.py::notify_job_failure` on any uncaught exception — so the correct pattern is **let it propagate**. If you need a Telegram alert from inside a handler without failing the whole job (partial-failure case, e.g. one bad ticker in a loop), call `notify()` directly AND raise a `RuntimeError` with the combined message at the end if the failure is batch-wide. Retries belong at a proper retry layer (e.g. APScheduler cron `minute="50-59"` + idempotency guards), never a silent `except Exception: return default`. Specifically forbidden: `except Exception: return False/0.0/None/{}/[]`, `bars = {}` fallbacks, hardcoded portfolio values, swallowed yfinance/Alpaca API errors. Rule of thumb: if removing the `except` would surface a real bug sooner, remove it
- **Trade-path rule (stricter)**: every code path from `job_execute` → `_execute_entry` → `place_limit_order` / `mark_triggered` / `get_portfolio_value` / `calculate_position_size` / `resolve_execution_price` must either (1) let exceptions propagate to `_track_job` or (2) call `notify()` with a descriptive message before logging. Never silently log + return. A wrong-size trade is worse than no trade; a missed DB state update that re-triggers on restart is worse than a loud alert. Trade-path sites already audited: `main.py::_execute_entry`, `strategies/ep_earnings/plugin.py::job_execute`, `strategies/ep_news/plugin.py::job_execute`, `core/execution.py::resolve_execution_price`, `executor/alpaca_client.py::AlpacaClient.__init__` (stub-mode guard). Add to this list when touching trade code
- **Telegram is best-effort, not guaranteed**: if `notify()` itself fails, `_track_job` and `core/scheduler.py` elevate to `logger.error` and tag `JobExecution.result_summary += " [notify_failed]"` so the gap is visible via the dashboard / doctor endpoint. Don't build parallel alert channels; use this escalation path
- **Docs first**: Write/update docs before implementing code changes. After any code change, update the relevant README.md (strategy, module, or `docs/`) to keep docs in sync with code
- **Always commit and push after changes**: After completing any code change (and updating docs), offer to commit and push to `main`. Pushing to `main` triggers the GitHub Actions auto-deploy pipeline (`.github/workflows/deploy.yml`), which deploys to the Linode server. Do not leave changes uncommitted — either commit+push, or explicitly confirm with the user why they should stay local. During market hours, warn before deploying (see `bot.sh deploy`)
- **Dashboard param descriptions**: Descriptions + phase/variation tags for `config.yaml` `signals:` keys live in `trading-bot/api/param_meta.py`. Update there when adding a new strategy config key, or the Strategies page shows an empty description. A/B variation badges on signals/positions/trades are resolved at read time from `Watchlist.meta["ep_strategy"]` via `trading-bot/api/variation.py`. The Watchlist page reads the variation directly off the row (no join needed) — `meta["ep_strategy"]` carries A or B. Legacy C tags on existing positions still resolve via the `_c` setup_type suffix during the post-2026-05-08 transition window.
- **Plain pandas**: SMA/ATR use `pandas.rolling()` — no pandas-ta (incompatible with Python 3.14)
- **Python 3.14**: numba-dependent libraries (pandas-ta, vectorbt) won't work
- **Alpaca BarSet**: use `bars.data` dict, NOT `bars.get()` (BarSet lacks `.get`)
- **yfinance batch**: 1500 tickers ~14 min in batches of 500
- **lxml required**: `yfinance.Ticker.get_earnings_dates()` scrapes HTML via `pandas.read_html()` which requires `lxml`. Listed in `pyproject.toml`; must be present in any venv (local + server)
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

**API** (`trading-bot/api/`): Read-only FastAPI endpoints. Shares DB models with bot. Auth via `X-API-Key` header. Endpoints: `/api/status`, `/api/portfolio`, `/api/positions`, `/api/positions/closed`, `/api/watchlist`, `/api/attempts`, `/api/attempts/today`, `/api/performance/pnl`, `/api/performance/summary`, `/api/pipeline`, `/api/pipeline/history`, `/api/pipeline/job-detail`, `/api/risk`, `/api/market`.

**Trade Attempts model**: `/api/attempts` joins Signal + latest Order + Position into one row per attempt. The `outcome` field is the operator-facing label — replaces the old "what does Signal mean vs Position" confusion. Outcomes: `filled_open` (filled, position open), `filled_closed` (filled, position closed), `submitted` (working at broker), `did_not_fill` (`Order.status=cancelled` — limit didn't print, bot timed out), `broker_rejected` (`Order.status=rejected`). The legacy `/api/signals/today` and `/api/signals/history` endpoints have been removed; use `/api/attempts/today` and `/api/attempts?limit=N` instead. We deliberately do not split GTC `expired` from `did_not_fill` today because the bot only places day orders — if GTC orders are added, introduce an `expired` outcome then.

**Watchlist buckets**: `/api/watchlist` groups rows into seven buckets: `active`, `ready`, `watching`, `filled`, `order_failed`, `bot_error`, `expired`. The old single `cancelled` bucket has been split — `order_failed` is broker-side (cancelled/rejected order), `bot_error` is infrastructure-side (snapshot/fetch error at day-2 confirm, marked with `[bot-failure]` tag in `Watchlist.notes`). Different operator responses (market reality vs. bug to fix), so they're rendered with distinct colors and separate tabs.

**Dev workflow:**
```bash
# Terminal 1: API
cd trading-bot && .venv/bin/uvicorn api.main:app --reload --port 8000

# Terminal 2: Frontend
cd dashboard && npm run dev
```

## EP Swing Strategy (Integrated)

EP earnings and EP news swing strategies are integrated into the bot as strategy plugins (`strategies/ep_earnings/` and `strategies/ep_news/`).

**Execution is DB-driven and crash-safe.** `job_scan` (15:00 / 15:05) persists passing entries as `Watchlist(stage="ready")` with the full execution payload in `meta`. `job_execute` (15:37–15:59) reads ready rows from the DB — nothing held in memory, so process restarts between scan and execute are safe. `verify_day.py` Check 19 surfaces any drops.

### EP Earnings — Strategy B only: 44.7% WR, +4.95% avg, PF 2.57 (2020–2026 corrected)
1. CHG-OPEN% > 0
2. close_in_range >= 50
3. ATR% (10D) between 2–5%
4. Stop: -7% | Hold: 50D

### EP News — Strategy A: 57.6% WR, +11.93% avg, PF 5.34 (2020–2026 corrected)
1. CHG-OPEN% in (2, 10]
2. close_in_range >= 50
3. downside_from_open < 3%
4. ATR% in [3, 7]
5. Volume < 3M
6. Stop: -7% | Hold: 50D

### EP News — Strategy B (relaxed): 49.1% WR, +9.92% avg, PF 4.24 (2020–2026 corrected)
1. CHG-OPEN% in (2, 10]
2. close_in_range in [30, 80]
3. downside_from_open < 6%
4. ATR% in [3, 7]
5. Volume < 5M
6. Stop: -7% | Hold: 50D

A wins over B on overlap (tighter filters). A and B are kept because they catch different regimes — A wins in trending years, B-only wins in choppy/bear years.

### Exit pipeline (all three keepers)
1. **GTC -7% stop at broker** — automatic, fires on intraday low ≤ entry × 0.93
2. **9:40 AM ET time-partial check** (added 2026-05-11): for any open EP position at day 19+ that's currently in profit, cancel the GTC stop, market-sell 40% of position, place a new GTC stop at entry × 1.05 (locks in 5% guaranteed on the remaining 60%). Single-shot per position. Scheduled at 9:40 AM (not EOD 3:55 PM) so order-failure retries have ~6h of market time. See `monitor/position_tracker.py::check_ep_time_partial` and `tests/test_ep_time_partial.py`.
3. **3:55 PM EOD max-hold** — 50 calendar days forces close
4. **Reconcile drift detector** (every 5 min, added 2026-05-11) — catches any naked positions if stop placement fails after partial

Backtest impact of step 2 on corrected 2020-2026 data: PF 3.46→3.86, win rate 48.5%→61.3%, capital efficiency +20%. Total return -9% (giving up some upside on big winners) in exchange for locked-in gains + faster capital recycling. Year-by-year: improves 6 of 7 years (2020 roughly flat as the gangbuster year); 2021/2023/2024 jump from marginal to solid.

`_check_ma_close_exits` (the existing MA-trail close logic) is gated against EP setups so it doesn't fire on top of step 2's fixed-percentage trail.

### Dropped variants (2026-05-08)
- **EP Earnings A** dropped after corrected backtest showed A-only trades had PF 1.36 (worse than B alone).
- **Strategy C** (both setups) dropped after corrected backtest showed PF 1.85 / 2.25 — barely better than buying every gap, while contributing 343 trades/year of capital pressure.
- **EP News B stop** tightened from -10% to -7% (lifted PF from 3.48 to 4.24).
- See strategy READMEs for the full audit.

### Entry Timing
Entry = Close on gap day (~3:50 PM ET). Forward returns measured from gap day Close.

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
| `docs/alpaca-api.md` | Alpaca API cheat sheet — endpoints we use, endpoints tested & rejected, measured perf, tier quirks |

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
