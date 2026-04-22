"""
Daily verification script for the trading bot.

Runs automated checks against DB, Alpaca broker, logs, and yfinance data
to verify yesterday's (or a specified date's) execution was correct.

Usage:
    cd trading-bot && .venv/bin/python verify_day.py              # last trading day
    cd trading-bot && .venv/bin/python verify_day.py 2026-02-27   # specific date
"""

from __future__ import annotations

import math
import os
import re
import sys
from datetime import date, datetime, timedelta

import pytz
import yaml

from db.models import (
    DailyPnl, Order, Position, Signal, Watchlist, get_engine, get_session,
)
from executor.alpaca_client import AlpacaClient

ET = pytz.timezone("America/New_York")
LOG_FILE = "trading_bot.log"

# ── Formatting helpers ──────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _pass(msg: str) -> str:
    return f"  {GREEN}PASS{RESET}  {msg}"


def _fail(msg: str) -> str:
    return f"  {RED}FAIL{RESET}  {msg}"


def _warn(msg: str) -> str:
    return f"  {YELLOW}WARN{RESET}  {msg}"


def _skip(msg: str) -> str:
    return f"  {YELLOW}SKIP{RESET}  {msg}"


def _header(title: str) -> str:
    return f"\n{BOLD}{'═' * 70}\n {title}\n{'═' * 70}{RESET}"


def _section(title: str) -> str:
    return f"\n{BOLD}── {title} ──{RESET}"


# ── Config / setup ──────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("ALPACA_API_KEY"):
        cfg.setdefault("alpaca", {})["api_key"] = os.environ["ALPACA_API_KEY"]
    if os.environ.get("ALPACA_SECRET_KEY"):
        cfg.setdefault("alpaca", {})["secret_key"] = os.environ["ALPACA_SECRET_KEY"]
    if os.environ.get("DATABASE_URL"):
        cfg["database"]["url"] = os.environ["DATABASE_URL"]
    return cfg


def get_last_trading_day() -> date:
    """Return the most recent weekday (simple heuristic; holidays not handled)."""
    today = datetime.now(ET).date()
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # skip weekends
        d -= timedelta(days=1)
    return d


def parse_log_lines(target_date: date) -> list[str]:
    """Return all log lines whose timestamp matches target_date."""
    if not os.path.exists(LOG_FILE):
        return []
    date_str = target_date.strftime("%Y-%m-%d")
    lines = []
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line[:10] == date_str:
                lines.append(line.rstrip())
    return lines


# ── Automated checks ────────────────────────────────────────────────────────

class CheckResult:
    def __init__(self, num: int, name: str, status: str, detail: str = ""):
        self.num = num
        self.name = name
        self.status = status  # "PASS", "FAIL", "WARN", "SKIP"
        self.detail = detail

    def __str__(self):
        fn = {"PASS": _pass, "FAIL": _fail, "WARN": _warn, "SKIP": _skip}[self.status]
        line = f"[{self.num:>2}] {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        return fn(line)


