# Qullamaggie Trading Bot

Automated momentum trading bot inspired by Kristjan Kullamagi's 3 setups: **Breakout** (long), **Episodic Pivot** (long), and **Parabolic Short**. Trades US equities via Alpaca. Runs on a Linode VPS with a Streamlit dashboard.

## Quick Reference

| What | Where |
|------|-------|
| Bot code | `trading-bot/` |
| Documentation | `docs/` (7 docs — read these for deep context) |
| Entry point | `trading-bot/main.py` — APScheduler orchestrator |
| Config | `trading-bot/config.yaml` (env vars override: `ALPACA_API_KEY`, etc.) |
| Tests | `trading-bot/tests/` — 240 tests across 7 files |
| Backtest | `trading-bot/backtest/` + `trading-bot/run_backtest.py` |
| Dashboard | `trading-bot/dashboard/app.py` (Streamlit) |
| Verification | `trading-bot/verify_day.py` — daily execution verification |
| Operations | `trading-bot/bot.sh` — start/stop/deploy/logs/status/verify |
| Server | Linode at `root@172.235.216.175`, code at `/opt/trading-bot` |
| Dashboard URL | Server: `http://172.235.216.175:8501` / Local: `http://localhost:8501` |

## Architecture

```
Scanners (premarket)          Signals (market open)         Monitor (intraday + EOD)
├── gapper.py (EP)            ├── breakout.py               ├── stop checks
├── momentum_rank.py (BO)     ├── episodic_pivot.py         ├── partial exits (40% @ +15%)
├── consolidation.py (BO)     └── parabolic_short.py        ├── trailing MA close (10d)
└── parabolic.py (short)                                    └── parabolic targets (10d/20d MA)
         ↓                           ↓                              ↓
    Watchlist ──────────────→ Risk Manager ──────────────→ Alpaca Executor
                              (1% risk/trade,               (limit entries,
                               max 4 positions,              GTC stop orders)
                               15% max position)
```

**Data sources:** Alpaca screener/snapshots for scanning, yfinance for daily bars (Alpaca free tier IEX covers ~2% of stocks), Alpaca 1m candles for intraday signals.

**Scheduler (ET timezone):** 5:00 PM nightly scan → 6:00 AM premarket scan → 9:25 AM finalize watchlist → 9:30 AM intraday monitor → 3:55 PM EOD tasks → every 5 min reconcile → every 30s heartbeat.

**Database:** SQLAlchemy ORM, SQLite for dev/paper, PostgreSQL for live. All DB ops use `get_session(engine)` context manager.

## Key Modules

| Module | Key functions / classes |
|--------|----------------------|
| `signals/base.py` | `compute_orh()`, `compute_orb_low()`, `compute_vwap()`, `compute_sma()`, `compute_atr_from_list()`, `SignalResult` |
| `risk/manager.py` | `calculate_position_size()`, `check_exposure()`, `check_daily_loss()`, `check_weekly_loss()` |
| `executor/alpaca_client.py` | `place_limit_order()`, `place_stop_order()`, `close_position()`, `get_candles_1m()`, `run_screener()`, `get_snapshots()` |
| `monitor/position_tracker.py` | Stop checks, partial exits, trailing MA close (daily close not intraday), parabolic profit targets |
| `db/models.py` | `Signal`, `Order`, `Position`, `Watchlist`, `DailyPnl` — exit reasons: `stop_hit`, `trailing_stop`, `trailing_ma_close`, `parabolic_target`, `manual`, `daily_loss_limit` |
| `backtest/runner.py` | `BacktestConfig`, `BacktestRunner.run()` — daily bar-by-bar simulation |
| `backtest/metrics.py` | `compute_metrics()` — win_rate, Sharpe, max_drawdown, CAGR, calmar, profit_factor |

## Conventions

- **Docs first**: Write/update markdown in `docs/` before implementing code changes
- **Plain pandas**: SMA/ATR use `pandas.rolling()` — no pandas-ta (incompatible with Python 3.14)
- **Python 3.14**: numba-dependent libraries (pandas-ta, vectorbt) won't work
- **Alpaca BarSet**: use `bars.data` dict, NOT `bars.get()` (BarSet lacks `.get`)
- **yfinance batch**: 1500 tickers ~14 min in batches of 500
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
- Backtest results: EP is best strategy (Sharpe 1.08 OOS), tuned combined Sharpe 1.29 OOS, parabolic short unprofitable (disabled)
- **Phase 6 (paper trading)**: next up
- **Phase 7 (Dashboard & Telegram)**: complete
- See `docs/implementation-plan.md` for full phase checklist

## Documentation Index

| Doc | Contents |
|-----|----------|
| `README.md` | Strategy, risk rules, exit rules, bot flow, backtest results, getting started |
| `docs/architecture.md` | Tech stack, data flow, project structure, module reference, design decisions |
| `docs/config-reference.md` | Full config.yaml schema with all parameters |
| `docs/operations.md` | Bot operations: start/stop/deploy/verify/scan commands, troubleshooting |
| `docs/backtesting.md` | Test plan, backtest procedures, results, paper trading checklist |
| `docs/daily-verification.md` | Daily verification playbook, diagnostics, parameter tuning reference |
| `docs/risks-and-mitigations.md` | Known risks and how they're handled |
| `docs/implementation-plan.md` | Phase-by-phase build plan with checklists |
