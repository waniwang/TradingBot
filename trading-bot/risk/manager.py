"""Risk manager: position sizing, exposure checks, daily/weekly loss limits."""

from __future__ import annotations

import logging
import math
from datetime import datetime, date

import pytz

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 35  # no entries before 9:35 AM ET


class RiskManager:
    """
    Stateless helper that evaluates risk rules.

    All checks return True if it is safe to proceed, False if blocked.
    """

    def __init__(self, config: dict):
        r = config["risk"]
        self.risk_per_trade_pct: float = float(r["risk_per_trade_pct"])
        self.max_positions: int = int(r["max_positions"])
        self.max_position_pct: float = float(r["max_position_pct"])
        self.daily_loss_limit_pct: float = float(r["daily_loss_limit_pct"])
        self.weekly_loss_limit_pct: float = float(r["weekly_loss_limit_pct"])

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        portfolio_value: float,
        entry_price: float,
        stop_price: float,
    ) -> int:
        """
        Return number of shares to buy/short given risk parameters.

        Formula: floor((portfolio * risk_pct%) / risk_per_share)
        Then capped at max_position_pct% of portfolio notional.
        """
        if entry_price <= 0 or stop_price <= 0:
            raise ValueError("entry_price and stop_price must be positive")

        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share == 0:
            raise ValueError("entry_price and stop_price cannot be equal")

        max_risk_dollars = portfolio_value * (self.risk_per_trade_pct / 100.0)
        raw_shares = math.floor(max_risk_dollars / risk_per_share)

        # Cap by max_position_pct
        max_notional = portfolio_value * (self.max_position_pct / 100.0)
        max_shares_by_notional = math.floor(max_notional / entry_price)

        shares = min(raw_shares, max_shares_by_notional)

        logger.debug(
            "Position size: portfolio=%.0f entry=%.2f stop=%.2f "
            "risk/share=%.2f max_risk=$%.0f raw=%d capped=%d",
            portfolio_value, entry_price, stop_price,
            risk_per_share, max_risk_dollars, raw_shares, shares,
        )
        return max(shares, 0)

    # ------------------------------------------------------------------
    # Pre-entry exposure checks
    # ------------------------------------------------------------------

    def check_max_positions(self, open_position_count: int) -> bool:
        """True if we can open another position."""
        ok = open_position_count < self.max_positions
        if not ok:
            logger.info(
                "Max positions reached (%d/%d) — skipping entry",
                open_position_count, self.max_positions,
            )
        return ok

    def check_position_notional(
        self, shares: int, entry_price: float, portfolio_value: float
    ) -> bool:
        """True if the new position notional is within the single-position cap."""
        notional = shares * entry_price
        pct = notional / portfolio_value * 100
        ok = pct <= self.max_position_pct
        if not ok:
            logger.info(
                "Position notional %.1f%% exceeds cap %.1f%% — will cap shares",
                pct, self.max_position_pct,
            )
        return ok

    def check_trading_window(self) -> bool:
        """True if current ET time is past 9:35 AM (no entries in first 5 min)."""
        now_et = datetime.now(ET)
        boundary = now_et.replace(
            hour=MARKET_OPEN_HOUR,
            minute=MARKET_OPEN_MINUTE,
            second=0,
            microsecond=0,
        )
        ok = now_et >= boundary
        if not ok:
            logger.info(
                "Too early to trade — current ET time %s, wait until 9:35 AM",
                now_et.strftime("%H:%M:%S"),
            )
        return ok

    # ------------------------------------------------------------------
    # Loss limit checks
    # ------------------------------------------------------------------

    def check_daily_loss(
        self, daily_pnl: float, portfolio_value: float
    ) -> bool:
        """True if daily loss is within the allowed limit."""
        loss_pct = daily_pnl / portfolio_value * 100  # negative if losing
        ok = loss_pct > -self.daily_loss_limit_pct
        if not ok:
            logger.warning(
                "Daily loss limit hit: %.2f%% (limit=%.2f%%) — halting trading",
                loss_pct, self.daily_loss_limit_pct,
            )
        return ok

    def check_weekly_loss(
        self, weekly_pnl: float, portfolio_value: float
    ) -> bool:
        """True if weekly loss is within the allowed limit."""
        loss_pct = weekly_pnl / portfolio_value * 100
        ok = loss_pct > -self.weekly_loss_limit_pct
        if not ok:
            logger.warning(
                "Weekly loss limit hit: %.2f%% (limit=%.2f%%) — halting for the week",
                loss_pct, self.weekly_loss_limit_pct,
            )
        return ok

    # ------------------------------------------------------------------
    # Composite gate — call before any entry
    # ------------------------------------------------------------------

    def can_enter(
        self,
        open_position_count: int,
        daily_pnl: float,
        weekly_pnl: float,
        portfolio_value: float,
    ) -> tuple[bool, str]:
        """
        Run all pre-entry checks in order.

        Returns (ok, reason_if_blocked).
        """
        if not self.check_trading_window():
            return False, "before_trading_window"
        if not self.check_daily_loss(daily_pnl, portfolio_value):
            return False, "daily_loss_limit"
        if not self.check_weekly_loss(weekly_pnl, portfolio_value):
            return False, "weekly_loss_limit"
        if not self.check_max_positions(open_position_count):
            return False, "max_positions"
        return True, ""

    # ------------------------------------------------------------------
    # Stop management helpers
    # ------------------------------------------------------------------

    @staticmethod
    def tighten_stop(current_stop: float, new_stop: float, side: str) -> float:
        """
        Return the tighter of the two stop levels — never widen a stop.

        For longs: higher stop = tighter. For shorts: lower stop = tighter.
        """
        if side == "long":
            return max(current_stop, new_stop)
        else:
            return min(current_stop, new_stop)

    @staticmethod
    def compute_trailing_stop(
        ma_value: float,
        current_stop: float,
        side: str,
    ) -> float:
        """
        Update trailing stop to the MA level, but never loosen it.
        """
        if side == "long":
            return max(current_stop, ma_value)
        else:
            return min(current_stop, ma_value)
