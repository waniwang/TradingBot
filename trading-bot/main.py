"""
Main entry point.

APScheduler orchestrates all jobs:
  6:00 AM ET  — pre-market scan (Alpaca screener + snapshots)
  9:25 AM ET  — finalize watchlist, subscribe Alpaca real-time
  9:30 AM ET  — start intraday signal monitor
  3:55 PM ET  — EOD tasks: trailing stop updates, P&L summary, Telegram
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
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from db.models import init_db, get_session
from executor.alpaca_client import AlpacaClient
from monitor.position_tracker import PositionTracker
from risk.manager import RiskManager
from scanner.gapper import get_premarket_gappers
from scanner.momentum_rank import rank_by_momentum
from scanner.consolidation import scan_breakout_candidates
from scanner.parabolic import scan_parabolic_candidates
from scanner.watchlist_manager import (
    run_nightly_scan,
    get_ready_candidates,
    mark_triggered,
    get_pipeline_counts,
)
from signals import evaluate_signal
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
    return cfg


# ---------------------------------------------------------------------------
# Global state (populated during the pre-market phase)
# ---------------------------------------------------------------------------

_watchlist: list[dict] = []          # [{ticker, setup_type, gap_pct, ...}]
_db_engine = None                    # set in main(), used by _write_status
_daily_bars_cache: dict[str, list[dict]] = {}
_daily_closes_cache: dict[str, list[float]] = {}
_daily_volumes_cache: dict[str, list[int]] = {}
_daily_highs_cache: dict[str, list[float]] = {}
_daily_lows_cache: dict[str, list[float]] = {}
_cache_lock = threading.Lock()       # guards all caches above
_entry_locks: dict[str, threading.Lock] = {}
_entry_locks_meta = threading.Lock()  # guards _entry_locks dict itself


def _get_entry_lock(ticker: str) -> threading.Lock:
    """Return a per-ticker lock to prevent concurrent entry evaluation."""
    with _entry_locks_meta:
        if ticker not in _entry_locks:
            _entry_locks[ticker] = threading.Lock()
        return _entry_locks[ticker]


# ---------------------------------------------------------------------------
# Telegram notifier (simple async wrapper)
# ---------------------------------------------------------------------------

def make_notifier(config: dict):
    token = config["telegram"].get("bot_token", "")
    chat_id = config["telegram"].get("chat_id", "")
    if not token or not chat_id:
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
            try:
                await bot.send_message(chat_id=chat_id, text=message)
            except Exception as e:
                logger.warning("Telegram send failed: %s", e)

        asyncio.run_coroutine_threadsafe(_send(), _loop)

    return notify


# ---------------------------------------------------------------------------
# Market calendar helpers
# ---------------------------------------------------------------------------

def is_trading_day(client=None) -> bool:
    """
    Return True if today is a US equity trading day.

    Uses Alpaca's calendar API when a client is available — correctly handles
    weekends, federal holidays, and early-close days (e.g. day after Thanksgiving).
    Falls back to a simple weekday check if no client is passed.
    """
    if client is not None:
        return client.is_trading_day()
    # Fallback: weekday only (no holiday awareness)
    return datetime.now(ET).weekday() < 5


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

    setup_labels = {
        "episodic_pivot": "EP",
        "breakout": "Breakout",
        "parabolic_short": "Parabolic Short",
    }

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
    _set_phase("scanning")
    logger.info("=== PRE-MARKET SCAN START ===")
    if notify:
        notify("PRE-MARKET SCAN STARTED")

    errors = []
    enabled = config.get("strategies", {}).get("enabled", ["episodic_pivot", "breakout", "parabolic_short"])

    # EP gappers
    ep_candidates = []
    if "episodic_pivot" in enabled:
        try:
            ep_candidates = get_premarket_gappers(config, client)
            logger.info("EP candidates: %d", len(ep_candidates))
        except Exception as e:
            logger.error("EP gapper scan failed: %s", e)
            errors.append(f"EP gapper scan failed: {e}")

    # Breakout candidates from persistent watchlist DB (populated by nightly scan)
    breakout_candidates = []
    if "breakout" in enabled:
        try:
            breakout_candidates = get_ready_candidates(db_engine)
            logger.info("Breakout candidates from DB: %d ready", len(breakout_candidates))
        except Exception as e:
            logger.error("Breakout DB read failed: %s", e)
            errors.append(f"Breakout DB read failed: {e}")

    # Parabolic short candidates
    parabolic_candidates = []
    if "parabolic_short" in enabled:
        try:
            parabolic_candidates = scan_parabolic_candidates(config, client)
            logger.info("Parabolic candidates: %d", len(parabolic_candidates))
        except Exception as e:
            logger.error("Parabolic scan failed: %s", e)
            errors.append(f"Parabolic scan failed: {e}")
    else:
        logger.info("Parabolic short disabled in config")

    # Merge watchlist (EP first, then breakout, then parabolic)
    watchlist = ep_candidates + breakout_candidates + parabolic_candidates
    # Deduplicate by ticker (EP takes priority)
    seen = set()
    deduped = []
    for c in watchlist:
        if c["ticker"] not in seen:
            seen.add(c["ticker"])
            deduped.append(c)

    _watchlist = deduped[:20]
    _set_phase("watchlist_ready")
    logger.info("=== PRE-MARKET SCAN DONE: %d candidates ===", len(_watchlist))
    for c in _watchlist:
        logger.info("  %s [%s]", c["ticker"], c["setup_type"])

    # Send Telegram notifications
    if notify:
        notify(_format_watchlist_notification(_watchlist))
        if errors:
            notify("SCAN ERRORS:\n" + "\n".join(errors))


def job_subscribe_watchlist(
    client: AlpacaClient,
    config: dict,
    tracker: PositionTracker,
    risk: RiskManager,
    db_engine,
    notify,
):
    """9:25 AM ET — subscribe to Alpaca real-time bars for watchlist."""
    if not _watchlist:
        logger.info("Watchlist empty — nothing to subscribe")
        return
    tickers = [c["ticker"] for c in _watchlist]
    logger.info("Subscribing to real-time data for %s", tickers)

    def on_bar(bar: dict):
        """Called by AlpacaClient stream for every 1m bar update."""
        ticker = bar["ticker"]
        current_price = bar["close"]
        current_volume_bar = bar["volume"]

        # Fetch recent candles and daily history for signal evaluation
        candles_1m = client.get_candles_1m(ticker, count=30)
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

        # Running today's cumulative volume (approximate from bars)
        today_volume = sum(c["volume"] for c in candles_1m)

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
        )

    client.subscribe_quotes(tickers, callback=on_bar)
    _set_phase("observing")


def job_intraday_monitor(
    config: dict,
    client: AlpacaClient,
    tracker: PositionTracker,
    risk: RiskManager,
    db_engine,
    notify,
):
    """9:30 AM ET — log confirmation that the stream is running."""
    logger.info("=== INTRADAY MONITOR STARTED — stream active ===")
    # Data processing is driven by the Alpaca WebSocket stream callback
    # registered in job_subscribe_watchlist at 9:25 AM.


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
):
    """
    Called for each ticker on every 1m candle update (via Alpaca stream callback).
    Checks signals for watchlist items and manages open positions.
    """
    # Use pre-fetched data when available (from stream callback)
    if candles_1m is None or current_price is None:
        try:
            candles_1m = client.get_candles_1m(ticker, count=30)
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
        )


def _evaluate_and_enter(
    ticker, watchlist_entry, candles_1m, daily_closes, daily_volumes,
    daily_highs, daily_lows,
    current_price, current_volume, config, client, tracker, risk, db_engine, notify,
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

    # Check if strategy is enabled
    setup = watchlist_entry["setup_type"]
    enabled = config.get("strategies", {}).get("enabled", ["episodic_pivot", "breakout", "parabolic_short"])
    if setup not in enabled:
        logger.debug("Skipping %s for %s — strategy disabled", setup, ticker)
        return

    # Evaluate signal via strategy registry
    sig = evaluate_signal(
        setup,
        ticker,
        candles_1m=candles_1m,
        daily_closes=daily_closes,
        daily_volumes=daily_volumes,
        daily_highs=daily_highs,
        daily_lows=daily_lows,
        current_price=current_price,
        current_volume=current_volume,
        gap_pct=watchlist_entry.get("gap_pct", 0.0),
        config=config,
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


def _wait_for_fill(client, broker_order_id: str, timeout_secs: int = 60) -> dict | None:
    """
    Poll broker until order fills, hits a terminal state, or times out.

    Returns fill dict on success, None if cancelled/expired/timed-out.
    """
    import time
    TERMINAL = {"filled", "cancelled", "expired", "rejected", "replaced", "done_for_day"}
    deadline = time.time() + timeout_secs
    last_info = None
    while time.time() < deadline:
        try:
            info = client.get_order_status(broker_order_id)
            status = info.get("status", "")
            if status == "filled":
                return info
            if status == "partially_filled":
                logger.info("Order %s partially filled (%s shares)", broker_order_id, info.get("filled_qty"))
                last_info = info
            if status in TERMINAL:
                logger.info("Order %s ended with status: %s", broker_order_id, status)
                return None
        except Exception as e:
            logger.warning("Error polling order %s: %s", broker_order_id, e)
        time.sleep(2)

    # On timeout, accept partial fill if any shares were filled
    if last_info and last_info.get("filled_qty", 0) > 0:
        logger.info("Order %s timed out but has partial fill of %s shares — accepting",
                     broker_order_id, last_info["filled_qty"])
        # Cancel the remaining unfilled quantity to prevent orphaned fills
        try:
            client.cancel_order(broker_order_id)
            logger.info("Cancelled remainder of partially filled order %s", broker_order_id)
        except Exception as e:
            logger.warning("Failed to cancel remainder of order %s: %s — "
                           "orphaned qty may fill later", broker_order_id, e)
        return last_info

    logger.info("Order %s did not fill within %ds", broker_order_id, timeout_secs)
    return None


def _await_fill_and_setup_stop(
    ticker, signal, shares, broker_order_id, order_db_id, client, db_engine, notify
):
    """
    Runs in a background thread after an entry order is submitted.

    Waits for fill confirmation, then:
      - Updates the Order record with actual fill price
      - Creates the Position record
      - Places a GTC stop order with the broker
    """
    from db.models import Order, Position

    fill = _wait_for_fill(client, broker_order_id, timeout_secs=60)

    if fill is None:
        # Order didn't fill — cancel it and clean up
        logger.info("Entry order for %s did not fill within timeout — cancelling", ticker)
        try:
            client.cancel_order(broker_order_id)
        except Exception as e:
            logger.warning("Failed to cancel unfilled order %s: %s", broker_order_id, e)
        with get_session(db_engine) as session:
            order = session.query(Order).filter_by(id=order_db_id).first()
            if order:
                order.status = "cancelled"
                session.commit()
        notify(f"ENTRY NOT FILLED: {ticker} order cancelled (timed out)")
        return

    # Order filled — extract actual fill details
    actual_price = fill.get("filled_avg_price") or signal.entry_price
    filled_qty = fill.get("filled_qty")
    if filled_qty is None:
        filled_qty = shares
    is_partial = filled_qty < shares

    # Update order record
    with get_session(db_engine) as session:
        order = session.query(Order).filter_by(id=order_db_id).first()
        if order:
            order.status = "partial_fill" if is_partial else "filled"
            order.filled_qty = filled_qty
            order.filled_avg_price = actual_price
            session.commit()

    # Place GTC stop order with broker — retry up to 3 times
    stop_side = "sell" if signal.side == "long" else "buy_to_cover"
    broker_stop_id = None
    for attempt in range(1, 4):
        try:
            broker_stop_id = client.place_stop_order(
                ticker, stop_side, filled_qty, signal.stop_price
            )
            break
        except Exception as e:
            logger.error("Stop order attempt %d/3 failed for %s: %s", attempt, ticker, e)
            if attempt < 3:
                time.sleep(2 ** attempt)  # 2s, 4s backoff
    if broker_stop_id is None:
        logger.critical("UNPROTECTED POSITION: %s %d shares @ %.2f — stop order failed",
                        ticker, filled_qty, actual_price)
        notify(
            f"🚨 CRITICAL — UNPROTECTED POSITION\n"
            f"{ticker}: {filled_qty} shares @ ${actual_price:.2f}\n"
            f"Stop order FAILED after 3 attempts.\n"
            f"Manually place stop at ${signal.stop_price:.2f} NOW."
        )

    # Create position record
    with get_session(db_engine) as session:
        pos = Position(
            ticker=ticker,
            setup_type=signal.setup_type,
            side=signal.side,
            entry_order_id=order_db_id,
            stop_order_id=broker_stop_id,
            shares=filled_qty,
            entry_price=actual_price,
            stop_price=signal.stop_price,
            initial_stop_price=signal.stop_price,
        )
        session.add(pos)
        session.commit()

    logger.info(
        "Position opened: %s %s %d @ %.2f stop=%.2f broker_stop=%s",
        signal.side, ticker, filled_qty, actual_price, signal.stop_price, broker_stop_id,
    )
    notify(
        f"ENTRY FILLED: {ticker} ({signal.setup_type})\n"
        f"Side: {signal.side.upper()} {filled_qty} shares @ ${actual_price:.2f}\n"
        f"Stop: ${signal.stop_price:.2f} (order id: {broker_stop_id or 'none'})\n"
        f"Risk/share: ${signal.risk_per_share:.2f}"
    )


def _execute_entry(ticker, signal, shares, client, db_engine, notify):
    """Place the entry limit order, record to DB, then wait for fill in background."""
    import threading
    from db.models import Signal as DbSignal, Order

    try:
        order_side = "buy" if signal.side == "long" else "sell_short"
        broker_order_id = client.place_limit_order(
            ticker, order_side, shares, signal.entry_price
        )
    except Exception as e:
        logger.error("Entry order failed for %s: %s", ticker, e)
        return

    # Persist signal + order immediately (position created after fill confirmation)
    with get_session(db_engine) as session:
        db_signal = DbSignal(
            ticker=ticker,
            setup_type=signal.setup_type,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            gap_pct=signal.gap_pct,
            orh=signal.orh,
            orb_low=signal.orb_low,
            acted_on=True,
        )
        session.add(db_signal)
        session.flush()

        db_order = Order(
            signal_id=db_signal.id,
            broker_order_id=broker_order_id,
            ticker=ticker,
            side=order_side,
            order_type="limit",
            qty=shares,
            price=signal.entry_price,
            status="submitted",
        )
        session.add(db_order)
        session.commit()
        order_db_id = db_order.id

    # Mark breakout watchlist entry as triggered
    if signal.setup_type == "breakout" and _db_engine is not None:
        try:
            mark_triggered(ticker, _db_engine)
        except Exception as e:
            logger.warning("Failed to mark %s as triggered in watchlist: %s", ticker, e)

    notify(
        f"ENTRY ORDER PLACED: {ticker} ({signal.setup_type})\n"
        f"Side: {signal.side.upper()} {shares} shares @ ${signal.entry_price:.2f}\n"
        f"Stop: ${signal.stop_price:.2f} | Waiting for fill..."
    )
    logger.info(
        "Entry order placed: %s %s %d @ %.2f stop=%.2f broker_id=%s",
        order_side, ticker, shares, signal.entry_price, signal.stop_price, broker_order_id,
    )

    # Wait for fill and place stop in background (doesn't block the stream callback)
    t = threading.Thread(
        target=_await_fill_and_setup_stop,
        args=(ticker, signal, shares, broker_order_id, order_db_id, client, db_engine, notify),
        daemon=True,
    )
    t.start()


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

    # Pass a snapshot of the cache to avoid race with the stream callback thread
    with _cache_lock:
        closes_snapshot = {k: list(v) for k, v in _daily_closes_cache.items()}
    tracker.run_eod_tasks(closes_snapshot)

    # Fetch current prices for unrealized P&L calculation
    current_prices = {}
    try:
        broker_positions = client.get_open_positions()
        for bp in broker_positions:
            if bp.get("current_price", 0) > 0:
                current_prices[bp["symbol"]] = bp["current_price"]
    except Exception as e:
        logger.warning("Could not fetch broker positions for P&L: %s", e)

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


def job_nightly_watchlist_scan(config: dict, client: AlpacaClient, db_engine, notify):
    """5:00 PM ET — run heavy breakout watchlist scan and persist to DB."""
    if not is_trading_day(client):
        logger.info("Nightly watchlist scan skipped — not a trading day")
        return
    logger.info("=== NIGHTLY WATCHLIST SCAN START ===")
    if notify:
        notify("NIGHTLY WATCHLIST SCAN STARTED")

    try:
        summary = run_nightly_scan(config, client, db_engine)
    except Exception as e:
        logger.error("Nightly watchlist scan failed: %s", e)
        if notify:
            notify(f"NIGHTLY WATCHLIST SCAN FAILED: {e}")
        return

    if "error" in summary:
        if notify:
            notify(f"NIGHTLY WATCHLIST SCAN ERROR: {summary['error']}")
        return

    msg = (
        f"NIGHTLY WATCHLIST SCAN DONE\n"
        f"Ready: {summary.get('ready', 0)} | Watching: {summary.get('watching', 0)}\n"
        f"New: {summary.get('new', 0)} | Updated: {summary.get('updated', 0)}\n"
        f"Failed: {summary.get('failed', 0)} | Aged out: {summary.get('aged_out', 0)}"
    )
    logger.info(msg)
    if notify:
        notify(msg)


# ---------------------------------------------------------------------------
# P&L helpers
# ---------------------------------------------------------------------------

def _safe_pnl_sum(positions) -> float:
    """Sum realized_pnl, skipping any NaN/None values with a warning."""
    total = 0.0
    for p in positions:
        val = p.realized_pnl
        if val is None:
            continue
        if math.isnan(val):
            logger.error("NaN realized_pnl on position id=%s ticker=%s — excluded from P&L", p.id, p.ticker)
            continue
        total += val
    return total


def _compute_current_daily_pnl(db_engine) -> float:
    from db.models import Position
    today = datetime.utcnow().date()
    with get_session(db_engine) as session:
        closed = (
            session.query(Position)
            .filter(
                Position.is_open == False,
                Position.closed_at >= datetime.combine(today, datetime.min.time()),
            )
            .all()
        )
    return _safe_pnl_sum(closed)


def _compute_current_weekly_pnl(db_engine) -> float:
    from db.models import Position
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    with get_session(db_engine) as session:
        closed = (
            session.query(Position)
            .filter(
                Position.is_open == False,
                Position.closed_at >= datetime.combine(week_start, datetime.min.time()),
            )
            .all()
        )
    return _safe_pnl_sum(closed)


# ---------------------------------------------------------------------------
# Status heartbeat — written every 30s, read by the dashboard
# ---------------------------------------------------------------------------

_current_phase = "idle"
_scheduler_ref = None


def _set_phase(phase: str):
    global _current_phase
    _current_phase = phase


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

    # Include breakout pipeline counts if DB engine is available
    pipeline = {}
    if _db_engine is not None:
        try:
            pipeline = get_pipeline_counts(_db_engine)
        except Exception:
            pass

    status = {
        "running": True,
        "phase": _current_phase,
        "environment": os.environ.get("BOT_ENV", "paper"),
        "last_heartbeat": datetime.now(ET).isoformat(),
        "next_job": next_job_name,
        "next_job_time": next_job_time,
        "watchlist": _watchlist,
        "breakout_pipeline": pipeline,
    }
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

def _reconcile_on_startup(client, db_engine, notify):
    """
    Run at startup to detect unsafe states left by a previous crash:
      1. Open positions with no stop order → CRITICAL alert
      2. Orders stuck in 'submitted' state → query broker for actual status
    """
    from db.models import Position, Order

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
    cutoff = datetime.utcnow() - timedelta(minutes=10)
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

    logger.info("Startup reconciliation complete.")


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

def main():
    config_path = os.environ.get("BOT_CONFIG", "config.yaml")
    config = load_config(config_path)

    logger.info("Trading bot starting. Environment: %s", config["environment"])

    # Database
    db_engine = init_db(config["database"]["url"])

    # Notifier (constructed first so AlpacaClient can use it for stream alerts)
    notify = make_notifier(config)

    # Broker
    client = AlpacaClient(config, notify)
    client.connect()

    # Startup safety check — alert on any unprotected positions from a previous crash
    _reconcile_on_startup(client, db_engine, notify)

    # Risk manager
    risk = RiskManager(config)

    # Position tracker
    tracker = PositionTracker(config, db_engine, client, notify)

    # Restore watchlist from bot_status.json if it was written today
    global _watchlist
    try:
        with open("bot_status.json") as f:
            saved = json.load(f)
        saved_wl = saved.get("watchlist", [])
        hb = saved.get("last_heartbeat")
        if saved_wl and hb:
            hb_date = datetime.fromisoformat(hb).date()
            if hb_date == datetime.now(ET).date():
                _watchlist = saved_wl
                _set_phase(saved.get("phase", "watchlist_ready"))
                logger.info("Restored watchlist from bot_status.json: %d candidates",
                            len(_watchlist))
    except Exception as e:
        logger.warning("Could not restore watchlist: %s", e)

    # Set module-level DB engine for _write_status and mark_triggered
    global _db_engine
    _db_engine = db_engine

    # Set trigger args so _check_trigger can run manual scans
    global _trigger_args
    _trigger_args = {"config": config, "client": client, "db_engine": db_engine, "notify": notify}

    # Scheduler
    global _scheduler_ref
    scheduler = BackgroundScheduler(timezone=ET)
    _scheduler_ref = scheduler

    scheduler.add_job(
        job_nightly_watchlist_scan,
        CronTrigger(hour=17, minute=0, timezone=ET),
        args=[config, client, db_engine, notify],
        id="nightly_watchlist_scan",
        replace_existing=True,
    )
    scheduler.add_job(
        job_premarket_scan,
        CronTrigger(hour=6, minute=0, timezone=ET),
        args=[config, client, db_engine, notify],
        id="premarket_scan",
        replace_existing=True,
    )
    scheduler.add_job(
        job_subscribe_watchlist,
        CronTrigger(hour=9, minute=25, timezone=ET),
        args=[client, config, tracker, risk, db_engine, notify],
        id="subscribe_watchlist",
        replace_existing=True,
    )
    scheduler.add_job(
        job_intraday_monitor,
        CronTrigger(hour=9, minute=30, timezone=ET),
        args=[config, client, tracker, risk, db_engine, notify],
        id="intraday_monitor",
        replace_existing=True,
    )
    scheduler.add_job(
        job_eod_tasks,
        CronTrigger(hour=15, minute=55, timezone=ET),
        args=[config, client, tracker, db_engine, notify],
        id="eod_tasks",
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
