# Core Framework

Plugin loader, scheduler integration, and shared data cache.

## loader.py — Plugin Discovery & Registry

Discovers and loads strategy plugins from `strategies/<name>/`. Each plugin must expose a `PLUGIN` instance conforming to the `StrategyPlugin` protocol.

**Key types:**
- `StrategyPlugin` (Protocol) — duck-typed interface all plugins implement
- `ScheduleEntry(job_id, cron, handler)` — declares a cron job for APScheduler
- `ExitAction(action, reason)` — strategy-specific exit signal ("partial" or "close")
- `BacktestEntryResult(entry_price, stop_price, side)` — backtest entry output

**Key functions:**
- `load_strategies(enabled: list[str])` — imports plugins, returns `{name: plugin_instance}`
- `get_registry()` — returns the loaded plugins dict
- `get_plugin(setup_type)` — lookup a single plugin by name

## scheduler.py — APScheduler Integration

Converts plugin-declared schedule entries into APScheduler cron jobs.

- `register_strategy_jobs(scheduler, plugins, config, client, db_engine, notify)` — registers each plugin's `schedule` list as CronTrigger jobs

## execution.py — Shared Trade-Path Helpers

Trade-path functions extracted from `main.py` so both the Alpaca bot (`main.py`) and the IBKR bot (`main_ib.py`, Phase 3 of the IB migration) can share them. Strategy plugins also import from here.

**Public API:**
- `is_trading_day(client)` — US equity trading-day check via Alpaca calendar, with weekday fallback
- `execute_entry(...)` — place entry limit order, persist Signal/Order rows, mark watchlist triggered, spawn background fill-wait thread (exposed as `_execute_entry` alias for backwards compatibility)
- `_compute_current_daily_pnl(db_engine)` / `_compute_current_weekly_pnl(db_engine)` — realized P&L for risk checks

**Internal helpers:** `_wait_for_fill`, `_await_fill_and_setup_stop`, `_safe_pnl_sum`.

All trade-path functions follow the no-silent-failure rule (see `CLAUDE.md`): exceptions propagate to `_track_job`, or `notify()` fires before logging.

## data_cache.py — Shared Daily Bar Cache

Thread-safe global caches to avoid per-ticker REST calls during intraday trading. Pre-fetches 130 days of daily bars for the watchlist via yfinance.

**Caches:** `daily_bars_cache`, `daily_closes_cache`, `daily_volumes_cache`, `daily_highs_cache`, `daily_lows_cache`

**Functions:**
- `prefetch_daily_bars(client, tickers)` — batch yfinance download into all caches
- `clear_daily_caches()` — called at start of each trading day
