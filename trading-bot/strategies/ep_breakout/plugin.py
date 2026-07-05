"""EP Breakout strategy plugin (EP 2.0 Track A — gap-anchored rested breakout).

Lifecycle (all times ET):
  3:15 PM  ep_breakout_scan     — qualify today's gap events (BIG/LOUD/
                                  VOLATILE), persist stage="watching" rows.
  3:50 PM  ep_breakout_confirm  — daily state machine over watching rows
           (retries to 3:59):
             close < gap-day low        -> expired ("gap-low break")
             past bo_window sessions    -> expired ("no confirmation")
             rested & close > gap high  -> chase guard, else EXECUTE with
                                           OTO GTC stop at entry x 0.92
  9:40 AM  ep_breakout_partial_check — sell 33% once price >= entry x 1.30
           (monitor/position_tracker.check_ep_breakout_target_partial).
  3:55 PM  eod_tasks — breakeven stop move after a close >= entry x 1.15,
           exit on close < 10d SMA, 50d max hold (position_tracker).

Validated 2026-07-05: docs/research/ep2_validation.md (PASS 6/6). Spec:
docs/ep-breakout-plugin-spec.md.
"""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timedelta

import pytz

from core.loader import ExitAction, ScheduleEntry

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


def _scan_job(config, client, db_engine, notify):
    """3:15 PM ET — qualify gap events into watching rows."""
    return PLUGIN.job_scan(config, client, db_engine, notify)


def _confirm_job(config, client, db_engine, notify):
    """3:50 PM ET — run the confirm state machine; execute on breakout."""
    return PLUGIN.job_confirm(config, client, db_engine, notify)


def _partial_check_job(config, client, db_engine, notify):
    """9:40 AM ET — +30% profit-target partial (33%) via PositionTracker."""
    from monitor.position_tracker import PositionTracker
    tracker = PositionTracker(config, db_engine, client, notify)
    return tracker.check_ep_breakout_target_partial()


