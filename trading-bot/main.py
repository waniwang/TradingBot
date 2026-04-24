"""
Main entry point.

APScheduler orchestrates all jobs:
  6:00 AM ET  — pre-market scan (Alpaca screener + snapshots)
  9:25 AM ET  — finalize watchlist, subscribe Alpaca real-time
  9:30 AM ET  — start intraday signal monitor
  3:00 PM ET  — EP earnings scan + strategy A/B evaluation
  3:50 PM ET  — EP earnings entry execution (limit orders near close)
  3:55 PM ET  — EOD tasks: trailing stop updates, max hold exits, P&L summary
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.loader import load_strategies, get_registry, get_plugin
from core.scheduler import register_strategy_jobs
from core import data_cache
from core.execution import (
    is_trading_day,
    execute_entry as _execute_entry,
    _compute_current_daily_pnl,
    _compute_current_weekly_pnl,
)
from db.models import init_db, get_session, JobExecution
from executor.alpaca_client import AlpacaClient
from monitor.position_tracker import PositionTracker
from risk.manager import RiskManager
from scanner.watchlist_manager import (
    mark_triggered,
    get_pipeline_counts,
    persist_candidates,
    expire_stale_active,
    get_active_watchlist,
    get_watching_tickers,
    purge_disabled_strategies,
    run_nightly_scan,
)
from signals.base import compute_sma

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("trading_bot.log"),
    ],
)
logger = logging.getLogger("main")

ET = pytz.timezone("America/New_York")


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    # Allow env var overrides
    if os.environ.get("ALPACA_API_KEY"):
        cfg.setdefault("alpaca", {})["api_key"] = os.environ["ALPACA_API_KEY"]
    if os.environ.get("ALPACA_SECRET_KEY"):
        cfg.setdefault("alpaca", {})["secret_key"] = os.environ["ALPACA_SECRET_KEY"]
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram"]["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg["telegram"]["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]

    # Merge per-strategy config.yaml files into cfg["strategies"]
    strategies_dir = Path(__file__).parent / "strategies"
    cfg.setdefault("strategies", {})
    if strategies_dir.is_dir():
        for d in sorted(strategies_dir.iterdir()):
            if d.is_dir() and (d / "config.yaml").exists():
                try:
                    with open(d / "config.yaml") as sf:
                        strategy_cfg = yaml.safe_load(sf) or {}
                    cfg["strategies"].setdefault(d.name, {}).update(strategy_cfg)
                except Exception as e:
                    logger.warning("Failed to load strategy config %s: %s", d / "config.yaml", e)

    return cfg


# ---------------------------------------------------------------------------
# Global state (populated during the pre-market phase)
# ---------------------------------------------------------------------------

_watchlist: list[dict] = []          # [{ticker, setup_type, gap_pct, ...}]
_db_engine = None                    # set in main(), used by _write_status
_notify_fn = None                    # set in main(), used by _track_job for failure alerts
# Daily bar caches now live in core.data_cache (shared module)
_daily_bars_cache = data_cache.daily_bars_cache
_daily_closes_cache = data_cache.daily_closes_cache
_daily_volumes_cache = data_cache.daily_volumes_cache
_daily_highs_cache = data_cache.daily_highs_cache
_daily_lows_cache = data_cache.daily_lows_cache
_cache_lock = data_cache.cache_lock
_entry_locks: dict[str, threading.Lock] = {}
_entry_locks_meta = threading.Lock()  # guards _entry_locks dict itself


def _get_entry_lock(ticker: str) -> threading.Lock:
    """Return a per-ticker lock to prevent concurrent entry evaluation."""
    with _entry_locks_meta:
        if ticker not in _entry_locks:
            _entry_locks[ticker] = threading.Lock()
        return _entry_locks[ticker]


def _clear_daily_caches():
    """Clear all daily bar caches. Called at start of each trading day."""
    data_cache.clear_daily_caches()


def _prefetch_daily_bars(client, tickers: list[str], notify=None):
    """Pre-fetch daily bars via shared data_cache module."""
    data_cache.prefetch_daily_bars(client, tickers, notify=notify)


# ---------------------------------------------------------------------------
# Telegram notifier (simple async wrapper)
# ---------------------------------------------------------------------------

def make_notifier(config: dict):
    token = config["telegram"].get("bot_token", "")
    chat_id_raw = str(config["telegram"].get("chat_id", "") or "")
    chat_ids = [c.strip() for c in chat_id_raw.split(",") if c.strip()]
    if not token or not chat_ids:
        return lambda msg: logger.info("[Telegram stub] %s", msg)

    import asyncio
    import threading
    from telegram import Bot

    bot = Bot(token=token)

    # Dedicated event loop in its own thread — safe to call from APScheduler,
    # watchdog threads, or any other background thread without "no current event
    # loop" errors.
    _loop = asyncio.new_event_loop()
    _loop_thread = threading.Thread(
        target=_loop.run_forever, daemon=True, name="telegram-loop"
    )
    _loop_thread.start()

    def notify(message: str):
        async def _send():
            for cid in chat_ids:
                try:
                    await bot.send_message(chat_id=cid, text=message)
                except Exception as e:
                    logger.warning("Telegram send failed (chat_id=%s): %s", cid, e)

        asyncio.run_coroutine_threadsafe(_send(), _loop)

    return notify


# ---------------------------------------------------------------------------
# Market calendar helpers
# ---------------------------------------------------------------------------

# is_trading_day is now imported from core.execution (shared with main_ib.py)


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

def _format_watchlist_notification(watchlist: list[dict]) -> str:
    """Format the watchlist into a Telegram-friendly summary message."""
    if not watchlist:
        return "WATCHLIST READY: 0 candidates"

    lines = [f"WATCHLIST READY: {len(watchlist)} candidates"]

    # Group by setup type
    by_setup: dict[str, list[dict]] = {}
    for c in watchlist:
        setup = c.get("setup_type", "unknown")
        by_setup.setdefault(setup, []).append(c)

    registry = get_registry()
    setup_labels = {name: p.display_name for name, p in registry.items()}
    # Fallback for any setup_type not in registry
    setup_labels.setdefault("episodic_pivot", "EP")
    setup_labels.setdefault("breakout", "Breakout")
    setup_labels.setdefault("parabolic_short", "Parabolic Short")

    for setup, items in by_setup.items():
        label = setup_labels.get(setup, setup)
        parts = []
        for c in items:
            ticker = c["ticker"]
            gap = c.get("gap_pct")
            if gap:
                parts.append(f"{ticker} (+{gap:.1f}%)")
            else:
                parts.append(ticker)
        lines.append(f"{label}: {', '.join(parts)}")

    return "\n".join(lines)


def job_premarket_scan(config: dict, client: AlpacaClient, db_engine, notify=None, force: bool = False):
    """6:00 AM ET — scan for EP gappers + read breakout candidates from DB."""
    global _watchlist
    if not force and not is_trading_day(client):
        logger.info("Pre-market scan skipped — not a trading day")
        return
    _set_phase("premarket_scan")
    logger.info("=== PRE-MARKET SCAN START ===")
    if notify:
        notify("PRE-MARKET SCAN STARTED")

    # Clear stale caches from yesterday
    _clear_daily_caches()

    today = datetime.now(ET).date()
    plugins = get_registry()

    # 1. Expire/demote yesterday's stale entries (uses plugin.watchlist_persist_days).
    # Let exceptions propagate — a failure here means tomorrow's watchlist state
    # is suspect and the operator must know.
    expire_stale_active(today, db_engine, plugins=plugins)

    # 2. Run premarket scan for each enabled strategy. Any plugin raising here
    # fails the whole job so _track_job fires "JOB FAILED" via Telegram. We'd
    # rather lose one scan-cycle than ship bad watchlist state silently.
    for plugin in plugins.values():
        _set_progress(f"Scanning {plugin.display_name}")
        candidates = plugin.premarket_scan(config, client, db_engine, notify)
        count = len(candidates) if isinstance(candidates, list) else 0
        if isinstance(candidates, list) and candidates:
            persist_candidates(candidates, plugin.name, "active", today, db_engine)
        logger.info("%s premarket scan: %d candidates", plugin.display_name, count)

    # 5. Load unified active watchlist from DB (filtered to enabled strategies)
    enabled_names = list(plugins.keys())
    _watchlist = get_active_watchlist(db_engine, enabled=enabled_names)[:20]
    _set_phase("watchlist_ready")
    logger.info("=== PRE-MARKET SCAN DONE: %d candidates ===", len(_watchlist))
    for c in _watchlist:
        logger.info("  %s [%s]", c["ticker"], c["setup_type"])

    # 6. Pre-fetch daily bars for all watchlist tickers (yfinance batch)
    # This populates caches so on_bar callbacks don't need per-ticker REST calls
    if _watchlist:
        _set_progress("Prefetching daily bars", f"{len(_watchlist)} tickers")
        _prefetch_daily_bars(client, [c["ticker"] for c in _watchlist], notify=notify)

    _set_progress()  # clear progress
    # Send Telegram notifications
    if notify:
        notify(_format_watchlist_notification(_watchlist))

    # Return summary for job tracking
    by_setup: dict[str, int] = {}
    for c in _watchlist:
        st = c.get("setup_type", "other")
        by_setup[st] = by_setup.get(st, 0) + 1
    parts = [f"{v} {k}" for k, v in by_setup.items()]
    return f"{len(_watchlist)} candidates ({', '.join(parts)})" if _watchlist else "0 candidates"


def job_subscribe_watchlist(
    client: AlpacaClient,
    config: dict,
    tracker: PositionTracker,
    risk: RiskManager,
    db_engine,
    notify,
):
    """9:25 AM ET — subscribe to Alpaca real-time bars for watchlist."""
    if not is_trading_day(client):
        logger.info("Subscribe watchlist skipped — not a trading day")
        return "Skipped — not a trading day"
    if not _watchlist:
        logger.info("Watchlist empty — nothing to subscribe")
        return
    tickers = [c["ticker"] for c in _watchlist]

    # Also subscribe yesterday's Strategy C candidates (stage=watching) so their
    # prices are cached in data_cache.intraday_price_cache by on_bar throughout
    # the day. fetch_current_price reads that cache at 3:35 PM, avoiding the
    # congested Alpaca snapshot REST endpoint near market close.
    watching = get_watching_tickers(db_engine)
    extra = [t for t in watching if t not in tickers]
    if extra:
        logger.info("Also subscribing %d day-2 watching tickers for price cache: %s", len(extra), extra)
        tickers = tickers + extra

    logger.info("Subscribing to real-time data for %d tickers", len(tickers))

    def on_bar(bar: dict):
        """Called by AlpacaClient stream for every 1m bar update."""
        try:
            ticker = bar["ticker"]
            current_price = bar["close"]

            # Keep intraday price cache warm — used by day2_confirm at 3:35 PM
            # to avoid a cold REST snapshot call on the congested Alpaca endpoint.
            data_cache.update_intraday_price(ticker, current_price)

            # Fetch ALL of today's 1m candles (up to 390 for a full day)
            candles_1m = client.get_candles_1m(ticker, count=390)
            with _cache_lock:
                daily_bars = _daily_bars_cache.get(ticker)
            if not daily_bars:
                daily_bars = client.get_daily_bars(ticker, days=130)
            daily_closes = [b["close"] for b in daily_bars]
            daily_volumes = [b["volume"] for b in daily_bars]
            daily_highs = [b["high"] for b in daily_bars]
            daily_lows = [b["low"] for b in daily_bars]
            with _cache_lock:
                _daily_bars_cache[ticker] = daily_bars
                _daily_closes_cache[ticker] = daily_closes
                _daily_volumes_cache[ticker] = daily_volumes
                _daily_highs_cache[ticker] = daily_highs
                _daily_lows_cache[ticker] = daily_lows

            # Cumulative volume from all of today's 1m candles
            today_volume = sum(c["volume"] for c in candles_1m)

            # Compute minutes since market open (9:30 ET)
            from zoneinfo import ZoneInfo
            et_now = datetime.now(ZoneInfo("America/New_York"))
            market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
            minutes_since_open = max(1, int((et_now - market_open).total_seconds() / 60))

            process_ticker_update(
                ticker=ticker,
                config=config,
                client=client,
                tracker=tracker,
                risk=risk,
                db_engine=db_engine,
                notify=notify,
                candles_1m=candles_1m,
                daily_closes=daily_closes,
                daily_volumes=daily_volumes,
                daily_highs=daily_highs,
                daily_lows=daily_lows,
                current_price=current_price,
                current_volume=today_volume,
                minutes_since_open=minutes_since_open,
            )
        except Exception as e:
            logger.error("on_bar error for %s: %s", bar.get("ticker", "?"), e, exc_info=True)
            notify(f"ERROR in on_bar for {bar.get('ticker', '?')}: {e}")

    try:
        client.subscribe_quotes(tickers, callback=on_bar)
        _set_phase("observing")
    except Exception as e:
        logger.error("STREAM SUBSCRIPTION FAILED: %s", e, exc_info=True)
        notify(f"CRITICAL: Stream subscription failed — NO SIGNALS WILL FIRE today.\n{e}")
        return f"FAILED: {e}"
    return f"Subscribed to {len(tickers)} tickers"


def job_intraday_monitor(
    config: dict,
    client: AlpacaClient,
    tracker: PositionTracker,
    risk: RiskManager,
    db_engine,
    notify,
):
    """9:30 AM ET — log confirmation that the stream is running."""
    if not is_trading_day(client):
        logger.info("Intraday monitor skipped — not a trading day")
        return "Skipped — not a trading day"
    logger.info("=== INTRADAY MONITOR STARTED — stream active ===")
    # Data processing is driven by the Alpaca WebSocket stream callback
    # registered in job_subscribe_watchlist at 9:25 AM.
    return "Stream active"


def process_ticker_update(
    ticker: str,
    config: dict,
    client: AlpacaClient,
    tracker: PositionTracker,
    risk: RiskManager,
    db_engine,
    notify,
    # Pre-fetched by stream callback — avoids redundant API calls
    candles_1m: list | None = None,
    daily_closes: list | None = None,
    daily_volumes: list | None = None,
    daily_highs: list | None = None,
    daily_lows: list | None = None,
    current_price: float | None = None,
    current_volume: int | None = None,
    minutes_since_open: int | None = None,
):
    """
    Called for each ticker on every 1m candle update (via Alpaca stream callback).
    Checks signals for watchlist items and manages open positions.
    """
    # Use pre-fetched data when available (from stream callback)
    if candles_1m is None or current_price is None:
        try:
            candles_1m = client.get_candles_1m(ticker, count=390)
            bar = client.get_latest_bar(ticker)
            current_price = bar["last_price"]
            current_volume = bar["volume"]
        except Exception as e:
            logger.warning("Failed to get data for %s: %s", ticker, e)
            return

    if daily_closes is None:
        with _cache_lock:
            daily_closes = list(_daily_closes_cache.get(ticker, []))
    if daily_volumes is None:
        with _cache_lock:
            daily_volumes = list(_daily_volumes_cache.get(ticker, []))
    if daily_highs is None:
        with _cache_lock:
            daily_highs = list(_daily_highs_cache.get(ticker, []))
    if daily_lows is None:
        with _cache_lock:
            daily_lows = list(_daily_lows_cache.get(ticker, []))
    if current_volume is None:
        current_volume = 0

    # Compute minutes_since_open if not provided
    if minutes_since_open is None:
        from zoneinfo import ZoneInfo
        et_now = datetime.now(ZoneInfo("America/New_York"))
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_since_open = max(1, int((et_now - market_open).total_seconds() / 60))

    # Always manage open positions (stop checks, partial exits) even when halted
    tracker.on_candle_update(ticker, current_price, candles_1m, daily_closes)

    # Skip new entries when halted
    if tracker.is_halted:
        return

    # Check if this ticker is on the watchlist (no open position yet)
    watchlist_entry = next((c for c in _watchlist if c["ticker"] == ticker), None)
    if watchlist_entry is None:
        return

    # Acquire per-ticker lock: prevents two bar events from both passing the
    # duplicate check and submitting two orders in the same millisecond window
    with _get_entry_lock(ticker):
        _evaluate_and_enter(
            ticker, watchlist_entry, candles_1m, daily_closes, daily_volumes,
            daily_highs, daily_lows,
            current_price, current_volume, config, client, tracker, risk, db_engine, notify,
            minutes_since_open=minutes_since_open,
        )


def _evaluate_and_enter(
    ticker, watchlist_entry, candles_1m, daily_closes, daily_volumes,
    daily_highs, daily_lows,
    current_price, current_volume, config, client, tracker, risk, db_engine, notify,
    minutes_since_open=None,
):
    """Inner entry logic — must be called while holding the per-ticker entry lock."""
    from db.models import Position, Order

    with get_session(db_engine) as session:
        already_open = session.query(Position).filter_by(ticker=ticker, is_open=True).count()
        if already_open > 0:
            return
        # Block if any non-terminal order exists for this ticker (includes filled orders
        # where the position record hasn't been created yet by the background thread)
        active_order = session.query(Order).filter(
            Order.ticker == ticker,
            Order.status.in_(["pending", "submitted", "filled", "partially_filled"]),
            Order.created_at >= datetime.utcnow() - timedelta(minutes=10),
        ).count()
        if active_order > 0:
            return

    portfolio_value = client.get_portfolio_value()
    daily_pnl = _compute_current_daily_pnl(db_engine)
    weekly_pnl = _compute_current_weekly_pnl(db_engine)

    with get_session(db_engine) as session:
        open_count = session.query(Position).filter_by(is_open=True).count()

    can_enter, block_reason = risk.can_enter(
        open_count, daily_pnl, weekly_pnl, portfolio_value
    )
    if not can_enter:
        if block_reason in ("daily_loss_limit", "weekly_loss_limit"):
            tracker.set_daily_halt(block_reason == "daily_loss_limit")
            tracker.set_weekly_halt(block_reason == "weekly_loss_limit")
            notify(f"Trading halted: {block_reason}")
        return

    # Check if strategy is enabled via plugin registry
    setup = watchlist_entry["setup_type"]
    plugin = get_plugin(setup)
    if plugin is None:
        logger.debug("Skipping %s for %s — strategy not loaded", setup, ticker)
        return

    # Evaluate signal via strategy plugin
    sig = plugin.evaluate_signal(
        ticker,
        watchlist_entry,
        candles_1m=candles_1m,
        daily_closes=daily_closes,
        daily_volumes=daily_volumes,
        daily_highs=daily_highs,
        daily_lows=daily_lows,
        current_price=current_price,
        current_volume=current_volume,
        config=config,
        minutes_since_open=minutes_since_open,
    )

    if sig is None:
        return

    # Validate signal prices — never trade on NaN or impossible values
    if not _validate_signal(ticker, sig):
        return

    # Size and place entry
    shares = risk.calculate_position_size(portfolio_value, sig.entry_price, sig.stop_price)
    if shares <= 0:
        logger.info("%s: position size = 0, skipping", ticker)
        return

    _execute_entry(ticker, sig, shares, client, db_engine, notify)


def _validate_signal(ticker: str, sig) -> bool:
    """Return False and log an error if signal prices are invalid."""
    for name, val in [("entry_price", sig.entry_price), ("stop_price", sig.stop_price)]:
        if not isinstance(val, (int, float)) or math.isnan(val) or val <= 0:
            logger.error("%s: invalid %s in signal: %s — skipping", ticker, name, val)
            return False
    if sig.side == "long" and sig.stop_price >= sig.entry_price:
        logger.error("%s: long stop %.2f must be below entry %.2f — skipping",
                     ticker, sig.stop_price, sig.entry_price)
        return False
    if sig.side == "short" and sig.stop_price <= sig.entry_price:
        logger.error("%s: short stop %.2f must be above entry %.2f — skipping",
                     ticker, sig.stop_price, sig.entry_price)
        return False
    return True


# _wait_for_fill, _await_fill_and_setup_stop, _execute_entry now live in
# core/execution.py and are imported at the top of this file.


def job_eod_tasks(
    config: dict,
    client: AlpacaClient,
    tracker: PositionTracker,
    db_engine,
    notify,
):
    """3:55 PM ET — trailing stop updates, P&L, Telegram summary."""
    if not is_trading_day(client):
        logger.info("EOD tasks skipped — not a trading day")
        return
    _set_phase("end_of_day")
    logger.info("=== EOD TASKS START ===")

    # Expire unfired active entries at end of day
    today = datetime.now(ET).date()
    try:
        expire_stale_active(today, db_engine)
    except Exception as e:
        logger.warning("Failed to expire active entries at EOD: %s", e)

    # Fetch current prices from broker — used both for today's close proxy and P&L
    current_prices = {}
    try:
        broker_positions = client.get_open_positions()
        for bp in broker_positions:
            if bp.get("current_price", 0) > 0:
                current_prices[bp["symbol"]] = bp["current_price"]
    except Exception as e:
        logger.warning("Could not fetch broker positions for P&L: %s", e)

    # Pass a snapshot of the cache with today's close appended from broker prices
    # so the trailing MA check uses today's actual close (not yesterday's)
    with _cache_lock:
        closes_snapshot = {k: list(v) for k, v in _daily_closes_cache.items()}
    for ticker, price in current_prices.items():
        if ticker in closes_snapshot:
            closes_snapshot[ticker].append(price)
    tracker.run_eod_tasks(closes_snapshot)

    # Compute daily P&L
    portfolio_value = client.get_portfolio_value()
    daily = tracker.compute_daily_pnl(portfolio_value, current_prices=current_prices)

    sign = "+" if daily.total_pnl >= 0 else ""
    summary = (
        f"EOD SUMMARY\n"
        f"Date: {daily.trade_date}\n"
        f"P&L: {sign}${daily.total_pnl:.2f}\n"
        f"Realized: ${daily.realized_pnl:.2f}\n"
        f"Trades: {daily.num_trades} ({daily.num_winners}W / {daily.num_losers}L)\n"
        f"Portfolio: ${portfolio_value:,.0f}"
    )
    notify(summary)
    logger.info(summary)

    # Reset daily halt for next day
    tracker.set_daily_halt(False)
    # Reset weekly halt on Friday EOD (new week starts Monday)
    from zoneinfo import ZoneInfo
    et_now = datetime.now(ZoneInfo("America/New_York"))
    if et_now.weekday() == 4:  # Friday
        tracker.set_weekly_halt(False)
        logger.info("Weekly halt reset (end of week)")
    _set_phase("idle")
    return f"P&L: {sign}${daily.total_pnl:.2f}, {daily.num_trades} trades"


def job_nightly_watchlist_scan(config: dict, client: AlpacaClient, db_engine, notify, force: bool = False):
    """5:00 PM ET — run heavy breakout watchlist scan and persist to DB."""
    if not force and not is_trading_day(client):
        logger.info("Nightly watchlist scan skipped — not a trading day")
        return
    _set_phase("nightly_scan")
    logger.info("=== NIGHTLY WATCHLIST SCAN START ===")
    if notify:
        notify("NIGHTLY WATCHLIST SCAN STARTED")

    try:
        summary = run_nightly_scan(config, client, db_engine, progress_cb=_set_progress)
    except Exception as e:
        logger.error("Nightly watchlist scan failed: %s", e)
        if notify:
            notify(f"NIGHTLY WATCHLIST SCAN FAILED: {e}")
        _set_phase("idle")
        _set_progress()
        return

    if "error" in summary:
        if notify:
            notify(f"NIGHTLY WATCHLIST SCAN ERROR: {summary['error']}")
        _set_phase("idle")
        _set_progress()
        return

    universe_raw = summary.get('universe_raw', '?')
    momentum_top = summary.get('momentum_top', '?')
    msg = (
        f"NIGHTLY WATCHLIST SCAN DONE\n"
        f"Universe: {universe_raw} → {momentum_top} (momentum top)\n"
        f"Ready: {summary.get('ready', 0)} | Watching: {summary.get('watching', 0)}\n"
        f"New: {summary.get('new', 0)} | Updated: {summary.get('updated', 0)}\n"
        f"Failed: {summary.get('failed', 0)} | Aged out: {summary.get('aged_out', 0)}"
    )
    logger.info(msg)
    if notify:
        notify(msg)
        # Alert if momentum scan returned 0 candidates from a large universe
        mt = summary.get('momentum_top', 0)
        ur = summary.get('universe_raw', 0)
        if isinstance(mt, int) and isinstance(ur, int) and mt == 0 and ur > 100:
            notify(f"WARNING: Nightly scan found 0 momentum candidates from {ur} tickers — possible data fetch issue")
    _set_phase("idle")
    _set_progress()  # clear progress
    return f"Ready: {summary.get('ready', 0)}, Watching: {summary.get('watching', 0)}"


# P&L helpers (_compute_current_daily_pnl, _compute_current_weekly_pnl, _safe_pnl_sum)
# now live in core/execution.py and are imported at the top of this file.


# ---------------------------------------------------------------------------
# Status heartbeat — written every 30s, read by the dashboard
# ---------------------------------------------------------------------------

_current_phase = "idle"
_current_progress: dict = {}  # {"task": "...", "detail": "..."}
_scheduler_ref = None


def _set_phase(phase: str):
    global _current_phase
    _current_phase = phase


def _set_progress(task: str = "", detail: str = ""):
    """Update the current scan progress (shown on dashboard)."""
    global _current_progress
    if task:
        _current_progress = {"task": task, "detail": detail}
    else:
        _current_progress = {}


# ---------------------------------------------------------------------------
# Job execution tracking — persists each job run to the DB for the pipeline UI
# ---------------------------------------------------------------------------

JOB_LABELS = {
    "premarket_scan": "Pre-market Scan",
    "subscribe_watchlist": "Subscribe Watchlist",
    "intraday_monitor": "Intraday Monitor",
    "eod_tasks": "End-of-Day Tasks",
    "breakout_nightly_scan": "Nightly Breakout Scan",
    "ep_earnings_scan": "EP Earnings Scan",
    "ep_earnings_execute": "EP Earnings Execute",
    "ep_news_scan": "EP News Scan",
    "ep_news_execute": "EP News Execute",
}


class _track_job:
    """Context manager that logs a job execution to the JobExecution table.

    Usage:
        with _track_job("premarket_scan") as tracker:
            ... do work ...
            tracker.summary = "8 candidates found"
    """

    def __init__(self, job_id: str, label: str | None = None):
        self.job_id = job_id
        self.label = label or JOB_LABELS.get(job_id, job_id)
        self.summary: str | None = None
        self._row_id: int | None = None

    def __enter__(self):
        if _db_engine is None:
            return self
        now = datetime.now(ET)
        try:
            with get_session(_db_engine) as session:
                row = JobExecution(
                    job_id=self.job_id,
                    job_label=self.label,
                    started_at=now,
                    status="running",
                    trade_date=now.date(),
                )
                session.add(row)
                session.commit()
                self._row_id = row.id
        except Exception as e:
            logger.debug("Failed to insert job_execution row: %s", e)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if _db_engine is None or self._row_id is None:
            return False
        now = datetime.now(ET)
        try:
            with get_session(_db_engine) as session:
                row = session.get(JobExecution, self._row_id)
                if row:
                    row.finished_at = now
                    row.duration_seconds = (now.replace(tzinfo=None) - row.started_at).total_seconds()
                    if exc_type is not None:
                        import traceback as _tb
                        row.status = "failed"
                        row.error = "".join(_tb.format_exception(exc_type, exc_val, exc_tb))[:2000]
                    else:
                        row.status = "success"
                    row.result_summary = (self.summary or "")[:500] or None
                    session.commit()
            # Notify via Telegram on uncaught job failures
            if exc_type is not None:
                from core.alerts import notify_job_failure
                sent = notify_job_failure(self.label, exc_val, _notify_fn, self.job_id)
                if not sent:
                    try:
                        with get_session(_db_engine) as session:
                            row = session.get(JobExecution, self._row_id)
                            if row:
                                row.result_summary = ((row.result_summary or "") + " [notify_failed]")[:500]
                                session.commit()
                    except Exception:
                        logger.error("Also failed to tag job_execution row with [notify_failed]")
        except Exception as e:
            logger.error("Failed to update job_execution row for %s: %s", self.job_id, e)
            # Retry once with a fresh session
            try:
                with get_session(_db_engine) as session:
                    row = session.get(JobExecution, self._row_id)
                    if row and row.status == "running":
                        row.finished_at = now
                        row.duration_seconds = (now.replace(tzinfo=None) - row.started_at).total_seconds()
                        row.status = "failed" if exc_type else "success"
                        row.error = f"Original commit failed: {e}" if exc_type else None
                        row.result_summary = (self.summary or "")[:500] or None
                        session.commit()
                        logger.info("Retry succeeded for job_execution %s", self.job_id)
            except Exception as e2:
                logger.error("Retry also failed for job_execution %s: %s", self.job_id, e2)
        return False  # don't suppress exceptions


def _tracked(job_id: str, fn, *args, **kwargs):
    """Convenience: run *fn* inside a _track_job context and capture its return as summary."""
    with _track_job(job_id) as tracker:
        result = fn(*args, **kwargs)
        if isinstance(result, str):
            tracker.summary = result
        return result


TRIGGER_FILE = Path("trigger_scan")
TRIGGER_NIGHTLY_FILE = Path("trigger_nightly_scan")

# State needed by _check_trigger — set in main() after objects are created
_trigger_args: dict = {}


def _check_trigger():
    """Check for trigger files and run scans if found."""
    args = _trigger_args
    if not args:
        return

    if TRIGGER_FILE.exists():
        TRIGGER_FILE.unlink(missing_ok=True)
        logger.info("Manual scan trigger detected — running premarket scan now")
        import threading
        t = threading.Thread(
            target=job_premarket_scan,
            args=[args["config"], args["client"], args["db_engine"], args["notify"]],
            kwargs={"force": True},
            daemon=True,
        )
        t.start()

    if TRIGGER_NIGHTLY_FILE.exists():
        TRIGGER_NIGHTLY_FILE.unlink(missing_ok=True)
        logger.info("Manual nightly scan trigger detected — running nightly watchlist scan now")
        import threading
        t = threading.Thread(
            target=job_nightly_watchlist_scan,
            args=[args["config"], args["client"], args["db_engine"], args["notify"]],
            kwargs={"force": True},
            daemon=True,
        )
        t.start()


def _write_status():
    """Write bot_status.json so the dashboard can read current state."""
    global _current_phase, _scheduler_ref

    _check_trigger()

    next_job_name = None
    next_job_time = None
    if _scheduler_ref:
        upcoming = sorted(
            [j for j in _scheduler_ref.get_jobs()
             if j.next_run_time and j.id != "heartbeat"],
            key=lambda j: j.next_run_time,
        )
        if upcoming:
            next_job_name = upcoming[0].id
            next_job_time = upcoming[0].next_run_time.isoformat()

    status = {
        "running": True,
        "phase": _current_phase,
        "environment": os.environ.get("BOT_ENV", "paper"),
        "last_heartbeat": datetime.now(ET).isoformat(),
        "next_job": next_job_name,
        "next_job_time": next_job_time,
    }
    if _current_progress:
        status["progress"] = _current_progress
    try:
        fd, tmp_path = tempfile.mkstemp(dir=".", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(status, f)
        os.replace(tmp_path, "bot_status.json")
    except Exception as e:
        logger.warning("Failed to write bot_status.json: %s", e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Startup safety checks
# ---------------------------------------------------------------------------

def job_reconcile_positions(client, db_engine, notify):
    """
    Periodic reconciliation (every 5 min during market hours).

    Detects when GTC stop orders fill at the broker without our knowledge.
    The DB doesn't learn about broker stop fills unless we poll for them.
    """
    from db.models import Position

    if not client.is_market_open():
        return

    with get_session(db_engine) as session:
        open_positions = session.query(Position).filter_by(is_open=True).all()
        if not open_positions:
            return

        for pos in open_positions:
            # Check if the GTC stop order has filled at the broker
            if not pos.stop_order_id:
                continue
            try:
                info = client.get_order_status(pos.stop_order_id)
            except Exception as e:
                logger.warning("Reconcile: failed to check stop order for %s: %s", pos.ticker, e)
                continue

            status = info.get("status", "")
            if status == "filled":
                fill_price = info.get("filled_avg_price", pos.stop_price)
                filled_qty = info.get("filled_qty", 0)
                logger.warning(
                    "RECONCILE: Stop order for %s filled at broker (price=%.2f qty=%d) — closing in DB",
                    pos.ticker, fill_price, filled_qty,
                )

                remaining = pos.shares - pos.partial_exit_shares
                if pos.side == "long":
                    pnl = remaining * (fill_price - pos.entry_price)
                else:
                    pnl = remaining * (pos.entry_price - fill_price)

                # Include partial exit P&L
                if pos.partial_exit_done and pos.partial_exit_price is not None:
                    if pos.side == "long":
                        pnl += pos.partial_exit_shares * (pos.partial_exit_price - pos.entry_price)
                    else:
                        pnl += pos.partial_exit_shares * (pos.entry_price - pos.partial_exit_price)

                pos.exit_price = fill_price
                pos.exit_reason = "stop_hit"
                pos.realized_pnl = pnl
                pos.is_open = False
                pos.closed_at = datetime.utcnow()
                session.commit()

                sign = "+" if pnl >= 0 else ""
                notify(
                    f"STOP FILLED (reconciled): {pos.ticker}\n"
                    f"Exit: ${fill_price:.2f} | P&L: {sign}${pnl:.2f}"
                )
            elif status in ("cancelled", "expired", "rejected"):
                logger.warning(
                    "RECONCILE: Stop order for %s is %s at broker — position may be unprotected",
                    pos.ticker, status,
                )
                pos.stop_order_id = None
                session.commit()
                notify(
                    f"RECONCILE ALERT: Stop for {pos.ticker} is {status} at broker.\n"
                    f"Position may be UNPROTECTED. Check manually."
                )


def _reconcile_on_startup(client, db_engine, notify):
    """
    Run at startup to detect unsafe states left by a previous crash:
      1. Open positions with no stop order → CRITICAL alert
      2. Orders stuck in 'submitted' state → query broker for actual status
      3. JobExecution rows stuck in 'running' → mark as failed
    """
    from db.models import Position, Order, JobExecution

    logger.info("Running startup reconciliation...")

    # Check for unprotected open positions (no broker stop order ID)
    with get_session(db_engine) as session:
        unprotected = (
            session.query(Position)
            .filter(Position.is_open == True, Position.stop_order_id == None)
            .all()
        )
        for pos in unprotected:
            logger.critical(
                "UNPROTECTED POSITION at startup: %s %d shares @ %.2f stop=%.2f",
                pos.ticker, pos.shares, pos.entry_price, pos.stop_price,
            )
            notify(
                f"🚨 STARTUP ALERT — UNPROTECTED POSITION\n"
                f"{pos.ticker}: {pos.shares} shares @ ${pos.entry_price:.2f}\n"
                f"No broker stop order recorded.\n"
                f"Manually place stop at ${pos.stop_price:.2f} NOW."
            )

    # Check for orders stuck in 'submitted' for more than 10 minutes
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        with get_session(db_engine) as session:
            stuck = (
                session.query(Order)
                .filter(Order.status == "submitted", Order.created_at < cutoff)
                .all()
            )
            for order in stuck:
                logger.warning("Stuck order at startup: %s %s broker_id=%s",
                               order.ticker, order.side, order.broker_order_id)
                try:
                    info = client.get_order_status(order.broker_order_id)
                    broker_status = info.get("status", "unknown")
                    logger.info("Broker reports stuck order %s as: %s", order.broker_order_id, broker_status)
                    if broker_status == "filled":
                        notify(
                            f"⚠️ STARTUP: Order for {order.ticker} shows filled on broker "
                            f"but no position recorded.\nCheck account manually."
                        )
                    else:
                        # Update DB status to match broker
                        order.status = broker_status
                        session.commit()
                except Exception as e:
                    logger.error("Could not reconcile stuck order %s: %s", order.broker_order_id, e)
                    notify(f"⚠️ STARTUP: Could not reconcile stuck order for {order.ticker}. Check broker.")
    except Exception as e:
        logger.error("Failed to reconcile stuck orders on startup: %s", e)

    # Clean up jobs stuck in 'running' from a previous crash
    try:
        now = datetime.now(ET)
        with get_session(db_engine) as session:
            stale_jobs = (
                session.query(JobExecution)
                .filter(JobExecution.status == "running")
                .all()
            )
            for job in stale_jobs:
                job.status = "failed"
                job.finished_at = now
                job.duration_seconds = (now.replace(tzinfo=None) - job.started_at).total_seconds() if job.started_at else None
                job.error = "Bot crashed or restarted while job was running"
            if stale_jobs:
                session.commit()
                logger.warning(
                    "Cleaned up %d stale running job(s) from previous crash: %s",
                    len(stale_jobs),
                    [j.job_label for j in stale_jobs],
                )
    except Exception as e:
        logger.error("Failed to clean up stale jobs on startup: %s", e)

    logger.info("Startup reconciliation complete.")


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

def main():
    config_path = os.environ.get("BOT_CONFIG", "config.yaml")
    config = load_config(config_path)

    logger.info("Trading bot starting. Environment: %s", config["environment"])

    # Load strategy plugins
    enabled = config.get("strategies", {}).get("enabled", [])
    plugins = load_strategies(enabled)
    logger.info("Loaded %d strategy plugins: %s", len(plugins), list(plugins.keys()))

    # Database
    db_engine = init_db(config["database"]["url"])

    # Self-heal: drop Watchlist rows for strategies that are no longer enabled.
    # Keeps the dashboard and _watchlist restore honest when a strategy is
    # toggled off in config.yaml.
    try:
        purged = purge_disabled_strategies(enabled, db_engine)
        if purged:
            logger.info("Startup cleanup: purged %d watchlist rows for disabled strategies", purged)
    except Exception as e:
        logger.warning("Startup watchlist purge failed: %s", e)

    # Notifier (constructed first so AlpacaClient can use it for stream alerts)
    notify = make_notifier(config)

    # Broker
    client = AlpacaClient(config, notify)
    client.connect()

    # Startup safety check — alert on any unprotected positions from a previous crash
    _reconcile_on_startup(client, db_engine, notify)

    # Risk manager
    risk = RiskManager(config)

    # Position tracker (with plugin registry for strategy-specific exit hooks)
    tracker = PositionTracker(config, db_engine, client, notify, plugins=plugins)

    # Restore watchlist from DB (active entries survive restarts with full data)
    global _watchlist
    try:
        _watchlist = get_active_watchlist(db_engine, enabled=enabled)
        if _watchlist:
            _set_phase("watchlist_ready")
            logger.info("Restored watchlist from DB: %d active candidates", len(_watchlist))
    except Exception as e:
        logger.warning("Could not restore watchlist from DB: %s", e)

    # Restore phase from bot_status.json if available
    try:
        with open("bot_status.json") as f:
            saved = json.load(f)
        hb = saved.get("last_heartbeat")
        if hb:
            hb_date = datetime.fromisoformat(hb).date()
            if hb_date == datetime.now(ET).date() and saved.get("phase"):
                _set_phase(saved["phase"])
    except Exception:
        pass

    # Set module-level DB engine for _write_status and mark_triggered
    global _db_engine, _notify_fn
    _db_engine = db_engine
    _notify_fn = notify

    # Set trigger args so _check_trigger can run manual scans
    global _trigger_args
    _trigger_args = {"config": config, "client": client, "db_engine": db_engine, "notify": notify}

    # Scheduler
    global _scheduler_ref
    scheduler = BackgroundScheduler(timezone=ET)
    _scheduler_ref = scheduler

    # Register strategy-declared cron jobs (e.g. breakout nightly scan)
    register_strategy_jobs(scheduler, plugins, config, client, db_engine, notify)

    # Only schedule premarket_scan / subscribe_watchlist if a strategy that uses them
    # is enabled. With only EP swing strategies enabled these jobs would no-op,
    # so they'd just add noise to the pipeline. Keep the ownership list in sync
    # with api/constants.py::JOB_OWNERS.
    intraday_owners = {"breakout", "episodic_pivot"}
    needs_intraday_plumbing = bool(set(plugins.keys()) & intraday_owners)

    if needs_intraday_plumbing:
        scheduler.add_job(
            _tracked,
            CronTrigger(hour=6, minute=0, day_of_week="mon-fri", timezone=ET),
            args=["premarket_scan", job_premarket_scan, config, client, db_engine, notify],
            id="premarket_scan",
            replace_existing=True,
        )
        scheduler.add_job(
            _tracked,
            CronTrigger(hour=9, minute=25, day_of_week="mon-fri", timezone=ET),
            args=["subscribe_watchlist", job_subscribe_watchlist, client, config, tracker, risk, db_engine, notify],
            id="subscribe_watchlist",
            replace_existing=True,
        )
    scheduler.add_job(
        _tracked,
        CronTrigger(hour=9, minute=30, day_of_week="mon-fri", timezone=ET),
        args=["intraday_monitor", job_intraday_monitor, config, client, tracker, risk, db_engine, notify],
        id="intraday_monitor",
        replace_existing=True,
    )
    scheduler.add_job(
        _tracked,
        CronTrigger(hour=15, minute=55, day_of_week="mon-fri", timezone=ET),
        args=["eod_tasks", job_eod_tasks, config, client, tracker, db_engine, notify],
        id="eod_tasks",
        replace_existing=True,
    )

    # Reconcile broker positions every 5 min during market hours only
    scheduler.add_job(
        job_reconcile_positions,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=ET),
        args=[client, db_engine, notify],
        id="reconcile_positions",
        replace_existing=True,
    )

    # Heartbeat — writes bot_status.json every 30s for the dashboard
    scheduler.add_job(
        _write_status,
        "interval",
        seconds=30,
        id="heartbeat",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started. Jobs: %s", [j.id for j in scheduler.get_jobs()])
    _write_status()  # write immediately on startup

    # Graceful shutdown
    def shutdown(sig, frame):
        logger.info("Shutdown signal received")
        scheduler.shutdown(wait=False)
        # Mark any currently-running jobs as failed before exiting
        try:
            now = datetime.now(ET)
            with get_session(db_engine) as session:
                running = (
                    session.query(JobExecution)
                    .filter(JobExecution.status == "running")
                    .all()
                )
                for job in running:
                    job.status = "failed"
                    job.finished_at = now
                    job.duration_seconds = (now.replace(tzinfo=None) - job.started_at).total_seconds() if job.started_at else None
                    job.error = "Bot shutdown (signal received)"
                if running:
                    session.commit()
                    logger.info("Marked %d running job(s) as failed on shutdown", len(running))
        except Exception as e:
            logger.error("Failed to clean up running jobs on shutdown: %s", e)
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    notify("Trading bot started. Environment: " + config["environment"])

    # Keep alive
    import time
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
