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

        # 2. Check partial exit conditions
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
        remaining = pos.shares - shares_to_sell

        logger.info(
            "Partial exit %s: selling %d/%d shares @ %.2f",
            pos.ticker, shares_to_sell, pos.shares, current_price,
        )

        try:
            order_side = "sell" if pos.side == "long" else "buy_to_cover"
            self.client.place_limit_order(
                pos.ticker, order_side, shares_to_sell, current_price
            )
        except Exception as e:
            logger.error("Partial exit order failed for %s: %s", pos.ticker, e)
            return

        pos.partial_exit_done = True
        pos.partial_exit_shares = shares_to_sell
        pos.partial_exit_price = current_price

        # Move stop to break-even
        old_stop = pos.stop_price
        pos.stop_price = pos.entry_price
        session.commit()

        self.notify(
            f"PARTIAL EXIT: {pos.ticker} sold {shares_to_sell} shares @ ${current_price:.2f}\n"
            f"Stop moved to break-even: ${pos.entry_price:.2f} (was ${old_stop:.2f})\n"
            f"Remaining: {remaining} shares"
        )

        # Update broker stop order to break-even
        if pos.stop_order_id:
            try:
                self.client.modify_stop_order(pos.stop_order_id, pos.entry_price)
            except Exception as e:
                logger.warning("Failed to update stop order to break-even for %s: %s", pos.ticker, e)

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
        if pos.partial_exit_done and pos.partial_exit_price:
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
        - Updates trailing stops for all open positions.
        - Schedules positions to close if price has closed below trailing MA.
        """
        with get_session(self.engine) as session:
            positions = session.query(Position).filter_by(is_open=True).all()
            for pos in positions:
                closes = daily_closes_map.get(pos.ticker)
                if not closes:
                    continue
                self._update_trailing_stop(session, pos, closes)

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
        pos.stop_price = new_stop
        session.commit()

        # Update broker stop order
        if pos.stop_order_id:
            try:
                self.client.modify_stop_order(pos.stop_order_id, new_stop)
            except Exception as e:
                logger.warning("Failed to update broker stop for %s: %s", pos.ticker, e)

        self.notify(
            f"TRAILING STOP UPDATE: {pos.ticker}\n"
            f"New stop: ${new_stop:.2f} (was ${old_stop:.2f})\n"
            f"MA{self.trailing_ma_period}={ma:.2f}"
        )

    # ------------------------------------------------------------------
    # EOD P&L summary
    # ------------------------------------------------------------------

    def compute_daily_pnl(self, portfolio_value: float) -> DailyPnl:
        """Compute and persist today's P&L summary."""
        today = date.today()
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

            # Unrealized from open positions — requires current prices
            # (caller should pass prices; here we use entry as fallback)
            open_positions = session.query(Position).filter_by(is_open=True).all()
            unrealized = 0.0  # Updated by caller with live prices

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