class EPBreakoutPlugin:
    """
    EP Breakout — long setup on rested breakouts above the gap-day high of
    big, loud, volatile gap events (catalyst-agnostic: earnings AND news).

    Unlike ep_earnings/ep_news, entry is NOT on gap day: candidates sit in
    stage="watching" for up to bo_window sessions and enter only when a
    close confirms above the gap-day high after >= bo_min_days of rest —
    the mechanism that skips gap-and-fade candidates entirely.
    """

    name = "ep_breakout"
    display_name = "EP Breakout"
    # Multi-day by design: watching rows persist for the bo_window;
    # lifecycle (expire/trigger) is fully owned by job_confirm, so keep the
    # shared nightly expiry away from these rows.
    watchlist_persist_days = 0

    schedule = [
        ScheduleEntry(
            job_id="ep_breakout_scan",
            cron={"hour": 15, "minute": 15, "day_of_week": "mon-fri"},
            handler=_scan_job,
        ),
        ScheduleEntry(
            job_id="ep_breakout_confirm",
            # Retry every minute 3:50-3:59. Idempotent: execution guarded by
            # (ticker, setup_type) Position + recent-Order checks.
            cron={"hour": 15, "minute": "50-59", "day_of_week": "mon-fri"},
            handler=_confirm_job,
        ),
        ScheduleEntry(
            job_id="ep_breakout_partial_check",
            cron={"hour": 9, "minute": 40, "day_of_week": "mon-fri"},
            handler=_partial_check_job,
        ),
    ]

    def premarket_scan(self, config, client, db_engine, notify) -> list[dict]:
        return []  # scans at 3:15 PM, not premarket

    def evaluate_signal(self, ticker, watchlist_entry, **ctx):
        return None  # entries fire from job_confirm, not intraday signals

    def on_position_update(self, pos, current_price, daily_closes) -> ExitAction | None:
        return None  # exits handled by broker stop + position_tracker EOD/9:40 logic

    def backtest_entry(self, ticker, date, row, history, bt_config):
        return None  # research backtests live in sweeps/path_harness.py

    def backtest_exit(self, pos, date, row, history, bt_config):
        return None

    # ------------------------------------------------------------------
    # 3:15 PM — scan job
    # ------------------------------------------------------------------

    def job_scan(self, config, client, db_engine, notify):
        from core.execution import is_trading_day
        from strategies.ep_breakout.scanner import scan_ep_breakout
        from db.models import Watchlist, get_session

        if not is_trading_day(client):
            logger.info("EP breakout scan skipped — not a trading day")
            return "Skipped — not a trading day"

        logger.info("=== EP BREAKOUT SCAN START ===")
        today = datetime.now(ET).date()

        candidates = scan_ep_breakout(config, client, notify=notify)
        if not candidates:
            if notify:
                notify("EP BREAKOUT SCAN: 0 qualifying gap events")
            return "0 candidates"

        persisted = 0
        with get_session(db_engine) as session:
            for c in candidates:
                # One watching row per (ticker, gap_date). Re-running the
                # scan same day must not duplicate.
                existing = (
                    session.query(Watchlist)
                    .filter_by(ticker=c["ticker"], setup_type="ep_breakout",
                               scan_date=today)
                    .first()
                )
                if existing:
                    continue
                meta = {
                    "gap_date": today.isoformat(),
                    # Provisional 3:15 PM levels — job_confirm refreshes both
                    # from the completed gap-day daily bar every run.
                    "gap_high": c["today_high"],
                    "gap_low": c["today_low"],
                    "gap_pct": c["gap_pct"],
                    "atr_pct": c["atr_pct"],
                    "market_cap": c["market_cap"],
                    "dollar_vol": c["dollar_vol"],
                    "prev_close": c["prev_close"],
                    "open_price": c["open_price"],
                }
                session.add(Watchlist(
                    ticker=c["ticker"],
                    setup_type="ep_breakout",
                    stage="watching",
                    scan_date=today,
                    metadata_json=_json.dumps(meta),
                    notes=f"EP breakout watch: gap {c['gap_pct']:.1f}%",
                ))
                persisted += 1
            session.commit()

        if notify:
            lines = [f"EP BREAKOUT: {persisted} new watch candidates"]
            for c in candidates:
                lines.append(
                    f"  {c['ticker']}: gap {c['gap_pct']:.1f}%, "
                    f"${c['dollar_vol']/1e6:.0f}M vol, ATR {c['atr_pct']:.1f}%, "
                    f"mcap ${c['market_cap']/1e9:.0f}B"
                )
            notify("\n".join(lines))

        tickers = ", ".join(c["ticker"] for c in candidates)
        return f"{persisted} new watching: {tickers}"

    # ------------------------------------------------------------------
    # 3:50 PM — confirm state machine + execute
    # ------------------------------------------------------------------

    def job_confirm(self, config, client, db_engine, notify):
        """Daily confirm pass over watching rows.

        State machine per row (mirrors sweeps/path_harness.py::
        find_rested_breakout_entry — the validated reference):
          - any completed close since gap < gap_low  -> expired
          - sessions elapsed > bo_window             -> expired
          - today_index >= bo_min_days AND price > gap_high:
              price > gap_high * (1 + premium/100)   -> expired (chase)
              else                                   -> execute
          - otherwise keep watching.

        Trade-path rule: everything from the execute branch downward either
        propagates to _tracked_strategy_job or notifies before returning.
        """
        from core.execution import is_trading_day, _execute_entry
        from core.execution import (
            _compute_current_daily_pnl, _compute_current_weekly_pnl,
        )
        from signals.base import SignalResult
        from risk.manager import RiskManager
        from db.models import Order, Position, Watchlist, get_session

        if not is_trading_day(client):
            return "Skipped — not a trading day"

        sig_cfg = config.get("signals", {})
        bo_min_days = int(sig_cfg.get("ep_breakout_bo_min_days", 4))
        bo_window = int(sig_cfg.get("ep_breakout_bo_window", 15))
        premium_pct = float(sig_cfg.get("ep_breakout_bo_max_premium_pct", 5.0))
        stop_pct = float(sig_cfg.get("ep_breakout_stop_loss_pct", 8.0))

        today = datetime.now(ET).date()

        with get_session(db_engine) as session:
            # "watching" is the normal state; "ready" is a crash-recovery
            # state (promoted but bot died before the order went out) — the
            # decision re-runs and the idempotency guards prevent doubles.
            rows = (
                session.query(Watchlist)
                .filter(Watchlist.setup_type == "ep_breakout",
                        Watchlist.stage.in_(["watching", "ready"]))
                .all()
            )
            watch = [
                {"id": w.id, "ticker": w.ticker, "meta": w.meta,
                 "scan_date": w.scan_date}
                for w in rows
            ]

        if not watch:
            return "0 watching"

        tickers = sorted({w["ticker"] for w in watch})
        # One batch fetch serves every row: completed closes since gap,
        # authoritative gap-day OHLC, and (via snapshots) the live price.
        bars = client.get_daily_bars_batch(tickers, days=60)
        snapshots = client.get_snapshots(tickers)

        executed, expired, kept = [], [], []
        failures: list[tuple[str, str]] = []
        risk = RiskManager(config)

        for w in watch:
            ticker = w["ticker"]
            meta = w["meta"] or {}
            try:
                decision = self._decide(
                    ticker, meta, bars.get(ticker), snapshots.get(ticker),
                    today, bo_min_days, bo_window, premium_pct,
                )
            except Exception as e:
                # Per-ticker data failure: skip this row this run (next
                # minute / next day retries), notify, keep going. Batch-wide
                # failure raises below.
                logger.warning("EP breakout confirm: %s decision failed: %s", ticker, e)
                failures.append((ticker, str(e)))
                continue

            action = decision["action"]
            if action == "expire":
                self._expire_row(db_engine, w["id"], decision["reason"])
                expired.append(f"{ticker}({decision['reason']})")
                continue
            if action == "watch":
                kept.append(ticker)
                continue

            # action == "enter"
            price = decision["price"]
            new_meta = decision["meta_updates"]
            # Promote watching -> ready BEFORE executing: mark_triggered
            # (called inside _execute_entry) only flips active/ready rows,
            # and the ready state also makes the row crash-safe — a restart
            # between promotion and execution re-enters via the idempotency
            # guards, not a fresh decision.
            self._promote_row(db_engine, w["id"], meta | new_meta)
            done = self._execute_breakout_entry(
                ticker, price, stop_pct, meta | new_meta,
                config, client, db_engine, notify, risk,
                _compute_current_daily_pnl, _compute_current_weekly_pnl,
                _execute_entry, SignalResult, Order, Position, get_session,
            )
            if done:
                executed.append(ticker)

        if failures and len(failures) == len(watch):
            raise RuntimeError(
                "EP BREAKOUT CONFIRM: all rows failed data checks: "
                + "; ".join(f"{t}: {e}" for t, e in failures)
            )

        parts = []
        if executed:
            parts.append(f"entered={','.join(executed)}")
        if expired:
            parts.append(f"expired={','.join(expired)}")
        if kept:
            parts.append(f"watching={len(kept)}")
        if failures:
            parts.append(f"failures={len(failures)}")
        summary = " | ".join(parts) if parts else "no action"
        if notify and (executed or expired):
            notify(f"EP BREAKOUT CONFIRM: {summary}")
        return summary

    # -- decision core (pure — unit-tested directly) --------------------

    @staticmethod
    def _decide(ticker, meta, df, snap, today, bo_min_days, bo_window,
                premium_pct):
        """Classify one watching row. Returns
        {"action": "watch"|"expire"|"enter", "reason": str, "price": float,
         "meta_updates": dict}.

        Raises on missing/short data — caller treats it as a per-ticker
        data failure (retry next run)."""
        import pandas as pd

        if df is None or (hasattr(df, "empty") and df.empty):
            raise RuntimeError("no daily bars")
        if snap is None or not snap.get("latest_price"):
            raise RuntimeError("no snapshot/latest price")
        price = float(snap["latest_price"])

        gap_date = pd.Timestamp(meta["gap_date"]).date()
        # get_daily_bars_batch frames carry a "date" COLUMN (not an index).
        if "date" not in df.columns:
            raise RuntimeError("bars missing 'date' column")
        dates = [pd.Timestamp(d).date() for d in df["date"]]
        if not dates:
            raise RuntimeError("bars have no dates")

        # Authoritative gap-day levels from the completed daily bar (the
        # 3:15 PM scan captured provisional intraday values).
        try:
            gi = dates.index(gap_date)
            gap_high = float(df["high"].iloc[gi])
            gap_low = float(df["low"].iloc[gi])
        except ValueError:
            # Gap day bar not in window (data gap) — fall back to meta.
            gap_high = float(meta["gap_high"])
            gap_low = float(meta["gap_low"])

        # Completed sessions strictly after gap day, strictly before today.
        completed = [i for i, d in enumerate(dates) if gap_date < d < today]
        today_index = len(completed)  # today's bar index in path terms

        # Thesis break: any completed close below the gap-day low, or the
        # live price below it right now.
        for i in completed:
            if float(df["close"].iloc[i]) < gap_low:
                return {"action": "expire", "reason": "gap-low break",
                        "price": price, "meta_updates": {}}
        if price < gap_low:
            return {"action": "expire", "reason": "gap-low break",
                    "price": price, "meta_updates": {}}

        # Deadline: today is beyond the confirmation window.
        if today_index >= bo_window:
            return {"action": "expire", "reason": "no confirmation",
                    "price": price, "meta_updates": {}}

        # Breakout trigger (only after enough rest).
        if today_index >= bo_min_days and price > gap_high:
            if price > gap_high * (1 + premium_pct / 100.0):
                return {"action": "expire", "reason": "chase guard",
                        "price": price, "meta_updates": {}}
            return {
                "action": "enter", "reason": "breakout confirmed",
                "price": price,
                "meta_updates": {
                    "gap_high": round(gap_high, 4),
                    "gap_low": round(gap_low, 4),
                    "confirm_day": today_index,
                    "entry_price": round(price, 4),
                },
            }

        return {"action": "watch", "reason": "no trigger", "price": price,
                "meta_updates": {}}

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _expire_row(db_engine, row_id, reason):
        from db.models import Watchlist, get_session
        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(id=row_id).first()
            if wl is not None and wl.stage in ("watching", "ready"):
                wl.stage = "expired"
                wl.notes = f"ep_breakout: {reason}"
                session.commit()
                logger.info("EP breakout: %s expired (%s)", wl.ticker, reason)

    @staticmethod
    def _promote_row(db_engine, row_id, meta):
        """watching -> ready with the confirm-time meta (refreshed gap levels,
        confirm_day, entry_price). mark_triggered flips ready -> triggered
        after the order is placed."""
        import json
        from db.models import Watchlist, get_session
        with get_session(db_engine) as session:
            wl = session.query(Watchlist).filter_by(id=row_id).first()
            if wl is not None and wl.stage in ("watching", "ready"):
                wl.stage = "ready"
                wl.metadata_json = json.dumps(meta)
                wl.stage_changed_at = datetime.utcnow()
                session.commit()

    def _execute_breakout_entry(
        self, ticker, price, stop_pct, meta, config, client, db_engine,
        notify, risk, _daily_pnl, _weekly_pnl, _execute_entry, SignalResult,
        Order, Position, get_session,
    ) -> bool:
        """Risk-checked execution of one confirmed breakout. Mirrors the
        ep_news execute path (idempotency, risk gates, BP pre-flight); any
        broker/account error propagates to _tracked_strategy_job."""
        setup_type = "ep_breakout"

        with get_session(db_engine) as session:
            existing = session.query(Position).filter_by(
                ticker=ticker, setup_type=setup_type, is_open=True
            ).first()
            if existing:
                logger.info("EP breakout: %s already has open position, skipping", ticker)
                return False
            recent_order = (
                session.query(Order)
                .filter(
                    Order.ticker == ticker,
                    Order.status.in_(["pending", "submitted", "filled",
                                      "partially_filled"]),
                    Order.created_at >= datetime.utcnow() - timedelta(minutes=10),
                )
                .first()
            )
            if recent_order is not None:
                logger.warning(
                    "EP breakout: %s has recent order (id=%s status=%s) — "
                    "replay detected, skipping",
                    ticker, recent_order.id, recent_order.status,
                )
                return False
            open_count = session.query(Position).filter_by(is_open=True).count()

        # Wrong-size trade is worse than no trade — let account errors raise.
        portfolio_value = client.get_portfolio_value()
        can_enter, reason = risk.can_enter(
            open_count, _daily_pnl(db_engine), _weekly_pnl(db_engine),
            portfolio_value,
        )
        if not can_enter:
            if reason == "max_positions":
                from db.models import record_risk_skip
                stop_price = round(price * (1 - stop_pct / 100), 4)
                record_risk_skip(
                    db_engine, ticker=ticker, setup_type=setup_type,
                    ep_strategy=None, block_reason="max_positions",
                    intended_entry=price, intended_stop=stop_price,
                    portfolio_value=portfolio_value,
                    open_position_count=open_count,
                    notes=f"open={open_count}/cap={risk.max_positions}",
                )
            logger.info("EP breakout: %s blocked by risk manager: %s", ticker, reason)
            if notify:
                notify(f"EP BREAKOUT BLOCKED: {ticker} - {reason}")
            return False

        stop_price = round(price * (1 - stop_pct / 100), 4)
        shares = risk.calculate_position_size(portfolio_value, price, stop_price)
        if shares <= 0:
            logger.info("EP breakout: %s position size = 0, skipping", ticker)
            return False

        cost_basis = shares * price
        try:
            buying_power = client.get_buying_power()
        except Exception as e:
            logger.warning("EP breakout: %s BP pre-flight failed: %s — submitting anyway",
                           ticker, e)
            if notify:
                notify(f"BP CHECK FAILED for {ticker}: {type(e).__name__}: {e}")
            buying_power = None
        if buying_power is not None and cost_basis > buying_power:
            from db.models import record_risk_skip
            record_risk_skip(
                db_engine, ticker=ticker, setup_type=setup_type,
                ep_strategy=None, block_reason="insufficient_bp",
                intended_entry=price, intended_stop=stop_price,
                portfolio_value=portfolio_value,
                open_position_count=open_count,
                notes=f"cost=${cost_basis:,.0f} > BP=${buying_power:,.0f}",
            )
            logger.info("EP breakout: %s BP=$%.0f < cost=$%.0f — skipping",
                        ticker, buying_power, cost_basis)
            if notify:
                notify(f"INSUFFICIENT BP: {ticker} cost=${cost_basis:,.0f} > "
                       f"BP=${buying_power:,.0f} — skipping")
            return False

        signal = SignalResult(
            ticker=ticker,
            setup_type=setup_type,
            side="long",
            entry_price=price,
            stop_price=stop_price,
            gap_pct=meta.get("gap_pct"),
            volume_ratio=None,
            notes=(
                f"EP Breakout D{meta.get('confirm_day', '?')} confirm | "
                f"gapHigh={meta.get('gap_high')} gap={meta.get('gap_pct', 0):.1f}% "
                f"ATR={meta.get('atr_pct', 0):.1f}% "
                f"$vol={meta.get('dollar_vol', 0) / 1e6:.0f}M"
            ),
        )
        logger.info(
            "EP breakout entry: %s %d shares @ $%.2f stop $%.2f (D%s confirm)",
            ticker, shares, price, stop_price, meta.get("confirm_day", "?"),
        )
        _execute_entry(
            ticker, signal, shares, client, db_engine, notify,
            watchlist_setup_type="ep_breakout",
        )
        return True


PLUGIN = EPBreakoutPlugin()