def run_checks(
    target_date: date,
    engine,
    client: AlpacaClient,
    config: dict,
    log_lines: list[str],
) -> list[CheckResult]:
    results: list[CheckResult] = []

    # Fetch all DB data for the target date once
    with get_session(engine) as session:
        watchlist_items = (
            session.query(Watchlist).filter(Watchlist.scan_date == target_date).all()
        )
        signals = (
            session.query(Signal)
            .filter(Signal.fired_at >= datetime.combine(target_date, datetime.min.time()))
            .filter(Signal.fired_at < datetime.combine(target_date + timedelta(days=1), datetime.min.time()))
            .all()
        )
        day_start = datetime.combine(target_date, datetime.min.time())
        day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
        orders = (
            session.query(Order)
            .filter(Order.created_at >= day_start, Order.created_at < day_end)
            .all()
        )
        open_positions = session.query(Position).filter(Position.is_open == True).all()
        closed_today = (
            session.query(Position)
            .filter(Position.is_open == False)
            .filter(Position.closed_at >= day_start, Position.closed_at < day_end)
            .all()
        )
        opened_today = (
            session.query(Position)
            .filter(Position.opened_at >= day_start, Position.opened_at < day_end)
            .all()
        )
        daily_pnl = (
            session.query(DailyPnl).filter(DailyPnl.trade_date == target_date).first()
        )
        # For max-concurrent check: all positions that were open at any point during the day
        all_positions = session.query(Position).all()

    risk_cfg = config.get("risk", {})
    risk_per_trade_pct = float(risk_cfg.get("risk_per_trade_pct", 1.0))
    max_position_pct = float(risk_cfg.get("max_position_pct", 15.0))
    max_positions = int(risk_cfg.get("max_positions", 4))

    # ── Check 1: Bot ran all phases ─────────────────────────────────────
    has_premarket = any("PRE-MARKET SCAN START" in l for l in log_lines)
    has_eod = any("EOD TASKS START" in l for l in log_lines)
    if has_premarket and has_eod:
        results.append(CheckResult(1, "Bot ran all phases", "PASS"))
    elif not log_lines:
        results.append(CheckResult(1, "Bot ran all phases", "SKIP", "no log lines for date"))
    else:
        missing = []
        if not has_premarket:
            missing.append("PRE-MARKET SCAN")
        if not has_eod:
            missing.append("EOD TASKS")
        results.append(CheckResult(1, "Bot ran all phases", "FAIL", f"missing: {', '.join(missing)}"))

    # ── Check 2: No critical errors ─────────────────────────────────────
    error_lines = [l for l in log_lines if " ERROR " in l]
    critical_lines = [l for l in log_lines if "CRITICAL" in l or "UNPROTECTED" in l]
    if critical_lines:
        results.append(CheckResult(2, "No critical errors", "FAIL", f"{len(critical_lines)} CRITICAL/UNPROTECTED lines"))
    elif error_lines:
        results.append(CheckResult(2, "No critical errors", "WARN", f"{len(error_lines)} ERROR lines (no CRITICAL)"))
    else:
        results.append(CheckResult(2, "No critical errors", "PASS"))

    # ── Check 3: Scanner found candidates ───────────────────────────────
    if watchlist_items:
        by_setup = {}
        for w in watchlist_items:
            by_setup[w.setup_type] = by_setup.get(w.setup_type, 0) + 1
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(by_setup.items()))
        results.append(CheckResult(3, "Scanner found candidates", "PASS", detail))
    else:
        results.append(CheckResult(3, "Scanner found candidates", "WARN", "0 candidates (may be normal)"))

    # ── Check 4: Signals fired ──────────────────────────────────────────
    if signals:
        acted = sum(1 for s in signals if s.acted_on)
        results.append(CheckResult(4, "Signals fired", "PASS", f"{len(signals)} signals ({acted} acted on)"))
    else:
        results.append(CheckResult(4, "Signals fired", "WARN", "0 signals (may be normal)"))

    # ── Check 5: Entry prices valid ─────────────────────────────────────
    if signals:
        try:
            import yfinance as yf
            tickers = list({s.ticker for s in signals})
            data = yf.download(tickers, start=target_date, end=target_date + timedelta(days=1), progress=False)
            bad = []
            for s in signals:
                try:
                    if len(tickers) == 1:
                        high = float(data["High"].iloc[0])
                        low = float(data["Low"].iloc[0])
                    else:
                        high = float(data["High"][s.ticker].iloc[0])
                        low = float(data["Low"][s.ticker].iloc[0])
                    if not (low <= s.entry_price <= high):
                        bad.append(f"{s.ticker} entry={s.entry_price:.2f} range=[{low:.2f}, {high:.2f}]")
                except (KeyError, IndexError):
                    continue
            if bad:
                results.append(CheckResult(5, "Entry prices valid", "FAIL", "; ".join(bad)))
            else:
                results.append(CheckResult(5, "Entry prices valid", "PASS"))
        except Exception as e:
            results.append(CheckResult(5, "Entry prices valid", "SKIP", f"yfinance error: {e}"))
    else:
        results.append(CheckResult(5, "Entry prices valid", "SKIP", "no signals"))

    # ── Check 6: Order-broker sync ──────────────────────────────────────
    orders_with_broker_id = [o for o in orders if o.broker_order_id]
    if orders_with_broker_id:
        mismatches = []
        for o in orders_with_broker_id:
            try:
                broker = client.get_order_status(o.broker_order_id)
                broker_status = broker["status"]
                # Map Alpaca status to our enum
                if broker_status in ("new", "accepted", "pending_new"):
                    expected = "submitted"
                elif broker_status == "filled":
                    expected = "filled"
                elif broker_status in ("canceled", "expired", "replaced"):
                    expected = "cancelled"
                elif broker_status == "partially_filled":
                    expected = "partially_filled"
                else:
                    expected = broker_status
                if o.status != expected:
                    mismatches.append(f"{o.ticker} DB={o.status} broker={broker_status}")
            except Exception as e:
                mismatches.append(f"{o.ticker} broker query failed: {e}")
        if mismatches:
            results.append(CheckResult(6, "Order-broker sync", "FAIL", "; ".join(mismatches[:5])))
        else:
            results.append(CheckResult(6, "Order-broker sync", "PASS"))
    else:
        results.append(CheckResult(6, "Order-broker sync", "SKIP", "no orders with broker IDs"))

    # ── Check 7: Fill price slippage ────────────────────────────────────
    filled_orders = [o for o in orders if o.status == "filled" and o.filled_avg_price and o.price]
    if filled_orders:
        bad_slippage = []
        for o in filled_orders:
            slip_pct = abs(o.filled_avg_price - o.price) / o.price * 100
            if slip_pct > 1.0:
                bad_slippage.append(f"{o.ticker} {slip_pct:.2f}% ({o.price:.2f}→{o.filled_avg_price:.2f})")
        if bad_slippage:
            results.append(CheckResult(7, "Fill price slippage", "WARN", "; ".join(bad_slippage)))
        else:
            results.append(CheckResult(7, "Fill price slippage", "PASS"))
    else:
        results.append(CheckResult(7, "Fill price slippage", "SKIP", "no filled orders with prices"))

    # ── Check 8: Position-broker sync ───────────────────────────────────
    if open_positions:
        try:
            broker_positions = client.get_open_positions()
            broker_map = {p["symbol"]: p for p in broker_positions}
            mismatches = []
            for pos in open_positions:
                bp = broker_map.get(pos.ticker)
                if not bp:
                    mismatches.append(f"{pos.ticker} in DB but not at broker")
                else:
                    remaining = pos.shares - pos.partial_exit_shares
                    if abs(float(bp["qty"]) - remaining) > 0.5:
                        mismatches.append(f"{pos.ticker} shares DB={remaining} broker={bp['qty']}")
            # Check for broker positions not in DB
            db_tickers = {p.ticker for p in open_positions}
            for sym in broker_map:
                if sym not in db_tickers:
                    mismatches.append(f"{sym} at broker but not in DB")
            if mismatches:
                results.append(CheckResult(8, "Position-broker sync", "FAIL", "; ".join(mismatches[:5])))
            else:
                results.append(CheckResult(8, "Position-broker sync", "PASS"))
        except Exception as e:
            results.append(CheckResult(8, "Position-broker sync", "SKIP", f"broker query failed: {e}"))
    else:
        results.append(CheckResult(8, "Position-broker sync", "PASS", "no open positions"))

    # ── Check 9: All stops in place ─────────────────────────────────────
    if open_positions:
        missing_stops = []
        for pos in open_positions:
            if not pos.stop_order_id:
                missing_stops.append(pos.ticker)
            else:
                try:
                    status = client.get_order_status(pos.stop_order_id)
                    if status["status"] not in ("new", "accepted", "pending_new", "held"):
                        missing_stops.append(f"{pos.ticker} stop={status['status']}")
                except Exception:
                    missing_stops.append(f"{pos.ticker} (stop query failed)")
        if missing_stops:
            results.append(CheckResult(9, "All stops in place", "FAIL", "; ".join(missing_stops)))
        else:
            results.append(CheckResult(9, "All stops in place", "PASS"))
    else:
        results.append(CheckResult(9, "All stops in place", "PASS", "no open positions"))

    # ── Check 10: Stop prices match ─────────────────────────────────────
    if open_positions:
        mismatches = []
        for pos in open_positions:
            if not pos.stop_order_id:
                continue
            try:
                broker = client.get_order_status(pos.stop_order_id)
                # get_order_status doesn't return stop_price directly;
                # we rely on the AlpacaClient._trade.get_order_by_id for full detail
                # For now, just flag if we can't verify
            except Exception:
                pass
        # Since get_order_status doesn't return stop_price, we skip detailed matching
        # but flag any positions where we couldn't verify
        results.append(CheckResult(10, "Stop prices match", "SKIP", "stop_price not in order status response"))
    else:
        results.append(CheckResult(10, "Stop prices match", "PASS", "no open positions"))

    # ── Check 11: daily_pnl exists ──────────────────────────────────────
    if daily_pnl:
        results.append(CheckResult(
            11, "daily_pnl exists", "PASS",
            f"realized={daily_pnl.realized_pnl:.2f} unrealized={daily_pnl.unrealized_pnl:.2f} portfolio={daily_pnl.portfolio_value:.2f}",
        ))
    else:
        results.append(CheckResult(11, "daily_pnl exists", "FAIL", "no record for target date"))

    # ── Check 12: Realized P&L math ─────────────────────────────────────
    if daily_pnl and closed_today:
        sum_closed_pnl = sum(p.realized_pnl or 0 for p in closed_today)
        diff = abs(sum_closed_pnl - daily_pnl.realized_pnl)
        if diff < 0.02:
            results.append(CheckResult(12, "Realized P&L math", "PASS", f"sum={sum_closed_pnl:.2f} daily_pnl={daily_pnl.realized_pnl:.2f}"))
        else:
            results.append(CheckResult(12, "Realized P&L math", "WARN", f"sum={sum_closed_pnl:.2f} vs daily_pnl={daily_pnl.realized_pnl:.2f} (diff={diff:.2f})"))
    elif daily_pnl and not closed_today:
        if abs(daily_pnl.realized_pnl) < 0.02:
            results.append(CheckResult(12, "Realized P&L math", "PASS", "no closed positions, realized=0"))
        else:
            results.append(CheckResult(12, "Realized P&L math", "WARN", f"no closed positions but realized={daily_pnl.realized_pnl:.2f}"))
    else:
        results.append(CheckResult(12, "Realized P&L math", "SKIP", "missing daily_pnl or no closed positions"))

    # ── Check 13: Per-trade P&L math ────────────────────────────────────
    pnl_mismatches = []
    for pos in closed_today:
        if pos.realized_pnl is None or pos.exit_price is None:
            continue
        remaining = pos.shares - pos.partial_exit_shares
        if pos.side == "long":
            expected = remaining * (pos.exit_price - pos.entry_price)
        else:
            expected = remaining * (pos.entry_price - pos.exit_price)
        # Account for partial exit P&L
        if pos.partial_exit_done and pos.partial_exit_price:
            if pos.side == "long":
                expected += pos.partial_exit_shares * (pos.partial_exit_price - pos.entry_price)
            else:
                expected += pos.partial_exit_shares * (pos.entry_price - pos.partial_exit_price)
        diff = abs(expected - pos.realized_pnl)
        if diff > 1.0:  # tolerance of $1
            pnl_mismatches.append(
                f"{pos.ticker} expected={expected:.2f} actual={pos.realized_pnl:.2f}"
            )
    if closed_today:
        if pnl_mismatches:
            results.append(CheckResult(13, "Per-trade P&L math", "FAIL", "; ".join(pnl_mismatches)))
        else:
            results.append(CheckResult(13, "Per-trade P&L math", "PASS"))
    else:
        results.append(CheckResult(13, "Per-trade P&L math", "SKIP", "no closed positions"))

    # ── Check 14: Stop exit slippage ────────────────────────────────────
    stop_exits = [p for p in closed_today if p.exit_reason == "stop_hit"]
    if stop_exits:
        bad_slippage = []
        for p in stop_exits:
            if p.exit_price and p.stop_price:
                slip_pct = abs(p.exit_price - p.stop_price) / p.stop_price * 100
                if slip_pct > 2.0:
                    bad_slippage.append(f"{p.ticker} {slip_pct:.2f}% (stop={p.stop_price:.2f} exit={p.exit_price:.2f})")
        if bad_slippage:
            results.append(CheckResult(14, "Stop exit slippage", "WARN", "; ".join(bad_slippage)))
        else:
            results.append(CheckResult(14, "Stop exit slippage", "PASS"))
    else:
        results.append(CheckResult(14, "Stop exit slippage", "SKIP", "no stop_hit exits"))

    # ── Check 15: MA-close exit valid ───────────────────────────────────
    ma_exits = [p for p in closed_today if p.exit_reason == "trailing_ma_close"]
    if ma_exits:
        try:
            import yfinance as yf
            ma_period = int(config.get("exits", {}).get("trailing_ma_period", 10))
            bad = []
            for p in ma_exits:
                hist = yf.download(
                    p.ticker,
                    end=target_date + timedelta(days=1),
                    period=f"{ma_period + 10}d",
                    progress=False,
                )
                if len(hist) >= ma_period:
                    sma = float(hist["Close"].tail(ma_period).mean())
                    close_price = float(hist["Close"].iloc[-1])
                    if p.side == "long" and close_price >= sma:
                        bad.append(f"{p.ticker} close={close_price:.2f} > SMA{ma_period}={sma:.2f}")
                    elif p.side == "short" and close_price <= sma:
                        bad.append(f"{p.ticker} close={close_price:.2f} < SMA{ma_period}={sma:.2f}")
            if bad:
                results.append(CheckResult(15, "MA-close exit valid", "FAIL", "; ".join(bad)))
            else:
                results.append(CheckResult(15, "MA-close exit valid", "PASS"))
        except Exception as e:
            results.append(CheckResult(15, "MA-close exit valid", "SKIP", f"yfinance error: {e}"))
    else:
        results.append(CheckResult(15, "MA-close exit valid", "SKIP", "no trailing_ma_close exits"))

    # ── Check 16: Risk per trade ────────────────────────────────────────
    if opened_today and daily_pnl:
        portfolio_val = daily_pnl.portfolio_value
        bad_risk = []
        for p in opened_today:
            risk_dollars = p.shares * abs(p.entry_price - p.initial_stop_price)
            risk_pct = risk_dollars / portfolio_val * 100
            if risk_pct > risk_per_trade_pct * 1.5:  # 50% tolerance
                bad_risk.append(f"{p.ticker} risk={risk_pct:.2f}% (expected ~{risk_per_trade_pct}%)")
        if bad_risk:
            results.append(CheckResult(16, "Risk per trade", "WARN", "; ".join(bad_risk)))
        else:
            results.append(CheckResult(16, "Risk per trade", "PASS"))
    else:
        results.append(CheckResult(16, "Risk per trade", "SKIP", "no new positions or no daily_pnl"))

    # ── Check 17: Position sizing ───────────────────────────────────────
    if opened_today and daily_pnl:
        portfolio_val = daily_pnl.portfolio_value
        oversized = []
        for p in opened_today:
            notional_pct = (p.shares * p.entry_price) / portfolio_val * 100
            if notional_pct > max_position_pct * 1.1:  # 10% tolerance
                oversized.append(f"{p.ticker} {notional_pct:.1f}% (limit={max_position_pct}%)")
        if oversized:
            results.append(CheckResult(17, "Position sizing", "FAIL", "; ".join(oversized)))
        else:
            results.append(CheckResult(17, "Position sizing", "PASS"))
    else:
        results.append(CheckResult(17, "Position sizing", "SKIP", "no new positions or no daily_pnl"))

    # ── Check 18: Max positions ─────────────────────────────────────────
    # Find max concurrent open positions during the day
    # A position was open during the day if: opened_at <= day_end AND (closed_at is None OR closed_at >= day_start)
    concurrent = [
        p for p in all_positions
        if p.opened_at < day_end and (p.closed_at is None or p.closed_at >= day_start)
    ]
    if len(concurrent) > max_positions:
        results.append(CheckResult(
            18, "Max positions",
            "FAIL", f"{len(concurrent)} concurrent (limit={max_positions})",
        ))
    else:
        results.append(CheckResult(
            18, "Max positions",
            "PASS", f"{len(concurrent)} concurrent (limit={max_positions})",
        ))

    # ── Check 19: EP execution drop ─────────────────────────────────────
    # Catches the failure mode where a Strategy A/B/C entry was persisted as
    # stage="ready" (A/B at scan, C after day-2 confirm) but job_execute didn't
    # fire it — typically because the bot restarted between scan/confirm and
    # execute. Also flags rows marked stage="triggered" today with no matching
    # Signal, which would indicate the watchlist row was advanced without an
    # actual order being placed.
    ep_setups = ("ep_earnings", "ep_news")
    with get_session(engine) as session:
        ready_orphans = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type.in_(ep_setups),
                Watchlist.stage == "ready",
                Watchlist.scan_date <= target_date,
            )
            .all()
        )
        triggered_today = (
            session.query(Watchlist)
            .filter(
                Watchlist.setup_type.in_(ep_setups),
                Watchlist.stage == "triggered",
                Watchlist.stage_changed_at >= day_start,
                Watchlist.stage_changed_at < day_end,
            )
            .all()
        )
        ready_summary = [
            f"{w.ticker} ({w.setup_type}, scan={w.scan_date})" for w in ready_orphans
        ]
        signal_tickers = {
            s.ticker for s in signals
            if s.setup_type and any(s.setup_type.startswith(p) for p in ep_setups)
        }
        triggered_orphans = [w for w in triggered_today if w.ticker not in signal_tickers]
        triggered_summary = [
            f"{w.ticker} ({w.setup_type})" for w in triggered_orphans
        ]
    drops = ready_summary + triggered_summary
    if drops:
        results.append(CheckResult(
            19, "EP execution drop", "FAIL",
            f"{len(drops)} unexecuted: {'; '.join(drops[:8])}",
        ))
    else:
        results.append(CheckResult(19, "EP execution drop", "PASS"))

    # ── Check 20: Unfilled limits — why didn't the order fill? ──────────
    # For every cancelled/rejected order with 0 fill, pull 1-minute IEX bars
    # for the fill-wait window (created_at → +90s; the executor's timeout is 60s)
    # and check whether the stock ever traded at or below a buy-limit (or at or
    # above a sell-limit). Surfaces today's MCRI case: limit $111.78, low-of-
    # window $113.xx → passive limit never had a chance. Operator can then
    # decide whether to adjust entry logic, spread tolerance, or skip next time.
    unfilled_orders = [
        o for o in orders
        if o.status in ("cancelled", "rejected")
        and (o.filled_qty or 0) == 0
        and o.order_type == "limit"
        and o.price is not None
    ]
    if not unfilled_orders:
        results.append(CheckResult(20, "Unfilled limits", "PASS", "no cancelled/rejected limits"))
    else:
        postmortem_lines: list[str] = []
        api_errors = 0
        for o in unfilled_orders:
            # Window: from order create → +90s (60s exec timeout + 30s slack)
            start = o.created_at
            if start.tzinfo is None:
                start = start.replace(tzinfo=pytz.UTC)
            end = start + timedelta(seconds=90)

            try:
                bars = client.get_candles_1m_range(o.ticker, start, end)
            except Exception as e:
                api_errors += 1
                postmortem_lines.append(f"{o.ticker} ${o.price:.2f} qty={o.qty}: bars fetch failed ({type(e).__name__})")
                continue

            if not bars:
                postmortem_lines.append(f"{o.ticker} ${o.price:.2f} qty={o.qty}: no bars in window")
                continue

            win_low = min(b["low"] for b in bars)
            win_high = max(b["high"] for b in bars)

            if o.side == "buy":
                # Buy limit fills when price <= limit
                reached = win_low <= o.price
                verdict = "touched limit" if reached else "NEVER touched limit"
                postmortem_lines.append(
                    f"{o.ticker} buy@${o.price:.2f} qty={o.qty}: window low=${win_low:.2f} high=${win_high:.2f} → {verdict}"
                )
            else:
                # Sell / sell_short limit fills when price >= limit
                reached = win_high >= o.price
                verdict = "touched limit" if reached else "NEVER touched limit"
                postmortem_lines.append(
                    f"{o.ticker} {o.side}@${o.price:.2f} qty={o.qty}: window low=${win_low:.2f} high=${win_high:.2f} → {verdict}"
                )

        if api_errors == len(unfilled_orders):
            # All calls errored — don't imply bad trades, just note we couldn't check
            results.append(CheckResult(
                20, "Unfilled limits", "SKIP",
                f"{len(unfilled_orders)} unfilled, bar fetch failed for all",
            ))
        else:
            # The point isn't to FAIL — unfilled limits are normal when price moves away.
            # Surface as WARN so the operator reads the details.
            results.append(CheckResult(
                20, "Unfilled limits", "WARN",
                f"{len(unfilled_orders)} cancelled/rejected: {' | '.join(postmortem_lines[:6])}",
            ))

    return results


