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
    ):
        self.config = config
        self.engine = db_engine
        self.client = broker_client
        self.notify = notify or (lambda msg: None)
        self.risk = RiskManager(config)

        exits = config["exits"]
        self.partial_exit_after_days: int = int(exits["partial_exit_after_days"])
        self.partial_exit_gain_pct: float = float(exits["partial_exit_gain_threshold_pct"])
        self.partial_exit_fraction: float = float(exits["partial_exit_fraction"])
        self.trailing_ma_period: int = int(exits["trailing_ma_period"])

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

        # 3. Parabolic short: check profit targets at 10d/20d MA
        if pos.setup_type == "parabolic_short" and pos.side == "short":
            if self._check_parabolic_target(session, pos, current_price, daily_closes):
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

    def _check_parabolic_target(
        self, session, pos: Position, current_price: float, daily_closes: list[float]
    ) -> bool:
        """
        For parabolic short positions, cover at 10d/20d MA profit targets.
        Returns True if a target exit was triggered.
        """
        from signals.base import compute_sma

        if not daily_closes or len(daily_closes) < 20:
            return False

        ma10 = compute_sma(daily_closes, 10)
        ma20 = compute_sma(daily_closes, 20)

        # Cover half at 10d MA, rest at 20d MA
        if not pos.partial_exit_done and ma10 is not None and current_price <= ma10:
            logger.info(
                "Parabolic target: %s price %.2f <= 10d MA %.2f — partial cover",
                pos.ticker, current_price, ma10,
            )
            self._do_partial_exit(session, pos, current_price)
            return True

        if pos.partial_exit_done and ma20 is not None and current_price <= ma20:
            logger.info(
                "Parabolic target: %s price %.2f <= 20d MA %.2f — full cover",
                pos.ticker, current_price, ma20,
            )
            self._close_position(session, pos, current_price, reason="parabolic_target")
            return True

        return False

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
        - Checks for MA-close exits (daily close below trailing MA).
        - Updates trailing stops for remaining open positions.
        """
        with get_session(self.engine) as session:
            positions = session.query(Position).filter_by(is_open=True).all()

            # First: check if today's close is below the trailing MA
            # (only for positions that have already done a partial exit)
            self._check_ma_close_exits(session, positions, daily_closes_map)

            # Refresh the list — some positions may have been closed above
            positions = session.query(Position).filter_by(is_open=True).all()
            for pos in positions:
                closes = daily_closes_map.get(pos.ticker)
                if not closes:
                    continue
                self._update_trailing_stop(session, pos, closes)

    def _check_ma_close_exits(self, session, positions, daily_closes_map):
        """Exit positions where today's close is below the trailing MA."""
        from signals.base import compute_sma

        for pos in positions:
            if not pos.partial_exit_done:
                continue  # only trail after partial exit
            closes = daily_closes_map.get(pos.ticker)
            if not closes or len(closes) < self.trailing_ma_period:
                continue
            ma = compute_sma(closes, self.trailing_ma_period)
            if ma is None:
                continue
            todays_close = closes[-1]
            if pos.side == "long" and todays_close < ma:
                logger.info(
                    "MA close exit %s: close %.2f < MA%d %.2f",
                    pos.ticker, todays_close, self.trailing_ma_period, ma,
                )
                self._close_position(session, pos, todays_close, reason="trailing_ma_close")
            elif pos.side == "short" and todays_close > ma:
                logger.info(
                    "MA close exit %s: close %.2f > MA%d %.2f",
                    pos.ticker, todays_close, self.trailing_ma_period, ma,
                )
                self._close_position(session, pos, todays_close, reason="trailing_ma_close")

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
