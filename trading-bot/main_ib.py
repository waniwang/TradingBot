"""
IBKR paper trading entry point — runs ONLY the EP earnings + EP news swing
strategies against an Interactive Brokers paper account.

This is a separate process from the main Alpaca bot (main.py). It uses a
separate database (trading_bot_ib.db), separate heartbeat file
(bot_status_ib.json), and connects to IB Gateway instead of Alpaca. Code,
strategies, and the API are shared with the Alpaca bot.

Architecture: passive executor.
  The IB bot does NOT scan. It reads the Alpaca bot's already-vetted
  Watchlist from trading_bot.db and only places orders on IBKR paper.
  Every scanner/strategy fix shipped to the Alpaca code automatically
  applies to IB execution. Idempotency is enforced via the IB bot's local
  Order/Position tables in trading_bot_ib.db.

Prereqs:
  1. IB Gateway running + logged into PAPER account on localhost:4002
  2. A personal config file (e.g. config.ib.local.yaml — gitignored) with:
       - `ibkr:` section (host, port, client_id)
       - `database_ib.url` pointing at your IB DB (e.g. sqlite:///trading_bot_ib.db)
       - `watchlist_source_db_url` pointing at the Alpaca DB
         (e.g. sqlite:////opt/trading-bot/trading-bot/trading_bot.db) — the
         IB bot reads ready/triggered Watchlist rows from this DB instead of
         scanning. Without this key the IB bot falls back to local-DB reads.
       - Any per-instance risk / share-size tweaks you want
  3. Launch via bot_ib.sh, which sets BOT_CONFIG=config.ib.local.yaml

Scheduled jobs (ET timezone):
  Scan + day-2-confirm jobs run only in the Alpaca bot — the IB bot skips
  them via the scheduler's skip_jobs param. The IB bot only runs:
    3:37–3:59 PM — EP earnings execute (limit orders, retry per minute)
    3:37–3:59 PM — EP news execute (limit orders, retry per minute)
    3:55 PM      — EOD tasks: trailing stops, max hold exits, P&L
    every 5 min  — reconcile broker stops during market hours
    every 60s    — IB Gateway watchdog (reconnect if dropped)
    every 30s    — heartbeat status write

Usage:
  BOT_CONFIG=config.ib.local.yaml .venv/bin/python main_ib.py
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytz
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.loader import load_strategies
from core.scheduler import register_strategy_jobs
from core.execution import is_trading_day
from db.models import init_db, get_session, JobExecution
from executor.ib_client import IBClient
from monitor.position_tracker import PositionTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("trading_bot_ib.log"),
    ],
)
logger = logging.getLogger("main_ib")

ET = pytz.timezone("America/New_York")

# Only these strategies run on IBKR
IB_ENABLED_STRATEGIES = ["ep_earnings", "ep_news"]

# Module-level state
_db_engine = None
_notify_fn = None
_scheduler_ref: BackgroundScheduler | None = None
_current_phase = "idle"


# ---------------------------------------------------------------------------
# Config loading (mirrors main.py but reads ibkr + database_ib sections)
# ---------------------------------------------------------------------------

def load_config(path: str | None = None) -> dict:
    # Default to BOT_CONFIG env var, falling back to config.yaml in cwd.
    # bot_ib.sh sets BOT_CONFIG=config.ib.local.yaml so you land on your
    # personal config with the ibkr:/database: overrides.
    if path is None:
        path = os.environ.get("BOT_CONFIG", "config.yaml")
    with open(path) as f:
        cfg = yaml.safe_load(f)

    # Env overrides
    if os.environ.get("IBKR_HOST"):
        cfg.setdefault("ibkr", {})["host"] = os.environ["IBKR_HOST"]
    if os.environ.get("IBKR_PORT"):
        cfg.setdefault("ibkr", {})["port"] = int(os.environ["IBKR_PORT"])
    if os.environ.get("IBKR_CLIENT_ID"):
        cfg.setdefault("ibkr", {})["client_id"] = int(os.environ["IBKR_CLIENT_ID"])
    if os.environ.get("DATABASE_IB_URL"):
        cfg.setdefault("database_ib", {})["url"] = os.environ["DATABASE_IB_URL"]
    if os.environ.get("WATCHLIST_SOURCE_DB_URL"):
        cfg["watchlist_source_db_url"] = os.environ["WATCHLIST_SOURCE_DB_URL"]
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg.setdefault("telegram", {})["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg.setdefault("telegram", {})["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]

    # Merge per-strategy configs (same as main.py)
    strategies_dir = Path(__file__).resolve().parent / "strategies"
    cfg.setdefault("strategies", {})
    if strategies_dir.is_dir():
        for d in strategies_dir.iterdir():
            if d.is_dir() and (d / "config.yaml").is_file():
                try:
                    with open(d / "config.yaml") as sf:
                        strategy_cfg = yaml.safe_load(sf) or {}
                    cfg["strategies"].setdefault(d.name, {}).update(strategy_cfg)
                except Exception as e:
                    logger.warning("Failed to load strategy config %s: %s", d, e)

    # Override the enabled-strategies list — this process ONLY runs EP*
    cfg.setdefault("strategies", {})["enabled"] = IB_ENABLED_STRATEGIES

    # Point database config at the IB DB
    if "database_ib" in cfg:
        cfg["database"] = cfg["database_ib"]

    return cfg


# ---------------------------------------------------------------------------
# Telegram notifier (copied from main.py — same logic, different log tag)
# ---------------------------------------------------------------------------

def make_notifier(config: dict):
    tg = config.get("telegram") or {}
    token = tg.get("bot_token", "")
    chat_id_raw = str(tg.get("chat_id", "") or "")
    chat_ids = [c.strip() for c in chat_id_raw.split(",") if c.strip()]
    if not token or not chat_ids:
        return lambda msg: logger.info("[Telegram stub] %s", msg)

    import asyncio
    import threading
    from telegram import Bot

    bot = Bot(token=token)
    _loop = asyncio.new_event_loop()
    _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True, name="ib-telegram-loop")
    _loop_thread.start()

    def notify(message: str):
        # Prefix so you can tell IB messages apart from Alpaca in Telegram
        tagged = f"[IB] {message}"

        async def _send():
            for cid in chat_ids:
                try:
                    await bot.send_message(chat_id=cid, text=tagged)
                except Exception as e:
                    logger.warning("Telegram send failed (chat_id=%s): %s", cid, e)

        asyncio.run_coroutine_threadsafe(_send(), _loop)

    return notify


# ---------------------------------------------------------------------------
# Heartbeat + status
# ---------------------------------------------------------------------------

STATUS_FILE = "bot_status_ib.json"


def _set_phase(phase: str):
    global _current_phase
    _current_phase = phase


def _write_status():
    """Write bot_status_ib.json so the IB dashboard can read current state."""
    next_job_name = None
    next_job_time = None
    if _scheduler_ref:
        upcoming = sorted(
            [j for j in _scheduler_ref.get_jobs()
             if j.next_run_time and j.id not in ("heartbeat",)],
            key=lambda j: j.next_run_time,
        )
        if upcoming:
            next_job_name = upcoming[0].id
            next_job_time = upcoming[0].next_run_time.isoformat()

    status = {
        "running": True,
        "broker": "ibkr",
        "phase": _current_phase,
        "environment": "paper",
        "last_heartbeat": datetime.now(ET).isoformat(),
        "next_job": next_job_name,
        "next_job_time": next_job_time,
    }
    try:
        fd, tmp_path = tempfile.mkstemp(dir=".", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(status, f)
        os.replace(tmp_path, STATUS_FILE)
    except Exception as e:
        logger.warning("Failed to write %s: %s", STATUS_FILE, e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# EOD job — subset of main.py's job_eod_tasks for swing strategies
# ---------------------------------------------------------------------------

def job_eod_tasks_ib(config, client, tracker, db_engine, notify):
    """
    3:55 PM ET — EOD trailing stop updates + P&L summary.

    For EP swing strategies, this is the critical job: it updates trailing
    MA-close stops and triggers max-hold exits. Executes after the 3:50 PM
    entry window closes.
    """
    if not is_trading_day(client):
        logger.info("EOD tasks skipped — not a trading day")
        return None
    _set_phase("end_of_day")
    logger.info("=== EOD TASKS START (IB) ===")

    # Fetch current prices from broker for today's-close proxy + P&L
    current_prices = {}
    try:
        broker_positions = client.get_open_positions()
        for bp in broker_positions:
            if bp.get("current_price", 0) > 0:
                current_prices[bp["symbol"]] = bp["current_price"]
    except Exception as e:
        logger.warning("Could not fetch broker positions for EOD: %s", e)

    # For swing strategies we don't maintain an intraday daily_closes_cache like
    # the Alpaca bot does. Passing an empty map means trailing MA checks skip
    # until they can fetch fresh daily bars inside the tracker.
    tracker.run_eod_tasks({sym: [price] for sym, price in current_prices.items()})

    # Compute daily P&L
    portfolio_value = client.get_portfolio_value()
    daily = tracker.compute_daily_pnl(portfolio_value, current_prices=current_prices)

    sign = "+" if daily.total_pnl >= 0 else ""
    summary = (
        f"EOD SUMMARY (IB)\n"
        f"Date: {daily.trade_date}\n"
        f"P&L: {sign}${daily.total_pnl:.2f}\n"
        f"Realized: ${daily.realized_pnl:.2f}\n"
        f"Trades: {daily.num_trades} ({daily.num_winners}W / {daily.num_losers}L)\n"
        f"Portfolio: ${portfolio_value:,.0f}"
    )
    notify(summary)
    logger.info(summary)

    tracker.set_daily_halt(False)
    from zoneinfo import ZoneInfo
    if datetime.now(ZoneInfo("America/New_York")).weekday() == 4:
        tracker.set_weekly_halt(False)
    _set_phase("idle")
    return f"P&L: {sign}${daily.total_pnl:.2f}, {daily.num_trades} trades"


def job_reconcile_positions_ib(client, db_engine, notify):
    """
    Every 5 min during market hours — detects when broker stop orders fill
    without our knowledge. Same logic as main.py's job_reconcile_positions
    (duplicated rather than imported to avoid pulling main.py's globals).
    """
    from db.models import Position

    if not client.is_market_open():
        return

    with get_session(db_engine) as session:
        open_positions = session.query(Position).filter_by(is_open=True).all()
        if not open_positions:
            return

        for pos in open_positions:
            if not pos.stop_order_id:
                continue
            try:
                info = client.get_order_status(pos.stop_order_id)
            except Exception as e:
                logger.warning("Reconcile: failed to check stop for %s: %s", pos.ticker, e)
                continue

            status = info.get("status", "")
            if status == "filled":
                fill_price = info.get("filled_avg_price", pos.stop_price)
                remaining = pos.shares - pos.partial_exit_shares
                if pos.side == "long":
                    pnl = remaining * (fill_price - pos.entry_price)
                else:
                    pnl = remaining * (pos.entry_price - fill_price)
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
                notify(f"STOP FILLED (reconciled): {pos.ticker}\n"
                       f"Exit: ${fill_price:.2f} | P&L: {sign}${pnl:.2f}")
            elif status in ("cancelled", "expired", "rejected"):
                logger.warning("RECONCILE: Stop for %s is %s — position may be unprotected",
                               pos.ticker, status)
                pos.stop_order_id = None
                session.commit()
                notify(f"RECONCILE ALERT: Stop for {pos.ticker} is {status}.\n"
                       f"Position may be UNPROTECTED. Check manually.")


def _reconcile_on_startup(client, db_engine, notify):
    """Basic startup check — alert on any unprotected open positions."""
    from db.models import Position
    try:
        with get_session(db_engine) as session:
            unprotected = (
                session.query(Position)
                .filter(Position.is_open == True, Position.stop_order_id == None)
                .all()
            )
        if unprotected:
            msg = f"STARTUP WARNING: {len(unprotected)} open position(s) without stop orders: " + \
                  ", ".join(p.ticker for p in unprotected)
            logger.warning(msg)
            notify(msg)
    except Exception as e:
        logger.warning("Startup reconcile check failed: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    config_path = os.environ.get("BOT_CONFIG")  # None → load_config uses shared default
    config = load_config(config_path)

    logger.info("IB trading bot starting. Environment: paper, Strategies: %s",
                IB_ENABLED_STRATEGIES)

    # Plugins (forced to ep_earnings + ep_news by load_config)
    plugins = load_strategies(config["strategies"]["enabled"])
    logger.info("Loaded %d strategy plugins: %s", len(plugins), list(plugins.keys()))

    # DB (separate from Alpaca bot)
    db_url = config.get("database_ib", {}).get("url") or config["database"]["url"]
    db_engine = init_db(db_url)
    logger.info("DB initialized: %s", db_url)

    # Notifier
    notify = make_notifier(config)

    # IB broker
    client = IBClient(config, notify)
    client.connect()

    # Startup safety check
    _reconcile_on_startup(client, db_engine, notify)

    # Position tracker (EP plugins use it for swing exits)
    tracker = PositionTracker(config, db_engine, client, notify, plugins=plugins)

    # Module globals
    global _db_engine, _notify_fn, _scheduler_ref
    _db_engine = db_engine
    _notify_fn = notify

    # Scheduler
    scheduler = BackgroundScheduler(timezone=ET)
    _scheduler_ref = scheduler

    # Register cron jobs declared by the EP plugins themselves.
    # The IB bot is a passive executor — it does NOT run scan or day-2-confirm
    # jobs. Those run on the Alpaca bot, which writes ready Watchlist rows
    # that the IB bot reads via config["watchlist_source_db_url"].
    ib_skip_jobs = (
        "ep_earnings_scan", "ep_earnings_day2_confirm",
        "ep_news_scan", "ep_news_day2_confirm",
    )
    if not config.get("watchlist_source_db_url"):
        logger.warning(
            "watchlist_source_db_url not set — IB bot will read from its own "
            "(empty) DB and never execute. Set this in config.ib.local.yaml "
            "to point at the Alpaca DB."
        )
    register_strategy_jobs(
        scheduler, plugins, config, client, db_engine, notify,
        skip_jobs=ib_skip_jobs,
    )

    # EOD tasks at 3:55 PM ET (swing exits + P&L summary)
    scheduler.add_job(
        job_eod_tasks_ib,
        CronTrigger(hour=15, minute=55, day_of_week="mon-fri", timezone=ET),
        args=[config, client, tracker, db_engine, notify],
        id="eod_tasks",
        replace_existing=True,
    )

    # Reconcile broker stops every 5 min during market hours
    scheduler.add_job(
        job_reconcile_positions_ib,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=ET),
        args=[client, db_engine, notify],
        id="reconcile_positions",
        replace_existing=True,
    )

    # Heartbeat — every 30s
    scheduler.add_job(_write_status, "interval", seconds=30,
                      id="heartbeat", replace_existing=True)

    # IB Gateway disconnect watchdog — every 60s, reconnect if link dropped
    def _ib_watchdog():
        if not client.is_connected():
            logger.warning("IB Gateway disconnected — attempting reconnect")
            try:
                client.connect()
                notify("IB Gateway reconnected")
            except Exception as e:
                logger.error("IB reconnect failed: %s", e)

    scheduler.add_job(_ib_watchdog, "interval", seconds=60,
                      id="ib_watchdog", replace_existing=True)

    scheduler.start()
    logger.info("Scheduler started. Jobs: %s", [j.id for j in scheduler.get_jobs()])
    _write_status()

    def shutdown(sig, frame):
        logger.info("Shutdown signal received")
        scheduler.shutdown(wait=False)
        try:
            now = datetime.now(ET)
            with get_session(db_engine) as session:
                running = session.query(JobExecution).filter(JobExecution.status == "running").all()
                for job in running:
                    job.status = "failed"
                    job.finished_at = now
                    job.error = "Bot shutdown (signal received)"
                if running:
                    session.commit()
        except Exception as e:
            logger.error("Shutdown cleanup failed: %s", e)
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    notify("IB trading bot started (paper account, EP earnings + EP news)")

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