# ── Data dump ───────────────────────────────────────────────────────────────

def dump_data(target_date: date, engine, client: AlpacaClient, log_lines: list[str]):
    """Print raw data tables for AI review."""
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

    with get_session(engine) as session:
        watchlist_items = (
            session.query(Watchlist).filter(Watchlist.scan_date == target_date).all()
        )
        signals = (
            session.query(Signal)
            .filter(Signal.fired_at >= day_start, Signal.fired_at < day_end)
            .all()
        )
        orders = (
            session.query(Order)
            .filter(Order.created_at >= day_start, Order.created_at < day_end)
            .all()
        )
        opened_today = (
            session.query(Position)
            .filter(Position.opened_at >= day_start, Position.opened_at < day_end)
            .all()
        )
        closed_today = (
            session.query(Position)
            .filter(Position.is_open == False)
            .filter(Position.closed_at >= day_start, Position.closed_at < day_end)
            .all()
        )
        open_positions = session.query(Position).filter(Position.is_open == True).all()
        daily_pnl = (
            session.query(DailyPnl).filter(DailyPnl.trade_date == target_date).first()
        )

        # ── Watchlist summary ───────────────────────────────────────────
        print(_section("Watchlist Summary"))
        if watchlist_items:
            print(f"  {'Ticker':<8} {'Setup':<18} {'Stage':<10} {'Metadata'}")
            print(f"  {'─'*8} {'─'*18} {'─'*10} {'─'*40}")
            for w in watchlist_items:
                meta = w.meta
                meta_str = ", ".join(f"{k}={v}" for k, v in meta.items()) if meta else ""
                print(f"  {w.ticker:<8} {w.setup_type:<18} {w.stage:<10} {meta_str}")
        else:
            print("  (none)")

        # ── Signals fired ───────────────────────────────────────────────
        print(_section("Signals Fired"))
        if signals:
            print(f"  {'Ticker':<8} {'Setup':<18} {'Entry':>8} {'Stop':>8} {'Gap%':>6} {'Acted':>6} {'Time'}")
            print(f"  {'─'*8} {'─'*18} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*20}")
            for s in signals:
                gap = f"{s.gap_pct:.1f}" if s.gap_pct else "—"
                acted = "YES" if s.acted_on else "no"
                fired = s.fired_at.strftime("%H:%M:%S") if s.fired_at else "—"
                print(f"  {s.ticker:<8} {s.setup_type:<18} {s.entry_price:>8.2f} {s.stop_price:>8.2f} {gap:>6} {acted:>6} {fired}")
        else:
            print("  (none)")

        # ── Orders ──────────────────────────────────────────────────────
        print(_section("Orders"))
        if orders:
            print(f"  {'Ticker':<8} {'Side':<12} {'Type':<8} {'Price':>8} {'Fill':>8} {'Status':<14} {'BrokerID'}")
            print(f"  {'─'*8} {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*14} {'─'*20}")
            for o in orders:
                price = f"{o.price:.2f}" if o.price else "—"
                fill = f"{o.filled_avg_price:.2f}" if o.filled_avg_price else "—"
                broker_id = (o.broker_order_id or "—")[:20]
                print(f"  {o.ticker:<8} {o.side:<12} {o.order_type:<8} {price:>8} {fill:>8} {o.status:<14} {broker_id}")
        else:
            print("  (none)")

        # ── Positions opened ────────────────────────────────────────────
        print(_section("Positions Opened"))
        if opened_today:
            print(f"  {'Ticker':<8} {'Setup':<18} {'Side':<6} {'Shares':>7} {'Entry':>8} {'Stop':>8} {'Risk$':>8}")
            print(f"  {'─'*8} {'─'*18} {'─'*6} {'─'*7} {'─'*8} {'─'*8} {'─'*8}")
            for p in opened_today:
                risk = p.shares * abs(p.entry_price - p.initial_stop_price)
                print(f"  {p.ticker:<8} {p.setup_type:<18} {p.side:<6} {p.shares:>7} {p.entry_price:>8.2f} {p.initial_stop_price:>8.2f} {risk:>8.2f}")
        else:
            print("  (none)")

        # ── Positions closed ────────────────────────────────────────────
        print(_section("Positions Closed"))
        if closed_today:
            print(f"  {'Ticker':<8} {'Setup':<18} {'Entry':>8} {'Exit':>8} {'P&L':>10} {'Reason':<18} {'Days'}")
            print(f"  {'─'*8} {'─'*18} {'─'*8} {'─'*8} {'─'*10} {'─'*18} {'─'*5}")
            for p in closed_today:
                pnl = p.realized_pnl or 0
                exit_p = f"{p.exit_price:.2f}" if p.exit_price else "—"
                reason = p.exit_reason or "—"
                print(f"  {p.ticker:<8} {p.setup_type:<18} {p.entry_price:>8.2f} {exit_p:>8} {pnl:>+10.2f} {reason:<18} {p.days_held}")
        else:
            print("  (none)")

        # ── Open positions ──────────────────────────────────────────────
        print(_section("Open Positions"))
        if open_positions:
            print(f"  {'Ticker':<8} {'Setup':<18} {'Shares':>7} {'Entry':>8} {'Stop':>8} {'StopOrderID'}")
            print(f"  {'─'*8} {'─'*18} {'─'*7} {'─'*8} {'─'*8} {'─'*20}")
            for p in open_positions:
                remaining = p.shares - p.partial_exit_shares
                stop_id = (p.stop_order_id or "—")[:20]
                print(f"  {p.ticker:<8} {p.setup_type:<18} {remaining:>7} {p.entry_price:>8.2f} {p.stop_price:>8.2f} {stop_id}")
        else:
            print("  (none)")

        # ── Daily P&L ──────────────────────────────────────────────────
        print(_section("Daily P&L"))
        if daily_pnl:
            print(f"  Realized:       {daily_pnl.realized_pnl:>+12.2f}")
            print(f"  Unrealized:     {daily_pnl.unrealized_pnl:>+12.2f}")
            print(f"  Total:          {daily_pnl.total_pnl:>+12.2f}")
            print(f"  Portfolio:      {daily_pnl.portfolio_value:>12.2f}")
            print(f"  Winners:        {daily_pnl.num_winners:>12}")
            print(f"  Losers:         {daily_pnl.num_losers:>12}")
            print(f"  Total trades:   {daily_pnl.num_trades:>12}")
        else:
            print("  (no daily_pnl record)")

    # ── Log warnings/errors ─────────────────────────────────────────────
    print(_section("Log Warnings & Errors"))
    warn_error_lines = [l for l in log_lines if " WARNING " in l or " ERROR " in l or "CRITICAL" in l]
    if warn_error_lines:
        for line in warn_error_lines[:50]:  # cap at 50 lines
            print(f"  {line}")
        if len(warn_error_lines) > 50:
            print(f"  ... and {len(warn_error_lines) - 50} more")
    else:
        print("  (none)")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    # Parse date argument
    if len(sys.argv) > 1:
        try:
            target_date = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"Invalid date: {sys.argv[1]}  (expected YYYY-MM-DD)")
            sys.exit(2)
    else:
        target_date = get_last_trading_day()

    print(_header(f"Daily Verification Report — {target_date}"))
    print(f"  Generated: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}")

    # Load config and connect
    config = load_config()
    db_url = config.get("database", {}).get("url")
    engine = get_engine(db_url)
    client = AlpacaClient(config)
    client.connect()

    # Parse logs
    log_lines = parse_log_lines(target_date)
    print(f"  Log lines for date: {len(log_lines)}")

    # ── Run automated checks ────────────────────────────────────────────
    print(_header("Automated Checks"))
    results = run_checks(target_date, engine, client, config, log_lines)
    for r in results:
        print(r)

    # Summary
    passes = sum(1 for r in results if r.status == "PASS")
    fails = sum(1 for r in results if r.status == "FAIL")
    warns = sum(1 for r in results if r.status == "WARN")
    skips = sum(1 for r in results if r.status == "SKIP")

    print(_section("Summary"))
    print(f"  {GREEN}PASS: {passes}{RESET}  {RED}FAIL: {fails}{RESET}  {YELLOW}WARN: {warns}{RESET}  SKIP: {skips}")

    # ── Data dump ───────────────────────────────────────────────────────
    print(_header("Data Dump (for AI review)"))
    dump_data(target_date, engine, client, log_lines)

    # Exit code
    if fails > 0:
        print(f"\n{RED}RESULT: {fails} check(s) FAILED — investigate above{RESET}")
        sys.exit(1)
    else:
        print(f"\n{GREEN}RESULT: All checks passed (with {warns} warnings){RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
