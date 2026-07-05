"""
Intraday position tracker.

Runs on every 1m candle update and:
- Checks if stops have been hit
- Triggers partial exits when gain/day conditions are met
- Updates trailing stops at EOD
- Logs all events to the database
"""

from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Callable

import pytz

from db.models import Position, Order, DailyPnl, get_session
from risk.manager import RiskManager

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


class PositionTracker:
    """
    Maintains the intraday state of all open positions and fires exit logic.

    Usage:
        tracker = PositionTracker(config, db_engine, alpaca_client, notify_fn)
        tracker.on_candle_update(ticker, candles_1m, daily_closes)  # called each minute
        tracker.run_eod_tasks(daily_closes_map)                      # called at 4:00 PM ET
    """

    def __init__(
        self,
        config: dict,
        db_engine,
        broker_client,
        notify: Callable[[str], None] | None = None,
        plugins: dict | None = None,
    ):
        self.config = config
        self.engine = db_engine
        self.client = broker_client
        self.notify = notify or (lambda msg: None)
        self.risk = RiskManager(config)
        self._plugins = plugins or {}  # {name: StrategyPlugin}

        exits = config["exits"]
        self.partial_exit_after_days: int = int(exits["partial_exit_after_days"])
        self.partial_exit_gain_pct: float = float(exits["partial_exit_gain_threshold_pct"])
        self.partial_exit_fraction: float = float(exits["partial_exit_fraction"])
        self.trailing_ma_period: int = int(exits["trailing_ma_period"])

        # EP-only EOD time-based partial (added 2026-05-11). Fires via the
        # 9:40 AM ET `ep_time_partial_check` scheduled job, not on_candle_update.
        # See check_ep_time_partial / _fire_ep_time_partial below.
        self.ep_time_partial_day: int = int(exits.get("ep_time_partial_day", 19))
        self.ep_time_partial_fraction: float = float(exits.get("ep_time_partial_fraction", 0.40))
        self.ep_time_partial_new_stop_pct: float = float(exits.get("ep_time_partial_new_stop_pct", 5.0))

        # EP Breakout exits (EP 2.0 Track A, added 2026-07-05). Price-target
        # partial at +30% (9:40 AM check_ep_breakout_target_partial), stop ->
        # breakeven after a +15% close (EOD), 10d MA-close exit active from
        # day 1 (no partial precondition), NO D19 time partial, NO MA stop
        # tightening. See strategies/ep_breakout/README.md.
        sig = config.get("signals", {})
        self.ep_bo_target_pct: float = float(sig.get("ep_breakout_profit_target_pct", 30.0))
        self.ep_bo_target_fraction: float = float(sig.get("ep_breakout_profit_target_fraction", 0.33))
        self.ep_bo_breakeven_trigger_pct: float = float(sig.get("ep_breakout_breakeven_trigger_pct", 15.0))
        self.ep_bo_trail_ma_days: int = int(sig.get("ep_breakout_trail_ma_days", 10))

        # halted state
        self._daily_halt: bool = False
        self._weekly_halt: bool = False

    # ------------------------------------------------------------------
    # Main entry point — called each 1m candle
    # ------------------------------------------------------------------

    def on_candle_update(
        self,
        ticker: str,
        current_price: float,
        candles_1m: list[dict],
        daily_closes: list[float],
    ):
        """Process a new 1m candle for `ticker`."""
        with get_session(self.engine) as session:
            positions = (
                session.query(Position)
                .filter_by(ticker=ticker, is_open=True)
                .all()
            )
            for pos in positions:
                self._evaluate_position(
                    session, pos, current_price, candles_1m, daily_closes
                )

    def _evaluate_position(
        self,
        session,
        pos: Position,
        current_price: float,
        candles_1m: list[dict],
        daily_closes: list[float],
    ):
        # 1. Check stop hit
        if self._is_stop_hit(pos, current_price):
            self._close_position(session, pos, current_price, reason="stop_hit")
            return

        # 2. Check pending partial exit fill before any logic that could re-trigger
        if self._check_pending_partial_exit(session, pos):
            return  # pending order exists — wait for it

        # 3. Strategy-specific exit hook (replaces hardcoded parabolic branch)
        plugin = self._plugins.get(pos.setup_type)
        if plugin is not None:
            exit_action = plugin.on_position_update(pos, current_price, daily_closes)
            if exit_action is not None:
                if exit_action.action == "partial":
                    self._do_partial_exit(session, pos, current_price)
                    return
                elif exit_action.action == "close":
                    self._close_position(session, pos, current_price, reason=exit_action.reason)
                    return

        # 4. Check partial exit conditions
        if not pos.partial_exit_done:
            days_held = pos.days_held
            gain_pct = pos.gain_pct(current_price)
            if (
                days_held >= self.partial_exit_after_days
                and gain_pct >= self.partial_exit_gain_pct
            ):
                self._do_partial_exit(session, pos, current_price)

    def _is_stop_hit(self, pos: Position, current_price: float) -> bool:
        if pos.side == "long":
            return current_price <= pos.stop_price
        else:
            return current_price >= pos.stop_price

    # ------------------------------------------------------------------
    # Partial exit
    # ------------------------------------------------------------------

    def _do_partial_exit(self, session, pos: Position, current_price: float):
        shares_to_sell = max(1, int(pos.shares * self.partial_exit_fraction))

        logger.info(
            "Partial exit %s: placing limit order for %d/%d shares @ %.2f",
            pos.ticker, shares_to_sell, pos.shares, current_price,
        )

        try:
            order_side = "sell" if pos.side == "long" else "buy_to_cover"
            order_id = self.client.place_limit_order(
                pos.ticker, order_side, shares_to_sell, current_price
            )
        except Exception as e:
            logger.error("Partial exit order failed for %s: %s", pos.ticker, e)
            return

        # Track the pending order — don't mark done or resize stop until confirmed
        pos.partial_exit_order_id = order_id
        pos.partial_exit_shares = shares_to_sell
        pos.partial_exit_price = current_price
        session.commit()

        self.notify(
            f"PARTIAL EXIT ORDER PLACED: {pos.ticker}\n"
            f"Selling {shares_to_sell}/{pos.shares} shares @ ${current_price:.2f}\n"
            f"Awaiting fill confirmation before resizing stop."
        )

    def _check_pending_partial_exit(self, session, pos: Position) -> bool:
        """
        Poll a pending partial exit order. If filled, finalize the partial exit
        and resize the stop. Returns True if a pending order was found (filled or not).
        """
        if not pos.partial_exit_order_id or pos.partial_exit_done:
            return False

        try:
            status_info = self.client.get_order_status(pos.partial_exit_order_id)
        except Exception as e:
            logger.warning("Failed to poll partial exit order for %s: %s", pos.ticker, e)
            return True  # still pending, don't re-trigger

        status = status_info.get("status", "")
        filled_qty = status_info.get("filled_qty", 0)

        if status == "filled":
            self._finalize_partial_exit(session, pos, filled_qty)
            return True

        if status in ("cancelled", "expired", "rejected", "done_for_day"):
            logger.warning(
                "Partial exit order for %s ended with status %s (filled %d) — clearing",
                pos.ticker, status, filled_qty,
            )
            if filled_qty > 0:
                # Partially filled before cancellation — finalize what we got
                self._finalize_partial_exit(session, pos, filled_qty)
            else:
                # Order failed entirely — clear pending state so it can retry
                pos.partial_exit_order_id = None
                pos.partial_exit_shares = 0
                pos.partial_exit_price = None
                session.commit()
            return True

        # Still pending (new, accepted, partially_filled) — wait
        return True

    def _finalize_partial_exit(self, session, pos: Position, filled_qty: int):
        """Mark partial exit done and resize the stop order for remaining shares."""
        remaining = pos.shares - filled_qty

        pos.partial_exit_done = True
        pos.partial_exit_shares = filled_qty
        pos.partial_exit_order_id = None

        # Replace stop: cancel old (full qty), place new (reduced qty) at break-even
        old_stop = pos.stop_price
        if pos.stop_order_id:
            try:
                self.client.cancel_order(pos.stop_order_id)
                stop_side = "sell" if pos.side == "long" else "buy_to_cover"
                new_stop_id = self.client.place_stop_order(
                    pos.ticker, stop_side, remaining, pos.entry_price
                )
                pos.stop_order_id = new_stop_id
                pos.stop_price = pos.entry_price
            except Exception as e:
                logger.error(
                    "CRITICAL: Failed to replace stop for %s after partial exit: %s. "
                    "Stop may be missing — position at risk.",
                    pos.ticker, e,
                )
                # Old stop was cancelled but new one failed — mark as unprotected
                pos.stop_order_id = None
                self.notify(
                    f"CRITICAL: Partial exit stop replacement FAILED for {pos.ticker}\n"
                    f"Stop was NOT replaced at break-even (${pos.entry_price:.2f}).\n"
                    f"Old stop at ${old_stop:.2f} was cancelled. NO ACTIVE STOP. Check manually."
                )
        else:
            # No broker stop order; just update DB
            pos.stop_price = pos.entry_price

        session.commit()

        self.notify(
            f"PARTIAL EXIT FILLED: {pos.ticker} sold {filled_qty} shares\n"
            f"Stop moved to break-even: ${pos.entry_price:.2f} (was ${old_stop:.2f})\n"
            f"Remaining: {remaining} shares"
        )

    # ------------------------------------------------------------------
    # Close position
    # ------------------------------------------------------------------

    def _close_position(
        self,
        session,
        pos: Position,
        current_price: float,
        reason: str,
    ):
        logger.info(
            "Closing %s (%s): price=%.2f reason=%s",
            pos.ticker, pos.side, current_price, reason,
        )

        try:
            remaining_shares = pos.shares - pos.partial_exit_shares
            self.client.close_position(pos.ticker, remaining_shares, pos.side)
        except Exception as e:
            logger.error("close_position failed for %s: %s", pos.ticker, e)
            return

        # Cancel the stop order if still open
        if pos.stop_order_id:
            try:
                self.client.cancel_order(pos.stop_order_id)
            except Exception as e:
                logger.warning("Failed to cancel stop order for %s: %s", pos.ticker, e)

        remaining = pos.shares - pos.partial_exit_shares
        if pos.side == "long":
            pnl = remaining * (current_price - pos.entry_price)
        else:
            pnl = remaining * (pos.entry_price - current_price)

        # Include partial exit P&L
        if pos.partial_exit_done and pos.partial_exit_price is not None:
            if pos.side == "long":
                pnl += pos.partial_exit_shares * (pos.partial_exit_price - pos.entry_price)
            else:
                pnl += pos.partial_exit_shares * (pos.entry_price - pos.partial_exit_price)

        pos.exit_price = current_price
        pos.exit_reason = reason
        pos.realized_pnl = pnl
        pos.is_open = False
        pos.closed_at = datetime.utcnow()
        session.commit()

        sign = "+" if pnl >= 0 else ""
        self.notify(
            f"POSITION CLOSED: {pos.ticker} ({pos.setup_type})\n"
            f"Exit: ${current_price:.2f} | Reason: {reason}\n"
            f"P&L: {sign}${pnl:.2f}"
        )

    # ------------------------------------------------------------------
    # EOD tasks
    # ------------------------------------------------------------------

    def run_eod_tasks(self, daily_closes_map: dict[str, list[float]]):
        """
        Called at ~3:55 PM ET.
        - Checks max hold period exits (EP earnings swing: 50 days).
        - Checks for MA-close exits (daily close below trailing MA).
        - Updates trailing stops for remaining open positions.
        """
        with get_session(self.engine) as session:
            positions = session.query(Position).filter_by(is_open=True).all()

            # First: check max hold period (EP earnings swing positions)
            self._check_max_hold_exits(session, positions, daily_closes_map)

            # Refresh — some may have been closed
            positions = session.query(Position).filter_by(is_open=True).all()

            # Second: EP breakout breakeven move (stop -> entry after a
            # close >= entry x (1 + trigger/100)); one-shot per position.
            self._check_ep_breakout_breakeven(session, positions, daily_closes_map)

            # Third: check if today's close is below the trailing MA
            # (only for positions that have already done a partial exit;
            # ep_breakout positions trail from day 1 — see the method)
            self._check_ma_close_exits(session, positions, daily_closes_map)

            # Refresh the list — some positions may have been closed above
            positions = session.query(Position).filter_by(is_open=True).all()
            for pos in positions:
                if pos.setup_type == "ep_breakout":
                    # Fixed 8% stop + breakeven move only. MA-tightening the
                    # broker stop would front-run the validated close-below-
                    # MA10 exit with an intraday variant that was never
                    # backtested.
                    continue
                closes = daily_closes_map.get(pos.ticker)
                if not closes:
                    continue
                self._update_trailing_stop(session, pos, closes)

    def _check_max_hold_exits(self, session, positions, daily_closes_map):
        """Exit positions that have exceeded their max hold period."""
        cfg = self.config.get("signals", {})
        # Per-setup-type max hold days
        _ep_e_hold = int(cfg.get("ep_earnings_max_hold_days", 50))
        _ep_e_c_hold = int(cfg.get("ep_earnings_c_max_hold_days", 20))
        _ep_n_hold = int(cfg.get("ep_news_max_hold_days", 50))
        _ep_n_c_hold = int(cfg.get("ep_news_c_max_hold_days", 20))
        max_hold_map = {
            "ep_earnings": _ep_e_hold,
            "ep_earnings_a": _ep_e_hold,
            "ep_earnings_b": _ep_e_hold,
            "ep_earnings_c": _ep_e_c_hold,
            "ep_news": _ep_n_hold,
            "ep_news_a": _ep_n_hold,
            "ep_news_b": _ep_n_hold,
            "ep_news_c": _ep_n_c_hold,
            "ep_breakout": int(cfg.get("ep_breakout_max_hold_days", 50)),
        }
        default_max_hold = 50

        for pos in positions:
            max_hold_days = max_hold_map.get(pos.setup_type, default_max_hold)
            if pos.days_held < max_hold_days:
                continue
            closes = daily_closes_map.get(pos.ticker)
            current_price = closes[-1] if closes else pos.entry_price
            logger.info(
                "Max hold exit %s: %d days held >= %d max (%s)",
                pos.ticker, pos.days_held, max_hold_days, pos.setup_type,
            )
            self._close_position(session, pos, current_price, reason="max_hold_period")

    def _check_ma_close_exits(self, session, positions, daily_closes_map):
        """Exit positions where today's close is below the trailing MA.

        Gated against EP swing setups (ep_earnings*, ep_news*): they use a
        fixed-percentage stop trail (entry × 1.05) placed in
        _fire_ep_time_partial, NOT an MA-based trail. Without this gate, an
        EP position whose partial fires would also become MA-trail-eligible
        and exit prematurely, defeating the "hold remainder to D49" rule.

        ep_breakout is the EXCEPTION: the close-below-MA10 exit IS its
        validated runner exit and is active from day 1 (no partial-exit
        precondition) with its own MA period. See
        strategies/ep_breakout/README.md.
        """
        from signals.base import compute_sma

        for pos in positions:
            if pos.setup_type == "ep_breakout":
                ma_period = self.ep_bo_trail_ma_days
            else:
                if not pos.partial_exit_done:
                    continue  # only trail after partial exit
                if pos.setup_type.startswith(("ep_earnings", "ep_news")):
                    continue  # EP setups use fixed-percentage trail, not MA
                ma_period = self.trailing_ma_period
            closes = daily_closes_map.get(pos.ticker)
            if not closes or len(closes) < ma_period:
                continue
            ma = compute_sma(closes, ma_period)
            if ma is None:
                continue
            todays_close = closes[-1]
            if pos.side == "long" and todays_close < ma:
                logger.info(
                    "MA close exit %s: close %.2f < MA%d %.2f",
                    pos.ticker, todays_close, ma_period, ma,
                )
                self._close_position(session, pos, todays_close, reason="trailing_ma_close")
            elif pos.side == "short" and todays_close > ma:
                logger.info(
                    "MA close exit %s: close %.2f > MA%d %.2f",
                    pos.ticker, todays_close, ma_period, ma,
                )
                self._close_position(session, pos, todays_close, reason="trailing_ma_close")

    def _check_ep_breakout_breakeven(self, session, positions, daily_closes_map):
        """EP breakout: after today's close >= entry × (1 + trigger/100),
        raise the GTC stop to entry (breakeven lock). One-shot by
        construction: once stop >= entry the condition can't re-fire.

        Broker-first like _update_trailing_stop — DB only commits if the
        broker accepted the modification (trade-path rule)."""
        for pos in positions:
            if pos.setup_type != "ep_breakout" or pos.side != "long":
                continue
            if pos.stop_price >= pos.entry_price:
                continue  # already at/above breakeven
            closes = daily_closes_map.get(pos.ticker)
            if not closes:
                continue
            trigger = pos.entry_price * (1 + self.ep_bo_breakeven_trigger_pct / 100)
            if closes[-1] < trigger:
                continue

            new_stop = round(pos.entry_price, 2)
            old_stop = pos.stop_price
            if pos.stop_order_id:
                try:
                    self.client.modify_stop_order(pos.stop_order_id, new_stop)
                except Exception as e:
                    logger.error(
                        "CRITICAL: EP breakout breakeven move FAILED for %s: %s. "
                        "DB stop NOT updated; stop remains %.2f.",
                        pos.ticker, e, old_stop,
                    )
                    self.notify(
                        f"🚨 CRITICAL: breakeven stop move FAILED for {pos.ticker}\n"
                        f"Stop remains ${old_stop:.2f} (wanted ${new_stop:.2f}). "
                        f"Check broker manually."
                    )
                    continue
            pos.stop_price = new_stop
            session.commit()
            self.notify(
                f"EP BREAKOUT BREAKEVEN: {pos.ticker}\n"
                f"Close ${closes[-1]:.2f} >= +{self.ep_bo_breakeven_trigger_pct:.0f}% "
                f"trigger — stop ${old_stop:.2f} → ${new_stop:.2f} (entry)"
            )

    def _update_trailing_stop(
        self, session, pos: Position, daily_closes: list[float]
    ):
        from risk.manager import RiskManager
        from signals.base import compute_sma

        ma = compute_sma(daily_closes, self.trailing_ma_period)
        if ma is None:
            return

        new_stop = RiskManager.compute_trailing_stop(ma, pos.stop_price, pos.side)
        if new_stop == pos.stop_price:
            return

        logger.info(
            "Trailing stop update %s: %.2f → %.2f (MA%d=%.2f)",
            pos.ticker, pos.stop_price, new_stop, self.trailing_ma_period, ma,
        )
        old_stop = pos.stop_price

        # Update BROKER FIRST — only commit to DB if broker accepts the change
        if pos.stop_order_id:
            try:
                self.client.modify_stop_order(pos.stop_order_id, new_stop)
            except Exception as e:
                logger.error(
                    "CRITICAL: Failed to update broker trailing stop for %s: %s. "
                    "DB stop NOT updated to keep consistency. Stop remains %.2f.",
                    pos.ticker, e, old_stop,
                )
                self.notify(
                    f"🚨 CRITICAL: Trailing stop update FAILED for {pos.ticker}\n"
                    f"Stop remains at ${old_stop:.2f} (not updated to ${new_stop:.2f}).\n"
                    f"Check broker manually."
                )
                return  # Do NOT update DB — keep it in sync with broker

        pos.stop_price = new_stop
        session.commit()

        self.notify(
            f"TRAILING STOP UPDATE: {pos.ticker}\n"
            f"New stop: ${new_stop:.2f} (was ${old_stop:.2f})\n"
            f"MA{self.trailing_ma_period}={ma:.2f}"
        )

    # ------------------------------------------------------------------
    # EP swing strategies — time-based partial profit
    #
    # Added 2026-05-11 to address the "can't hold 50 days" capital pressure
    # on the keeper EP strategies (Earnings B, News A, News B). Snapshot
    # backtest on corrected 2020-2026 Spikeet data showed this rule lifts
    # PF 3.46→3.86, WR 48%→61%, and ret/sd (capital efficiency) by +20%
    # vs the bare -7%/D49 baseline.
    #
    # Rule: at day 19+ of trade, if position is in profit (current price >
    # entry price), scale out 40% via market order and move the stop on
    # the remaining 60% to entry × 1.05 (5% above entry, guaranteeing a
    # locked-in profit on the remainder).
    #
    # Scheduled at 9:40 AM ET (10 min after open, avoids opening volatility,
    # leaves ~6h of market time for retries if any single order fails). NOT
    # in run_eod_tasks because the 3:55 PM EOD window is too tight (only 5
    # min to close).
    # ------------------------------------------------------------------

    def check_ep_time_partial(self) -> str:
        """Fire EP D19+ partial profit for any qualifying open positions.

        Scheduled at 9:40 AM ET on weekdays via main.py / main_ib.py.
        Single-shot per position per day; if a position is not yet at
        day 19 (or not in profit today), the next morning's run picks
        it up.

        Returns a short summary string for _track_job logging.
        """
        if not self.client.is_market_open():
            logger.info("EP time partial check skipped — market not open")
            return "Skipped — market not open"

        with get_session(self.engine) as session:
            candidates = (
                session.query(Position)
                .filter(Position.is_open == True)
                .filter(Position.partial_exit_done == False)
                .filter(Position.partial_exit_order_id == None)
                .all()
            )
            candidates = [
                p for p in candidates
                if p.setup_type.startswith(("ep_earnings", "ep_news"))
                and p.days_held >= self.ep_time_partial_day
            ]

            if not candidates:
                logger.info("EP time partial check: 0 candidates")
                return "0 candidates"

            logger.info("EP time partial check: %d candidates", len(candidates))
            fired = []
            skipped_not_in_profit = []
            failures: list[tuple[str, str]] = []

            for pos in candidates:
                try:
                    current_price = self._fetch_current_price_for_partial(pos.ticker)
                except Exception as e:
                    logger.warning(
                        "EP partial: price fetch failed for %s: %s — skipping",
                        pos.ticker, e,
                    )
                    failures.append((pos.ticker, f"price fetch failed: {e}"))
                    continue

                if current_price <= pos.entry_price:
                    skipped_not_in_profit.append(pos.ticker)
                    continue

                try:
                    self._fire_ep_time_partial(session, pos, current_price)
                    fired.append(pos.ticker)
                except Exception as e:
                    # _fire already notified + logged; record + continue so
                    # one bad ticker doesn't block the others.
                    failures.append((pos.ticker, str(e)))

            # Build summary
            parts = []
            if fired:
                parts.append(f"fired={','.join(fired)}")
            if skipped_not_in_profit:
                parts.append(f"not_in_profit={','.join(skipped_not_in_profit)}")
            if failures:
                parts.append(f"failures={len(failures)}")
                # Raise at the end if ALL attempts failed — signal systemic issue
                if len(failures) == len(candidates):
                    raise RuntimeError(
                        f"EP time partial: ALL {len(candidates)} attempts failed: "
                        + "; ".join(f"{t}: {e}" for t, e in failures)
                    )
            return " | ".join(parts) if parts else "0 fired"

    def _fetch_current_price_for_partial(self, ticker: str) -> float:
        """Get the broker's current price for partial sizing. Used at 9:40 AM
        so the broker is open + bars are streaming. Prefers latest trade /
        snapshot; falls back to get_latest_price if the client doesn't expose
        a snapshot method."""
        # Most clients have get_realtime_quote (returns dict with last_price)
        if hasattr(self.client, "get_realtime_quote"):
            quote = self.client.get_realtime_quote(ticker)
            last = quote.get("last_price") or quote.get("last") or 0
            if last and last > 0:
                return float(last)
        # Fallback
        if hasattr(self.client, "get_latest_price"):
            return float(self.client.get_latest_price(ticker))
        raise RuntimeError(
            f"Client has no get_realtime_quote or get_latest_price; "
            f"cannot fetch price for {ticker}"
        )

    def _fire_ep_time_partial(self, session, pos: Position, current_price: float):
        """EP D19 time partial: sell `ep_time_partial_fraction`, move the stop
        on the remainder to entry × (1 + ep_time_partial_new_stop_pct/100).
        Delegates to the shared 4-step sequence."""
        new_stop_price = round(
            pos.entry_price * (1 + self.ep_time_partial_new_stop_pct / 100), 2
        )
        self._fire_partial_and_replace_stop(
            session, pos, current_price,
            fraction=self.ep_time_partial_fraction,
            new_stop_price=new_stop_price,
            label="EP PARTIAL",
        )

    def _fire_partial_and_replace_stop(
        self, session, pos: Position, current_price: float,
        fraction: float, new_stop_price: float, label: str,
    ):
        """Execute the 4-step partial-profit sequence for one position.

        Steps:
          1. Cancel existing GTC stop order (frees broker-held shares so
             the partial sell can fill on Alpaca, which holds shares for
             open sell stops).
          2. Submit market sell for `int(shares × fraction)` via the
             broker's close_position. Goes through the safety pre-check
             added 2026-05-11 (refuses if broker doesn't actually have
             the long position).
          3. Wait briefly for the order to fill. If filled, mark DB. If
             pending, record state and return — next 9:40 AM run will
             see partial_exit_order_id and either confirm fill or retry.
          4. Place new GTC stop for the remainder at `new_stop_price`.
             Up to 3 retries with 3s backoff because this is the critical step
             — if it fails, the position becomes naked. After 3 fails, raise
             RuntimeError so _track_job fires JOB FAILED; the drift detector
             (reconcile_positions every 5 min) catches the naked state.
        """
        import time as _time

        partial_qty = max(1, int(pos.shares * fraction))
        remaining = pos.shares - partial_qty
        if remaining <= 0:
            logger.warning(
                "EP partial: %s would close ALL shares (qty=%d, partial=%d) — skipping",
                pos.ticker, pos.shares, partial_qty,
            )
            return

        # 1. Cancel existing stop
        old_stop_id = pos.stop_order_id
        old_stop_price = pos.stop_price
        if old_stop_id:
            try:
                self.client.cancel_order(old_stop_id)
                _time.sleep(2.0)  # let cancel propagate
            except Exception as e:
                logger.warning(
                    "EP partial: pre-cancel of stop %s failed for %s: %s (continuing)",
                    old_stop_id, pos.ticker, e,
                )

        # 2. Market sell the partial
        try:
            partial_order_id = self.client.close_position(
                pos.ticker, partial_qty, pos.side,
            )
        except Exception as e:
            # Restore the original stop so we're not naked, then raise.
            logger.error(
                "EP partial: market sell failed for %s: %s — attempting to restore stop",
                pos.ticker, e,
            )
            self._restore_stop_after_partial_failure(session, pos, old_stop_price)
            raise RuntimeError(f"EP partial sell failed for {pos.ticker}: {e}")

        # 3. Wait briefly for fill
        filled = self._wait_for_partial_fill(partial_order_id, timeout_s=15)
        if not filled:
            # Order is pending. Record state — next 9:40 run sees
            # partial_exit_order_id set and will either confirm-fill (via
            # the existing _check_pending_partial_exit path, which we
            # also need to invoke from check_ep_time_partial) or skip.
            pos.partial_exit_order_id = partial_order_id
            pos.partial_exit_shares = partial_qty
            pos.partial_exit_price = current_price
            session.commit()
            self.notify(
                f"{label} PLACED (pending fill): {pos.ticker} {partial_qty}sh "
                f"@ ~${current_price:.2f} — order {partial_order_id}. "
                f"Will reconcile next cycle."
            )
            return

        # Filled. Mark DB.
        pos.partial_exit_done = True
        pos.partial_exit_shares = partial_qty
        pos.partial_exit_price = current_price
        pos.partial_exit_order_id = None
        session.commit()

        # 4. Place new GTC stop for the remainder, with retries
        new_stop_id = None
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                new_stop_id = self.client.place_stop_order(
                    pos.ticker, remaining, "sell", new_stop_price,
                )
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    "EP partial: stop placement attempt %d/3 failed for %s: %s",
                    attempt, pos.ticker, e,
                )
                _time.sleep(3.0)

        if new_stop_id is None:
            msg = (
                f"🚨 CRITICAL: {pos.ticker} {label} filled ({partial_qty}sh sold) "
                f"but new stop placement FAILED after 3 attempts. "
                f"{remaining} shares UNPROTECTED at broker. Last error: {last_err}"
            )
            logger.error(msg)
            self.notify(msg)
            # Don't restore old stop — partial already filled, original size no
            # longer applies. Leave naked + alert. Drift detector picks it up
            # within 5 min.
            raise RuntimeError(msg)

        pos.stop_order_id = new_stop_id
        pos.stop_price = new_stop_price
        session.commit()

        self.notify(
            f"{label}: {pos.ticker} ({pos.setup_type})\n"
            f"Sold {partial_qty}/{pos.shares} shares @ ~${current_price:.2f} "
            f"on day {pos.days_held}\n"
            f"New stop @ ${new_stop_price:.2f} "
            f"for remaining {remaining} shares"
        )

    # ------------------------------------------------------------------
    # EP Breakout — +30% price-target partial (EP 2.0 Track A, 2026-07-05)
    #
    # Scheduled at 9:40 AM ET via the ep_breakout_partial_check job
    # (strategies/ep_breakout/plugin.py). Sells profit_target_fraction of
    # the position once price >= entry × (1 + profit_target_pct/100) and
    # moves the remainder's stop to max(current stop, entry) — by the time
    # +30% prints, the +15% breakeven trigger has effectively been earned;
    # locking entry here is marginally more conservative than the
    # backtest's next-bar breakeven and never looser.
    # ------------------------------------------------------------------

    def check_ep_breakout_target_partial(self) -> str:
        """Fire the +30% target partial for qualifying ep_breakout positions.
        Same skeleton as check_ep_time_partial: single-shot per position,
        per-ticker failures don't block the batch, all-fail raises."""
        if not self.client.is_market_open():
            logger.info("EP breakout partial check skipped — market not open")
            return "Skipped — market not open"

        with get_session(self.engine) as session:
            candidates = (
                session.query(Position)
                .filter(Position.is_open == True)   # noqa: E712
                .filter(Position.partial_exit_done == False)   # noqa: E712
                .filter(Position.partial_exit_order_id == None)  # noqa: E711
                .filter(Position.setup_type == "ep_breakout")
                .all()
            )
            if not candidates:
                logger.info("EP breakout partial check: 0 candidates")
                return "0 candidates"

            fired, below_target = [], []
            failures: list[tuple[str, str]] = []
            for pos in candidates:
                try:
                    current_price = self._fetch_current_price_for_partial(pos.ticker)
                except Exception as e:
                    logger.warning(
                        "EP breakout partial: price fetch failed for %s: %s — skipping",
                        pos.ticker, e,
                    )
                    failures.append((pos.ticker, f"price fetch failed: {e}"))
                    continue

                target = pos.entry_price * (1 + self.ep_bo_target_pct / 100)
                if current_price < target:
                    below_target.append(pos.ticker)
                    continue

                try:
                    new_stop = round(max(pos.stop_price, pos.entry_price), 2)
                    self._fire_partial_and_replace_stop(
                        session, pos, current_price,
                        fraction=self.ep_bo_target_fraction,
                        new_stop_price=new_stop,
                        label="EP BREAKOUT TARGET",
                    )
                    fired.append(pos.ticker)
                except Exception as e:
                    failures.append((pos.ticker, str(e)))

            parts = []
            if fired:
                parts.append(f"fired={','.join(fired)}")
            if below_target:
                parts.append(f"below_target={len(below_target)}")
            if failures:
                parts.append(f"failures={len(failures)}")
                if len(failures) == len(candidates):
                    raise RuntimeError(
                        f"EP breakout partial: ALL {len(candidates)} attempts failed: "
                        + "; ".join(f"{t}: {e}" for t, e in failures)
                    )
            return " | ".join(parts) if parts else "0 fired"

    def _wait_for_partial_fill(self, order_id: str, timeout_s: int = 15) -> bool:
        """Poll get_order_status every 1s up to timeout_s. Returns True if
        the order reaches 'filled' status, False otherwise (timeout / other
        terminal state). Caller handles the False case (treat as pending,
        retry later)."""
        import time as _time
        deadline = _time.time() + timeout_s
        while _time.time() < deadline:
            try:
                status = self.client.get_order_status(order_id)
            except Exception as e:
                logger.warning("get_order_status(%s) failed: %s", order_id, e)
                _time.sleep(1.0)
                continue
            s = status.get("status", "")
            if s == "filled":
                return True
            if s in ("cancelled", "rejected", "expired"):
                logger.warning("Partial order %s reached terminal status %s", order_id, s)
                return False
            _time.sleep(1.0)
        return False

    def _restore_stop_after_partial_failure(
        self, session, pos: Position, old_stop_price: float,
    ) -> None:
        """Re-place the original stop for the full position size after a
        partial-sell attempt failed. Called only when we cancelled the
        original stop but the subsequent market-sell failed; the position
        is unchanged in size, so we want the original protection back."""
        try:
            new_stop_id = self.client.place_stop_order(
                pos.ticker, pos.shares, "sell", old_stop_price,
            )
            pos.stop_order_id = new_stop_id
            pos.stop_price = old_stop_price
            session.commit()
            logger.info(
                "EP partial recovery: restored stop %.2f for %s (%d sh)",
                old_stop_price, pos.ticker, pos.shares,
            )
        except Exception as e:
            msg = (
                f"🚨 CRITICAL: {pos.ticker} EP partial-sell failed AND stop-restore "
                f"also failed: {e}. Position {pos.shares}sh UNPROTECTED."
            )
            logger.error(msg)
            self.notify(msg)

    # ------------------------------------------------------------------
    # EOD P&L summary
    # ------------------------------------------------------------------

    def compute_daily_pnl(
        self,
        portfolio_value: float,
        current_prices: dict[str, float] | None = None,
    ) -> DailyPnl:
        """Compute and persist today's P&L summary."""
        today = datetime.utcnow().date()
        with get_session(self.engine) as session:
            # Realized P&L from positions closed today
            closed_today = (
                session.query(Position)
                .filter(
                    Position.is_open == False,
                    Position.closed_at >= datetime.combine(today, datetime.min.time()),
                )
                .all()
            )
            realized = sum(p.realized_pnl or 0.0 for p in closed_today)

            # Unrealized from open positions using current prices
            open_positions = session.query(Position).filter_by(is_open=True).all()
            unrealized = 0.0
            if current_prices:
                for pos in open_positions:
                    price = current_prices.get(pos.ticker)
                    if price is not None:
                        unrealized += pos.unrealized_pnl(price)

            daily = DailyPnl(
                trade_date=today,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                total_pnl=realized + unrealized,
                portfolio_value=portfolio_value,
                num_trades=len(closed_today),
                num_winners=sum(1 for p in closed_today if (p.realized_pnl or 0) > 0),
                num_losers=sum(1 for p in closed_today if (p.realized_pnl or 0) < 0),
            )
            session.merge(daily)
            session.commit()
            return daily

    # ------------------------------------------------------------------
    # Halt controls
    # ------------------------------------------------------------------

    def set_daily_halt(self, halted: bool):
        self._daily_halt = halted
        if halted:
            logger.warning("Daily halt ACTIVATED")

    def set_weekly_halt(self, halted: bool):
        self._weekly_halt = halted
        if halted:
            logger.warning("Weekly halt ACTIVATED")

    @property
    def is_halted(self) -> bool:
        return self._daily_halt or self._weekly_halt
