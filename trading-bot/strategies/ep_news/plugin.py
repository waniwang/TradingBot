"""EP News swing strategy plugin."""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime

import pytz

from core.loader import ExitAction, ScheduleEntry

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


def _scan_job(config, client, db_engine, notify):
    """3:00 PM ET — scan + evaluate EP news candidates."""
    return PLUGIN.job_scan(config, client, db_engine, notify)


def _execute_job(config, client, db_engine, notify):
    """3:50 PM ET — execute staged EP news entries."""
    return PLUGIN.job_execute(config, client, db_engine, notify)


class EPNewsPlugin:
    """
    EP News Swing — long setup on news-driven gap-ups (non-earnings).

    Scans at 3:00 PM ET for stocks that gapped up on news catalysts.
    Excludes earnings-driven gaps (handled by ep_earnings).
    Both A and B use a -7% stop (B was -10% pre-2026-05-08; tightening
    materially improved B's profit factor — see strategy.py).
    Executes at 3:50 PM ET near market close.
    Uses shared exit logic + max hold period (50 days).
    """

    name = "ep_news"
    display_name = "EP News Swing"
    watchlist_persist_days = 1  # single-day: expire at EOD

    schedule = [
        ScheduleEntry(
            job_id="ep_news_scan",
            cron={"hour": 15, "minute": 5, "day_of_week": "mon-fri"},
            handler=_scan_job,
        ),
        ScheduleEntry(
            job_id="ep_news_execute",
            # Retry every minute from 3:37-3:59 PM. Idempotent: skips tickers with
            # an open Position or a recent (<10 min) non-terminal Order.
            cron={"hour": 15, "minute": "37-59", "day_of_week": "mon-fri"},
            handler=_execute_job,
        ),
    ]

    def premarket_scan(self, config, client, db_engine, notify) -> list[dict]:
        return []  # EP news scans at 3 PM, not premarket

    def evaluate_signal(self, ticker, watchlist_entry, **ctx):
        return None  # EP news executes at 3:50 PM, not via intraday signals

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
        """3:00 PM ET — EOD scan for EP news gap-up candidates."""
        from core.execution import is_trading_day
        from strategies.ep_news.scanner import scan_ep_news
        from strategies.ep_news.strategy import evaluate_ep_news_strategies
        from scanner.watchlist_manager import persist_candidates

        if not is_trading_day(client):
            logger.info("EP news scan skipped — not a trading day")
            return "Skipped — not a trading day"

        logger.info("=== EOD EP NEWS SCAN START ===")
        if notify:
            notify("EOD EP NEWS SCAN STARTED")

        today = datetime.now(ET).date()

        try:
            # Phase 1: Scanner (exclude earnings)
            candidates = scan_ep_news(config, client)
            logger.info("EOD EP news scanner: %d candidates", len(candidates))

            if not candidates:
                if notify:
                    notify("EP NEWS SCAN: 0 candidates found")
                return "0 candidates"

            # Phase 2: Fetch daily bars
            tickers = [c["ticker"] for c in candidates]
            daily_bars = client.get_daily_bars_batch(tickers, days=300)
            logger.info("Fetched daily bars for %d/%d tickers", len(daily_bars), len(tickers))

            # Phase 3: Evaluate Strategy A and B
            entries, rejections = evaluate_ep_news_strategies(candidates, daily_bars, config)
            data_errors = [r for r in rejections if r["is_data_error"]]
            logger.info(
                "News strategy evaluation: %d entries from %d candidates (%d data errors)",
                len(entries), len(candidates), len(data_errors),
            )

            reject_by_ticker = {r["ticker"]: r for r in rejections}

            if entries:
                persist_candidates(candidates, "ep_news", "active", today, db_engine)

            for entry in entries:
                self._persist_entry(entry, today, db_engine)

            # If every candidate failed with a data error, the batch-wide yfinance
            # fetch is broken — fail loud so _track_job fires JOB FAILED.
            if data_errors and len(data_errors) == len(candidates):
                msg = (
                    f"EP NEWS: ALL {len(candidates)} candidates failed due to "
                    f"missing/short daily bars — likely yfinance batch fetch failure."
                )
                if notify:
                    lines = [msg]
                    for r in data_errors:
                        lines.append(f"  {r['ticker']}: {r['reason']}")
                    notify("\n".join(lines))
                raise RuntimeError(msg)

            if entries:
                a_count = sum(1 for e in entries if e["ep_strategy"] == "A")
                b_count = sum(1 for e in entries if e["ep_strategy"] == "B")

                if notify:
                    lines = [f"EP NEWS: {len(entries)} strategy entries from {len(candidates)} candidates"]
                    lines.append(f"  Strategy A: {a_count} | Strategy B: {b_count}")
                    for e in entries:
                        lines.append(
                            f"  {e['ticker']} ({e['ep_strategy']}): gap {e['gap_pct']:.1f}%, "
                            f"entry ${e['entry_price']:.2f}, stop ${e['stop_price']:.2f}"
                        )
                    if data_errors:
                        lines.append(f"  WARN: {len(data_errors)} tickers had data errors:")
                        for r in data_errors:
                            lines.append(f"    {r['ticker']}: {r['reason']}")
                    notify("\n".join(lines))

                entry_tickers = ", ".join(e["ticker"] for e in entries)
                return f"{len(entries)} entries ({a_count}A+{b_count}B): {entry_tickers}"
            else:
                if notify:
                    lines = [f"EP NEWS: {len(candidates)} candidates, 0 passed strategy filters"]
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
            logger.error("EOD EP news scan failed: %s", e)
            if notify:
                notify(f"EOD EP NEWS SCAN FAILED: {e}")
            raise

    def job_execute(self, config, client, db_engine, notify):
        """3:37 PM ET — execute entries persisted as stage='ready' in the watchlist.

        DB-driven and crash-safe: A/B from today's scan are flagged stage='ready'
        with the execution payload in metadata_json.

        Multi-position: A and B variants for the same ticker each get their own
        Position (setup_type ep_news_a / ep_news_b). Idempotency is keyed on
        (ticker, setup_type) so a retry only skips the specific variant that
        already filled, not both.

        Watchlist source: by default reads from ``db_engine`` (the local DB).
        If ``config["watchlist_source_db_url"]`` is set, reads from that
        external DB instead (used by the IB passive-executor bot to consume
        the Alpaca bot's vetted watchlist). The IB-bot path includes
        stage="triggered" rows so we still see the row after Alpaca has
        executed; idempotency is enforced via the local Order/Position tables.
        """
        from core.execution import (
            is_trading_day, _execute_entry,
            _compute_current_daily_pnl, _compute_current_weekly_pnl,
            resolve_execution_price,
        )
        from signals.base import SignalResult
        from risk.manager import RiskManager
        from db.models import Order, Position, Signal as DbSignal, Watchlist, get_session
        from datetime import timedelta

        if not is_trading_day(client):
            return "Skipped — not a trading day"

        today = datetime.now(ET).date()

        # Load entries — either from the local DB (Alpaca bot path) or from
        # the Alpaca DB across processes (IB passive-executor path).
        watchlist_source_db_url = config.get("watchlist_source_db_url")
        entries: list[dict] = []
        if watchlist_source_db_url:
            from executor.watchlist_source import read_ready_entries
            entries = read_ready_entries(watchlist_source_db_url, "ep_news", today)
        else:
            from core.trading_calendar import is_valid_scan_date
            with get_session(db_engine) as session:
                # Pre-filter SQL by a loose 4-day window for performance, then
                # apply the per-variant scan_date check in Python (A/B require
                # scan_date == today). Without this, a stale "ready" row
                # leftover from a prior session leaks into the next day's
                # execute. See `core/trading_calendar.py` for details.
                #
                # Stage filter must include BOTH "ready" and "triggered". See
                # ep_earnings/plugin.py for the full rationale — same bug, same
                # fix.
                rows = session.query(Watchlist).filter(
                    Watchlist.setup_type == "ep_news",
                    Watchlist.stage.in_(["ready", "triggered"]),
                    Watchlist.scan_date >= today - timedelta(days=4),
                    Watchlist.scan_date <= today,
                ).all()
                for wl in rows:
                    meta = wl.meta or {}
                    variant = meta.get("ep_strategy")
                    if not variant:
                        logger.warning("EP news: ready row %s has no ep_strategy in meta, skipping", wl.ticker)
                        continue
                    if not is_valid_scan_date(variant, wl.scan_date, today):
                        logger.warning(
                            "EP news: %s (%s) scan_date=%s not valid for today=%s — "
                            "skipping stale row",
                            wl.ticker, variant, wl.scan_date, today,
                        )
                        continue
                    entries.append({"ticker": wl.ticker, **meta})

        if not entries:
            logger.info("EP news execute: no ready rows in watchlist")
            return "No entries staged"

        logger.info("=== EP NEWS EXECUTE: %d ready rows ===", len(entries))
        risk = RiskManager(config)
        executed = 0

        for entry in entries:
            ep_strategy = entry["ep_strategy"]
            # Each variant gets a distinct setup_type so A and B on the same ticker
            # produce separate Position rows with independent stop/exit tracking.
            setup_type = f"ep_news_{ep_strategy.lower()}"
            ticker = entry["ticker"]

            # Idempotency guards scoped to this specific variant:
            #   1. An open Position with this setup_type exists → already entered, skip.
            #   2. A recent non-terminal Order via Signal(setup_type) → prior run already
            #      submitted. Skip to avoid double-entry in the replay window.
            with get_session(db_engine) as session:
                existing = session.query(Position).filter_by(
                    ticker=ticker, setup_type=setup_type, is_open=True
                ).first()
                if existing:
                    logger.info("EP news: %s (%s) already has open position, skipping", ticker, ep_strategy)
                    continue
                # Replay guard: scope by ticker + recency, not by signal/setup_type.
                # See strategies/ep_earnings/plugin.py for the rationale — same fix
                # for the same join-blind-spot bug.
                recent_order = (
                    session.query(Order)
                    .filter(
                        Order.ticker == ticker,
                        Order.status.in_(["pending", "submitted", "filled", "partially_filled"]),
                        Order.created_at >= datetime.utcnow() - timedelta(minutes=10),
                    )
                    .first()
                )
                if recent_order is not None:
                    logger.warning(
                        "EP news: %s (%s) has recent order (id=%s status=%s) — replay detected, skipping",
                        ticker, ep_strategy, recent_order.id, recent_order.status,
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
                if reason == "max_positions":
                    from db.models import record_risk_skip
                    stop_pct_skip = float(entry.get("stop_loss_pct", 7.0))
                    intended_entry_skip = float(entry["entry_price"])
                    intended_stop_skip = round(intended_entry_skip * (1 - stop_pct_skip / 100), 4)
                    record_risk_skip(
                        db_engine,
                        ticker=ticker,
                        setup_type="ep_news",
                        ep_strategy=ep_strategy,
                        block_reason="max_positions",
                        intended_entry=intended_entry_skip,
                        intended_stop=intended_stop_skip,
                        portfolio_value=portfolio_value,
                        open_position_count=open_count,
                        notes=f"open={open_count}/cap={risk.max_positions}",
                    )
                logger.info("EP news: %s (%s) blocked by risk manager: %s", ticker, ep_strategy, reason)
                if notify:
                    notify(f"EP NEWS BLOCKED: {ticker} ({ep_strategy}) - {reason}")
                continue

            # Refresh entry against live mid — scanner captured price at 3:05 PM but
            # execute runs at 3:37+, so the mark is ~32 min stale on a running name.
            # Returns None to skip this attempt; next minute's retry re-evaluates.
            stop_pct = entry.get("stop_loss_pct", 7.0)
            resolved = resolve_execution_price(
                ticker, entry["entry_price"], stop_pct,
                side="long", client=client, config=config, notify=notify,
            )
            if resolved is None:
                continue
            use_entry, use_stop, price_label = resolved

            shares = risk.calculate_position_size(
                portfolio_value, use_entry, use_stop
            )
            if shares <= 0:
                logger.info("EP news: %s (%s) position size = 0, skipping", ticker, ep_strategy)
                continue

            # Pre-flight buying-power check — see ep_earnings/plugin.py for
            # the rationale.
            cost_basis = shares * use_entry
            try:
                buying_power = client.get_buying_power()
            except Exception as e:
                logger.warning(
                    "EP news: %s BP pre-flight fetch failed: %s — submitting anyway",
                    ticker, e,
                )
                if notify:
                    notify(f"BP CHECK FAILED for {ticker}: {type(e).__name__}: {e}")
                buying_power = None

            if buying_power is not None and cost_basis > buying_power:
                from db.models import record_risk_skip
                record_risk_skip(
                    db_engine,
                    ticker=ticker,
                    setup_type="ep_news",
                    ep_strategy=ep_strategy,
                    block_reason="insufficient_bp",
                    intended_entry=use_entry,
                    intended_stop=use_stop,
                    portfolio_value=portfolio_value,
                    open_position_count=open_count,
                    notes=f"cost=${cost_basis:,.0f} > BP=${buying_power:,.0f}",
                )
                logger.info(
                    "EP news: %s (%s) BP=$%.0f < cost=$%.0f — skipping",
                    ticker, ep_strategy, buying_power, cost_basis,
                )
                if notify:
                    notify(
                        f"INSUFFICIENT BP: {ticker} ({ep_strategy}) "
                        f"cost=${cost_basis:,.0f} > BP=${buying_power:,.0f} — skipping"
                    )
                continue

            signal = SignalResult(
                ticker=ticker,
                setup_type=setup_type,
                side="long",
                entry_price=use_entry,
                stop_price=use_stop,
                gap_pct=entry["gap_pct"],
                volume_ratio=entry.get("rvol"),
                notes=f"EP News Strategy {ep_strategy} | price={price_label} | "
                      f"CHG-OPEN={entry['chg_open_pct']:.1f}% CIR={entry['close_in_range']:.0f} "
                      f"DS={entry['downside_from_open']:.1f}% P10D={entry['prev_10d_change_pct']:.1f}% "
                      f"ATR={entry['atr_pct']:.1f}% Vol={entry.get('today_volume', 0)/1e6:.1f}M",
            )

            logger.info(
                "EP news entry: %s (%s) %d shares @ $%.2f stop $%.2f (%s)",
                ticker, ep_strategy, shares, signal.entry_price, signal.stop_price, price_label,
            )
            _execute_entry(
                ticker, signal, shares, client, db_engine, notify,
                watchlist_setup_type="ep_news",
                watchlist_ep_strategy=ep_strategy,
            )
            executed += 1

        entry_labels = [f"{e['ticker']}({e['ep_strategy']})" for e in entries]
        if notify:
            notify(f"EP NEWS EXECUTE: {executed}/{len(entries)} entered")

        return f"{executed}/{len(entries)} entered: {', '.join(entry_labels)}"

    def _persist_entry(self, entry: dict, scan_date, db_engine):
        """Persist an EP news strategy entry to the watchlist with metadata."""
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
                setup_type="ep_news",
                stage="ready",
                scan_date=scan_date,
                metadata_json=_json.dumps(meta),
                notes=f"EP News Strategy {entry['ep_strategy']}",
            )
            session.add(wl)
            session.commit()


PLUGIN = EPNewsPlugin()
