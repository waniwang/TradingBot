"""EP Earnings swing strategy plugin."""

from __future__ import annotations

import json as _json
import logging
from collections import defaultdict
from datetime import datetime

import pytz

from core.loader import ExitAction, ScheduleEntry

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


def _scan_job(config, client, db_engine, notify):
    """3:00 PM ET — scan + evaluate EP earnings candidates."""
    return PLUGIN.job_scan(config, client, db_engine, notify)


def _day2_confirm_job(config, client, db_engine, notify):
    """3:45 PM ET — check yesterday's Strategy C candidates for day-2 confirmation."""
    return PLUGIN.job_day2_confirm(config, client, db_engine, notify)


def _execute_job(config, client, db_engine, notify):
    """3:50 PM ET — execute staged EP earnings entries."""
    return PLUGIN.job_execute(config, client, db_engine, notify)


class EPEarningsPlugin:
    """
    EP Earnings Swing — long setup on earnings-driven gap-ups.

    Scans at 3:00 PM ET for stocks that gapped up on earnings.
    Evaluates Strategy A (tight) and Strategy B (relaxed) filters.
    Executes at 3:50 PM ET near market close.
    Uses shared exit logic + max hold period (50 days).
    """

    name = "ep_earnings"
    display_name = "EP Earnings Swing"
    watchlist_persist_days = 1  # single-day: expire at EOD

    schedule = [
        ScheduleEntry(
            job_id="ep_earnings_scan",
            cron={"hour": 15, "minute": 0, "day_of_week": "mon-fri"},
            handler=_scan_job,
        ),
        ScheduleEntry(
            job_id="ep_earnings_day2_confirm",
            cron={"hour": 15, "minute": 45, "day_of_week": "mon-fri"},
            handler=_day2_confirm_job,
        ),
        ScheduleEntry(
            job_id="ep_earnings_execute",
            # Retry every minute from 3:50-3:59 PM. Idempotent: skips tickers with
            # an open Position or a recent (<10 min) non-terminal Order.
            cron={"hour": 15, "minute": "50-59", "day_of_week": "mon-fri"},
            handler=_execute_job,
        ),
    ]

    def premarket_scan(self, config, client, db_engine, notify) -> list[dict]:
        return []  # EP earnings scans at 3 PM, not premarket

    def evaluate_signal(self, ticker, watchlist_entry, **ctx):
        return None  # EP earnings executes at 3:50 PM, not via intraday signals

    def on_position_update(self, pos, current_price, daily_closes) -> ExitAction | None:
        return None  # use shared exit logic + max_hold_period in position_tracker

    def backtest_entry(self, ticker, date, row, history, bt_config):
        return None

    def backtest_exit(self, pos, date, row, history, bt_config):
        return None

    # ------------------------------------------------------------------
    # Scheduled job handlers
    # ------------------------------------------------------------------

    def job_scan(self, config, client, db_engine, notify):
        """3:00 PM ET — EOD scan for EP earnings gap-up candidates."""
        from core.execution import is_trading_day
        from strategies.ep_earnings.scanner import scan_ep_earnings
        from strategies.ep_earnings.strategy import evaluate_ep_earnings_strategies
        from scanner.watchlist_manager import persist_candidates, get_active_watchlist

        if not is_trading_day(client):
            logger.info("EP earnings scan skipped — not a trading day")
            return "Skipped — not a trading day"

        logger.info("=== EOD EP EARNINGS SCAN START ===")
        if notify:
            notify("EOD EP EARNINGS SCAN STARTED")

        today = datetime.now(ET).date()

        try:
            # Phase 1: Scanner
            candidates = scan_ep_earnings(config, client)
            logger.info("EOD EP earnings scanner: %d candidates", len(candidates))

            if not candidates:
                if notify:
                    notify("EP EARNINGS SCAN: 0 candidates found")
                return "0 candidates"

            # Phase 2: Fetch daily bars
            tickers = [c["ticker"] for c in candidates]
            daily_bars = client.get_daily_bars_batch(tickers, days=300)
            logger.info("Fetched daily bars for %d/%d tickers", len(daily_bars), len(tickers))

            # Phase 3: Evaluate Strategy A + B + C
            entries, rejections = evaluate_ep_earnings_strategies(candidates, daily_bars, config)
            data_errors = [r for r in rejections if r["is_data_error"]]
            logger.info(
                "Strategy evaluation: %d entries from %d candidates (%d data errors)",
                len(entries), len(candidates), len(data_errors),
            )

            # Build a ticker→reason map so we can show the actual feature values
            # (or data-error message) per filtered-out ticker in the Telegram summary.
            reject_by_ticker = {r["ticker"]: r for r in rejections}

            # Separate immediate entries (A/B) from day-2 pending (C)
            immediate = [e for e in entries if not e.get("day2_confirm")]
            pending_c = [e for e in entries if e.get("day2_confirm")]

            if immediate or pending_c:
                persist_candidates(candidates, "ep_earnings", "active", today, db_engine)

            # Persist A/B entries to DB as stage="ready" — job_execute reads them at 3:50 PM
            for entry in immediate:
                self._persist_entry(entry, today, db_engine)

            # Persist C candidates as "watching" (pending day-2 confirmation)
            for entry in pending_c:
                self._persist_pending_day2(entry, today, db_engine)

            # If every candidate failed with a data error, the batch-wide yfinance
            # fetch is broken — fail loud so _track_job fires JOB FAILED. Returning
            # "0 passed" here would silently hide a systemic bug.
            if data_errors and len(data_errors) == len(candidates):
                msg = (
                    f"EP EARNINGS: ALL {len(candidates)} candidates failed due to "
                    f"missing/short daily bars — likely yfinance batch fetch failure."
                )
                if notify:
                    lines = [msg]
                    for r in data_errors:
                        lines.append(f"  {r['ticker']}: {r['reason']}")
                    notify("\n".join(lines))
                raise RuntimeError(msg)

            if immediate or pending_c:
                a_count = sum(1 for e in entries if e["ep_strategy"] == "A")
                b_count = sum(1 for e in entries if e["ep_strategy"] == "B")
                c_count = len(pending_c)

                if notify:
                    lines = [f"EP EARNINGS: {len(entries)} strategy entries from {len(candidates)} candidates"]
                    lines.append(f"  Strategy A: {a_count} | Strategy B: {b_count} | Strategy C (pending day-2): {c_count}")
                    for e in immediate:
                        lines.append(
                            f"  {e['ticker']} ({e['ep_strategy']}): gap {e['gap_pct']:.1f}%, "
                            f"entry ${e['entry_price']:.2f}, stop ${e['stop_price']:.2f}"
                        )
                    for e in pending_c:
                        lines.append(
                            f"  {e['ticker']} (C-pending): gap {e['gap_pct']:.1f}%, "
                            f"gap close ${e['gap_day_close']:.2f} — awaiting day-2 confirm"
                        )
                    if data_errors:
                        lines.append(f"  WARN: {len(data_errors)} tickers had data errors:")
                        for r in data_errors:
                            lines.append(f"    {r['ticker']}: {r['reason']}")
                    notify("\n".join(lines))

                entry_tickers = ", ".join(e["ticker"] for e in entries)
                return f"{len(entries)} entries ({a_count}A+{b_count}B+{c_count}C-pending): {entry_tickers}"
            else:
                if notify:
                    lines = [f"EP EARNINGS: {len(candidates)} candidates, 0 passed strategy filters"]
                    for c in candidates:
                        ticker = c["ticker"]
                        rej = reject_by_ticker.get(ticker)
                        if rej and rej["is_data_error"]:
                            lines.append(f"  {ticker}: DATA ERROR — {rej['reason']}")
                        elif rej:
                            lines.append(f"  {ticker}: gap {c['gap_pct']:.1f}% — {rej['reason']}")
                        else:
                            lines.append(f"  {ticker}: gap {c['gap_pct']:.1f}% (no evaluation recorded)")
                    notify("\n".join(lines))
                cand_tickers = ", ".join(c["ticker"] for c in candidates)
                return f"{len(candidates)} scanned, 0 passed: {cand_tickers}"

        except Exception as e:
            logger.error("EOD EP earnings scan failed: %s", e)
            if notify:
                notify(f"EOD EP EARNINGS SCAN FAILED: {e}")
            raise

    def job_execute(self, config, client, db_engine, notify):
        """3:50 PM ET — execute entries persisted as stage='ready' in the watchlist.

        DB-driven: any A/B from today's scan and any C confirmed at 15:45 will be
        flagged stage='ready' with the execution payload in metadata_json. This is
        crash-safe — a process restart between scan/confirm and execute is recoverable
        because nothing is held in memory.
        """
        from core.execution import is_trading_day, _execute_entry, _compute_current_daily_pnl, _compute_current_weekly_pnl
        from signals.base import SignalResult
        from risk.manager import RiskManager
        from db.models import Order, Position, Watchlist, get_session
        from datetime import timedelta

        if not is_trading_day(client):
            return "Skipped — not a trading day"

        today = datetime.now(ET).date()

        # Load entries from DB, not memory
        entries: list[dict] = []
        with get_session(db_engine) as session:
            rows = session.query(Watchlist).filter(
                Watchlist.setup_type == "ep_earnings",
                Watchlist.stage == "ready",
                Watchlist.scan_date <= today,
            ).all()
            for wl in rows:
                meta = wl.meta or {}
                if not meta.get("ep_strategy"):
                    logger.warning("EP earnings: ready row %s has no ep_strategy in meta, skipping", wl.ticker)
                    continue
                entries.append({"ticker": wl.ticker, **meta})

        if not entries:
            logger.info("EP earnings execute: no ready rows in watchlist")
            return "No entries staged"

        logger.info("=== EP EARNINGS EXECUTE: %d ready rows ===", len(entries))
        risk = RiskManager(config)

        # Group by ticker: if same stock passes both A and B, enter once
        by_ticker = defaultdict(list)
        for entry in entries:
            by_ticker[entry["ticker"]].append(entry)

        executed = 0
        for ticker, ticker_entries in by_ticker.items():
            strategies = [e["ep_strategy"] for e in ticker_entries]
            entry = ticker_entries[0]
            strategy_label = "+".join(sorted(set(strategies)))

            # Idempotency guards:
            #   1. An open Position exists → we're already in this trade, skip.
            #   2. A recent non-terminal Order exists → a prior job_execute run (or crashed
            #      mid-flight) already submitted the order. Skip to avoid double-entry
            #      in the replay window between place_limit_order and mark_triggered.
            with get_session(db_engine) as session:
                existing = session.query(Position).filter_by(
                    ticker=ticker, is_open=True
                ).first()
                if existing:
                    logger.info("EP earnings: %s already has open position, skipping", ticker)
                    continue
                recent_order = session.query(Order).filter(
                    Order.ticker == ticker,
                    Order.status.in_(["pending", "submitted", "filled", "partially_filled"]),
                    Order.created_at >= datetime.utcnow() - timedelta(minutes=10),
                ).first()
                if recent_order is not None:
                    logger.warning(
                        "EP earnings: %s has recent order (id=%s status=%s) — job_execute replay detected, skipping",
                        ticker, recent_order.id, recent_order.status,
                    )
                    continue

            # Risk manager checks
            with get_session(db_engine) as session:
                open_count = session.query(Position).filter_by(is_open=True).count()

            # Let any Alpaca-account error propagate — wrong-size trade is worse than no trade.
            # _track_job catches the exception and fires JOB FAILED via Telegram.
            portfolio_value = client.get_portfolio_value()

            daily_pnl = _compute_current_daily_pnl(db_engine)
            weekly_pnl = _compute_current_weekly_pnl(db_engine)
            can_enter, reason = risk.can_enter(open_count, daily_pnl, weekly_pnl, portfolio_value)
            if not can_enter:
                logger.info("EP earnings: %s blocked by risk manager: %s", ticker, reason)
                if notify:
                    notify(f"EP EARNINGS BLOCKED: {ticker} ({strategy_label}) - {reason}")
                continue

            shares = risk.calculate_position_size(
                portfolio_value, entry["entry_price"], entry["stop_price"]
            )
            if shares <= 0:
                logger.info("EP earnings: %s position size = 0, skipping", ticker)
                continue

            # Use distinct setup_type for Strategy C (different max hold period)
            setup_type = "ep_earnings"
            if strategy_label == "C":
                setup_type = "ep_earnings_c"

            signal = SignalResult(
                ticker=ticker,
                setup_type=setup_type,
                side="long",
                entry_price=entry["entry_price"],
                stop_price=entry["stop_price"],
                gap_pct=entry["gap_pct"],
                volume_ratio=entry.get("rvol"),
                notes=f"EP Earnings Strategy {strategy_label} | "
                      f"CHG-OPEN={entry['chg_open_pct']:.1f}% CIR={entry['close_in_range']:.0f} "
                      f"P10D={entry['prev_10d_change_pct']:.1f}% ATR={entry['atr_pct']:.1f}%",
            )

            logger.info(
                "EP earnings entry: %s (%s) %d shares @ $%.2f stop $%.2f",
                ticker, strategy_label, shares, signal.entry_price, signal.stop_price,
            )
            _execute_entry(
                ticker, signal, shares, client, db_engine, notify,
                watchlist_setup_type="ep_earnings",
            )
            executed += 1

        if notify:
            notify(f"EP EARNINGS EXECUTE: {executed}/{len(by_ticker)} tickers entered")

        return f"{executed}/{len(by_ticker)} entered: {', '.join(list(by_ticker.keys()))}"

    def job_day2_confirm(self, config, client, db_engine, notify):
        """3:45 PM ET — check yesterday's Strategy C candidates for day-2 confirmation."""
        from core.execution import is_trading_day
        from db.models import Watchlist, get_session

        if not is_trading_day(client):
            return "Skipped — not a trading day"

        logger.info("=== EP EARNINGS DAY-2 CONFIRM CHECK ===")

        today = datetime.now(ET).date()

        # Find pending day-2 candidates from previous trading days (stage=watching, setup_type=ep_earnings)
        confirmed = []
        failures: list[tuple[str, str]] = []  # (ticker, reason) — must never be silent
        with get_session(db_engine) as session:
            pending = session.query(Watchlist).filter(
                Watchlist.setup_type == "ep_earnings",
                Watchlist.stage == "watching",
                Watchlist.scan_date < today,
            ).all()

            if not pending:
                logger.info("EP earnings day-2 confirm: no pending candidates")
                return "No pending candidates"

            logger.info("EP earnings day-2 confirm: %d pending candidates", len(pending))
            attempted = 0

            for wl in pending:
                meta = wl.meta or {}
                if not meta.get("day2_confirm"):
                    continue

                ticker = wl.ticker
                gap_day_close = meta.get("gap_day_close", 0)
                attempted += 1

                # Fetch current price (proxy for day 2 close at 3:45 PM)
                try:
                    snapshot = client.get_snapshots([ticker])
                    snap = snapshot.get(ticker)
                    if snap and hasattr(snap, "latest_trade"):
                        current_price = float(snap.latest_trade.price)
                    elif snap and hasattr(snap, "minute_bar") and snap.minute_bar:
                        current_price = float(snap.minute_bar.close)
                    else:
                        logger.error("EP earnings day-2: no price data for %s", ticker)
                        wl.stage = "expired"
                        failures.append((ticker, "no price data"))
                        continue
                except Exception as e:
                    logger.error("EP earnings day-2: failed to get price for %s: %s", ticker, e)
                    wl.stage = "expired"
                    failures.append((ticker, f"snapshot error: {e}"))
                    continue

                # Day-2 confirmation: current price > gap day close (positive 1D return)
                if current_price > gap_day_close:
                    stop_pct = meta.get("stop_loss_pct", 7.0)
                    stop_price = round(current_price * (1 - stop_pct / 100), 2)
                    day1_return_pct = round((current_price - gap_day_close) / gap_day_close * 100, 2)

                    # Promote to "ready" — job_execute at 3:50 PM picks this up from DB.
                    # Update meta with the execution payload so execute can rebuild the entry
                    # without depending on any in-memory state.
                    meta.update({
                        "ep_strategy": "C",
                        "entry_price": current_price,
                        "stop_price": stop_price,
                        "stop_loss_pct": stop_pct,
                        "max_hold_days": meta.get("max_hold_days", 20),
                        "current_price": current_price,
                        "today_volume": 0,
                        "day1_return_pct": day1_return_pct,
                    })
                    wl.meta = meta
                    wl.stage = "ready"
                    confirmed.append({"ticker": ticker, **meta})
                    logger.info(
                        "%s: Day-2 CONFIRMED — price $%.2f > gap close $%.2f (+%.1f%%) — promoted to ready",
                        ticker, current_price, gap_day_close, day1_return_pct,
                    )
                else:
                    wl.stage = "expired"
                    logger.info(
                        "%s: Day-2 REJECTED — price $%.2f <= gap close $%.2f (%.1f%%)",
                        ticker, current_price, gap_day_close,
                        (current_price - gap_day_close) / gap_day_close * 100,
                    )

            session.commit()

        if confirmed and notify:
            lines = [f"EP EARNINGS DAY-2 CONFIRM: {len(confirmed)} confirmed"]
            for e in confirmed:
                lines.append(
                    f"  {e['ticker']}: entry ${e['entry_price']:.2f}, "
                    f"stop ${e['stop_price']:.2f}, 1D return +{e['day1_return_pct']:.1f}%"
                )
            notify("\n".join(lines))

        if failures:
            msg_lines = [f"EP EARNINGS DAY-2 CONFIRM: {len(failures)}/{attempted} failed"]
            msg_lines.extend(f"  {t}: {reason}" for t, reason in failures)
            msg = "\n".join(msg_lines)
            if notify:
                notify(msg)
            # Batch-wide failure → escalate: likely an Alpaca outage, not per-ticker noise.
            # _track_job turns RuntimeError into a JOB FAILED alert.
            if attempted > 0 and len(failures) == attempted:
                raise RuntimeError(msg)

        return f"{len(confirmed)} confirmed from {len(pending)} pending ({len(failures)} failed)"

    def _persist_pending_day2(self, entry: dict, scan_date, db_engine):
        """Persist a Strategy C candidate as pending day-2 confirmation."""
        from db.models import Watchlist, get_session

        meta = {
            "ep_strategy": "C",
            "day2_confirm": True,
            "gap_day_close": entry["gap_day_close"],
            "gap_pct": entry["gap_pct"],
            "stop_loss_pct": entry["stop_loss_pct"],
            "max_hold_days": entry["max_hold_days"],
            "chg_open_pct": entry["chg_open_pct"],
            "close_in_range": entry["close_in_range"],
            "downside_from_open": entry["downside_from_open"],
            "prev_10d_change_pct": entry["prev_10d_change_pct"],
            "atr_pct": entry["atr_pct"],
            "open_price": entry["open_price"],
            "prev_close": entry["prev_close"],
            "prev_high": entry.get("prev_high", 0),
            "market_cap": entry.get("market_cap", 0),
            "rvol": entry.get("rvol", 0),
        }

        with get_session(db_engine) as session:
            wl = Watchlist(
                ticker=entry["ticker"],
                setup_type="ep_earnings",
                stage="watching",
                scan_date=scan_date,
                metadata_json=_json.dumps(meta),
                notes="EP Earnings Strategy C — pending day-2 confirm",
            )
            session.add(wl)
            session.commit()

    def _persist_entry(self, entry: dict, scan_date, db_engine):
        """Persist an EP earnings strategy entry to the watchlist with metadata."""
        from db.models import Watchlist, get_session

        meta = {
            "ep_strategy": entry["ep_strategy"],
            "gap_pct": entry["gap_pct"],
            "entry_price": entry["entry_price"],
            "stop_price": entry["stop_price"],
            "stop_loss_pct": entry["stop_loss_pct"],
            "max_hold_days": entry["max_hold_days"],
            "chg_open_pct": entry["chg_open_pct"],
            "close_in_range": entry["close_in_range"],
            "downside_from_open": entry["downside_from_open"],
            "prev_10d_change_pct": entry["prev_10d_change_pct"],
            "atr_pct": entry["atr_pct"],
            "open_price": entry["open_price"],
            "prev_close": entry["prev_close"],
            "market_cap": entry.get("market_cap", 0),
            "rvol": entry.get("rvol", 0),
        }

        with get_session(db_engine) as session:
            wl = Watchlist(
                ticker=entry["ticker"],
                setup_type="ep_earnings",
                stage="ready",
                scan_date=scan_date,
                metadata_json=_json.dumps(meta),
                notes=f"EP Earnings Strategy {entry['ep_strategy']}",
            )
            session.add(wl)
            session.commit()


PLUGIN = EPEarningsPlugin()
