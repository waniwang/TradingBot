"""
Shared execution logic used by both the Alpaca bot (main.py) and the IBKR bot
(main_ib.py).

These functions were originally defined inline in main.py. They were extracted
here so strategy plugins (e.g. ep_earnings, ep_news) can import them without
depending on which entry point is running.

Public API:
    is_trading_day(client)        — check if today is a US equity trading day
    execute_entry(...)            — place entry order + persist signal/order,
                                     then await fill and place stop in background
    compute_current_daily_pnl(..) — realized P&L for today
    compute_current_weekly_pnl(..)— realized P&L for this week
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timedelta

import pytz

from db.models import get_session
from scanner.watchlist_manager import mark_triggered

logger = logging.getLogger("core.execution")
ET = pytz.timezone("America/New_York")


# ---------------------------------------------------------------------------
# Trading calendar
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
# Snapshot lookups
# ---------------------------------------------------------------------------

def fetch_current_price(client, ticker: str, attempts: int = 3, sleep_secs: float = 2.0) -> float | None:
    """
    Return the current price for a ticker.

    Checks the intraday stream cache first — on_bar populates this throughout
    the trading day, so day2_confirm at 3:35 PM gets a cache hit and never
    touches the Alpaca snapshot REST endpoint (which is congested near close).
    Falls back to REST snapshot with retry only if the cache is cold (e.g. the
    ticker was added to the watchlist after stream subscription).
    """
    from core import data_cache
    cached = data_cache.get_intraday_price(ticker)
    if cached is not None:
        logger.debug("fetch_current_price: %s stream cache $%.2f", ticker, cached)
        return cached

    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            snapshot = client.get_snapshots([ticker])
            snap = snapshot.get(ticker)
            # executor/alpaca_client.py::get_snapshots returns a flat dict per
            # ticker — keys: latest_price, prev_close, prev_high, daily_volume,
            # open, today_high, today_low. Pre-2026-04-25 this branch wrongly
            # checked hasattr for raw-SDK attrs (.latest_trade, .minute_bar) on
            # a plain dict, so it always fell through to None and produced
            # 100% "no price data" failures in EP day-2 confirm. See
            # tests/test_fetch_current_price.py for the regression lock.
            if snap:
                price = snap.get("latest_price") or 0
                if price > 0:
                    return float(price)
                # Last-resort fallback if there was no last_trade today (very
                # illiquid ticker or pre-market): use the daily bar's open as
                # a coarse proxy. Better than returning None and dropping the
                # candidate on a [bot-failure] tag.
                day_open = snap.get("open") or 0
                if day_open > 0:
                    logger.info(
                        "fetch_current_price: %s no latest_price, using day open $%.2f",
                        ticker, day_open,
                    )
                    return float(day_open)
            logger.info("fetch_current_price: %s attempt %d/%d empty", ticker, attempt, attempts)
        except Exception as e:
            last_err = e
            logger.warning("fetch_current_price: %s attempt %d/%d errored: %s",
                           ticker, attempt, attempts, e)
        if attempt < attempts:
            time.sleep(sleep_secs)
    if last_err is not None:
        raise last_err
    return None


# ---------------------------------------------------------------------------
# Order fill tracking
# ---------------------------------------------------------------------------

def _wait_for_fill(client, broker_order_id: str, timeout_secs: int = 60) -> dict | None:
    """
    Poll broker until order fills, hits a terminal state, or times out.

    Returns fill dict on success, None if cancelled/expired/timed-out.
    """
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
            order.status = "partially_filled" if is_partial else "filled"
            order.filled_qty = filled_qty
            order.filled_avg_price = actual_price
            session.commit()

    # Create position record IMMEDIATELY so the position tracker can monitor it
    # (stop_order_id will be updated after stop placement)
    with get_session(db_engine) as session:
        pos = Position(
            ticker=ticker,
            setup_type=signal.setup_type,
            side=signal.side,
            entry_order_id=order_db_id,
            stop_order_id=None,  # will be set after stop placement
            shares=filled_qty,
            entry_price=actual_price,
            stop_price=signal.stop_price,
            initial_stop_price=signal.stop_price,
        )
        session.add(pos)
        session.commit()
        pos_db_id = pos.id

    logger.info(
        "Position opened: %s %s %d @ %.2f stop=%.2f (placing stop order...)",
        signal.side, ticker, filled_qty, actual_price, signal.stop_price,
    )
    notify(
        f"ENTRY FILLED: {ticker} ({signal.setup_type})\n"
        f"Side: {signal.side.upper()} {filled_qty} shares @ ${actual_price:.2f}\n"
        f"Stop: ${signal.stop_price:.2f} (placing broker stop...)\n"
        f"Risk/share: ${signal.risk_per_share:.2f}"
    )

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
            f"CRITICAL — UNPROTECTED POSITION\n"
            f"{ticker}: {filled_qty} shares @ ${actual_price:.2f}\n"
            f"Stop order FAILED after 3 attempts.\n"
            f"Manually place stop at ${signal.stop_price:.2f} NOW."
        )
    else:
        # Update position with the broker stop order ID
        with get_session(db_engine) as session:
            pos = session.query(Position).filter_by(id=pos_db_id).first()
            if pos:
                pos.stop_order_id = broker_stop_id
                session.commit()
        logger.info("Stop order placed for %s: %s", ticker, broker_stop_id)


# ---------------------------------------------------------------------------
# Entry execution
# ---------------------------------------------------------------------------

def execute_entry(ticker, signal, shares, client, db_engine, notify, watchlist_setup_type=None, watchlist_ep_strategy=None):
    """Place the entry limit order, record to DB, then wait for fill in background.

    `watchlist_setup_type` lets callers flip a Watchlist row whose setup_type differs from
    the Signal's (e.g. Strategy A/B/C execute as `ep_earnings_a/b/c` but the Watchlist row
    was persisted as `ep_earnings`). Defaults to `signal.setup_type`.

    `watchlist_ep_strategy` ("A"/"B"/"C") disambiguates when the same ticker+setup_type has
    multiple ready Watchlist rows (A and B both passed for ep_earnings multi-position).
    """
    from db.models import Signal as DbSignal, Order

    order_side = "buy" if signal.side == "long" else "sell_short"
    try:
        broker_order_id = client.place_limit_order(
            ticker, order_side, shares, signal.entry_price
        )
    except Exception as e:
        # Loud failure — don't swallow. Telegram fires via caller's _track_job.
        logger.error("Entry order failed for %s: %s", ticker, e, exc_info=True)
        if notify:
            notify(
                f"ORDER FAILED: {ticker} {order_side} {shares} @ ${signal.entry_price:.2f}\n"
                f"{type(e).__name__}: {e}"
            )
        raise

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

    # Mark watchlist entry as triggered (all setup types)
    if db_engine is not None:
        try:
            mark_triggered(
                ticker, db_engine,
                setup_type=watchlist_setup_type or signal.setup_type,
                ep_strategy=watchlist_ep_strategy,
            )
        except Exception as e:
            # Order succeeded; DB state drift is recoverable but needs operator attention.
            logger.error("Failed to mark %s as triggered in watchlist: %s", ticker, e, exc_info=True)
            if notify:
                notify(
                    f"ORDER PLACED but watchlist state NOT updated for {ticker} "
                    f"({watchlist_setup_type or signal.setup_type}): "
                    f"{type(e).__name__}: {e}\n"
                    f"Row may re-trigger on restart — manual fix required."
                )

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


# Backwards-compatible alias — plugins / main.py import by this name.
_execute_entry = execute_entry


# ---------------------------------------------------------------------------
# Execute-time price refresh (EP strategies)
# ---------------------------------------------------------------------------

def resolve_execution_price(
    ticker: str,
    scan_entry: float,
    stop_pct: float,
    side: str,
    client,
    config: dict,
    notify=None,
) -> tuple[float, float, str] | None:
    """Pick the actual entry + stop for an EP swing order.

    Scanner captures price at 3:00 PM ET, but the execute window is 3:50-3:59 —
    a gap during which the stock often rallies past the scanner mark (today's
    MCRI). This helper fetches a live quote at execute time and picks the entry:

      - If the live mid is <= the scanner entry, use the scanner entry
        (buy-cheaper-if-possible, also strategy-consistent).
      - If the live mid is within `ep_execute_max_price_bump_pct` above the
        scanner entry AND the spread is under `ep_execute_max_spread_pct`, use
        the live mid and recompute the stop at mid * (1 - stop_pct/100).
      - Otherwise return None — the row stays stage="ready" and next minute's
        job_execute retry evaluates again with a fresh quote.

    Stop is always computed from the actual entry used so the -7% (or other)
    stop rule holds.

    Trade-path note: client.get_realtime_quote() exceptions propagate to
    _track_job by design (wrong-size trade is worse than no trade).
    """
    sig_cfg = config.get("signals") or {}
    enabled = bool(sig_cfg.get("ep_execute_refresh_price", True))
    if not enabled:
        return scan_entry, round(scan_entry * (1 - stop_pct / 100), 2), "scan (refresh disabled)"

    max_bump_pct = float(sig_cfg.get("ep_execute_max_price_bump_pct", 3.0))
    max_spread_pct = float(sig_cfg.get("ep_execute_max_spread_pct", 3.0))

    quote = client.get_realtime_quote(ticker)
    bid = float(quote.get("bid") or 0)
    ask = float(quote.get("ask") or 0)

    if bid <= 0 or ask <= 0:
        msg = f"{ticker}: invalid quote (bid={bid} ask={ask}) — skipping this attempt"
        logger.warning(msg)
        if notify:
            notify(f"EP EXECUTE SKIP: {msg}")
        return None

    mid = (bid + ask) / 2
    spread_pct = (ask - bid) / mid * 100

    if spread_pct > max_spread_pct:
        msg = (
            f"{ticker}: spread {spread_pct:.1f}% > cap {max_spread_pct:.1f}% "
            f"(bid=${bid:.2f} ask=${ask:.2f}) — skipping, retry next minute"
        )
        logger.info(msg)
        return None

    # Buy-side only (EP swing is long-only). If side is short in the future,
    # mirror-image the logic (live mid <= scan OK, else cap below scan).
    if side != "long":
        # Fall back to scanner values — don't alter shorts without explicit testing.
        return scan_entry, round(scan_entry * (1 - stop_pct / 100), 2), "scan (non-long side)"

    if mid <= scan_entry:
        # Live price at or below the scan — stay with scan entry (strategy
        # assumes gap-day close; paying even less is fine, stop stays at the
        # same dollar level so risk/share is unchanged).
        return scan_entry, round(scan_entry * (1 - stop_pct / 100), 2), "scan (live <= scan)"

    bump_pct = (mid - scan_entry) / scan_entry * 100
    if bump_pct > max_bump_pct:
        msg = (
            f"{ticker}: live mid ${mid:.2f} is {bump_pct:.1f}% above scan ${scan_entry:.2f} "
            f"(cap {max_bump_pct:.1f}%) — skipping, retry next minute"
        )
        logger.info(msg)
        return None

    refreshed_entry = round(mid, 2)
    refreshed_stop = round(refreshed_entry * (1 - stop_pct / 100), 2)
    logger.info(
        "%s: refreshed entry $%.2f (scan=$%.2f, +%.2f%%) stop=$%.2f spread=%.1f%%",
        ticker, refreshed_entry, scan_entry, bump_pct, refreshed_stop, spread_pct,
    )
    return refreshed_entry, refreshed_stop, f"refreshed (scan=${scan_entry:.2f}, +{bump_pct:.2f}%)"


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
    today = datetime.now(ET).date()
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
    today = datetime.now(ET).date()
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
