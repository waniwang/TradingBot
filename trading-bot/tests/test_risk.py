"""
Unit tests for the risk manager.
"""

import pytest
from unittest.mock import patch
from datetime import datetime

import pytz

from risk.manager import RiskManager

ET = pytz.timezone("America/New_York")


@pytest.fixture
def config():
    return {
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_positions": 4,
            "max_position_pct": 10.0,
            "daily_loss_limit_pct": 3.0,
            "weekly_loss_limit_pct": 5.0,
        }
    }


@pytest.fixture
def rm(config):
    return RiskManager(config)


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

class TestCalculatePositionSize:
    def test_basic_formula(self, rm):
        # $100k portfolio, 1% risk = $1000 max risk
        # entry=$10, stop=$6.50 → risk/share=$3.50
        # shares = floor(1000 / 3.50) = 285
        # notional = 285 × $10 = $2,850 = 2.85% — well under 10% cap
        shares = rm.calculate_position_size(100_000, 10.00, 6.50)
        assert shares == 285

    def test_capped_by_notional(self, rm):
        # entry=$52, stop=$48.50 → 285 shares × $52 = $14,820 (14.8% of 100k)
        # Cap at 10% = $10,000 / $52 = 192 shares
        shares = rm.calculate_position_size(100_000, 52.00, 48.50)
        max_by_notional = int(100_000 * 0.10 / 52.00)
        # Raw = 285, capped = 192
        assert shares == min(285, max_by_notional)

    def test_tight_stop_more_shares_but_capped(self, rm):
        # entry=$100, stop=$99 → risk/share=$1 → 1000 raw shares
        # But 1000 × $100 = $100k = 100% notional → capped to 10% = 100 shares
        shares = rm.calculate_position_size(100_000, 100.00, 99.00)
        assert shares == 100

    def test_wide_stop_fewer_shares(self, rm):
        # entry=$100, stop=$80 → risk/share=$20 → floor(1000/20) = 50 shares
        # 50 × $100 = $5,000 = 5% (under 10% cap)
        shares = rm.calculate_position_size(100_000, 100.00, 80.00)
        assert shares == 50

    def test_short_position_sizing(self, rm):
        # Same formula — entry > stop for shorts as well (entry=50, stop=55)
        shares = rm.calculate_position_size(100_000, 50.00, 55.00)
        # risk/share = 5.00, max_risk = $1000, raw = 200
        # 200 × $50 = $10,000 = 10% exactly
        assert shares == 200

    def test_returns_zero_when_no_risk(self, rm):
        with pytest.raises(ValueError):
            rm.calculate_position_size(100_000, 50.00, 50.00)

    def test_raises_on_zero_price(self, rm):
        with pytest.raises(ValueError):
            rm.calculate_position_size(100_000, 0.0, 48.50)

    def test_scales_with_portfolio(self, rm):
        shares_100k = rm.calculate_position_size(100_000, 50.00, 47.00)
        shares_200k = rm.calculate_position_size(200_000, 50.00, 47.00)
        assert shares_200k >= shares_100k  # larger portfolio → more shares (or capped)


# ---------------------------------------------------------------------------
# Max positions check
# ---------------------------------------------------------------------------

class TestCheckMaxPositions:
    def test_passes_when_under_limit(self, rm):
        assert rm.check_max_positions(3) is True

    def test_passes_when_at_zero(self, rm):
        assert rm.check_max_positions(0) is True

    def test_blocks_when_at_limit(self, rm):
        assert rm.check_max_positions(4) is False

    def test_blocks_when_over_limit(self, rm):
        assert rm.check_max_positions(5) is False


# ---------------------------------------------------------------------------
# Daily / weekly loss checks
# ---------------------------------------------------------------------------

class TestCheckDailyLoss:
    def test_passes_when_loss_under_limit(self, rm):
        # -$2,500 on $100k = -2.5%, limit is 3%
        assert rm.check_daily_loss(-2_500, 100_000) is True

    def test_passes_when_profitable(self, rm):
        assert rm.check_daily_loss(1_000, 100_000) is True

    def test_blocks_when_at_exact_limit(self, rm):
        # exactly -3% should block
        assert rm.check_daily_loss(-3_000, 100_000) is False

    def test_blocks_when_over_limit(self, rm):
        assert rm.check_daily_loss(-3_100, 100_000) is False


class TestCheckWeeklyLoss:
    def test_passes_when_loss_under_limit(self, rm):
        # -$4,000 on $100k = -4%, limit is 5%
        assert rm.check_weekly_loss(-4_000, 100_000) is True

    def test_blocks_when_over_limit(self, rm):
        assert rm.check_weekly_loss(-5_100, 100_000) is False


# ---------------------------------------------------------------------------
# Trading window check
# ---------------------------------------------------------------------------

class TestTradingWindow:
    def test_passes_after_935(self, rm):
        # 9:40 AM ET
        fake_time = ET.localize(datetime.now().replace(hour=9, minute=40))
        with patch("risk.manager.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            assert rm.check_trading_window() is True

    def test_blocks_before_935(self, rm):
        fake_time = ET.localize(datetime.now().replace(hour=9, minute=32))
        with patch("risk.manager.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            assert rm.check_trading_window() is False

    def test_passes_at_exactly_935(self, rm):
        fake_time = ET.localize(datetime.now().replace(hour=9, minute=35, second=0))
        with patch("risk.manager.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            assert rm.check_trading_window() is True


# ---------------------------------------------------------------------------
# Stop management helpers
# ---------------------------------------------------------------------------

class TestTightenStop:
    def test_long_tightens_upward(self):
        # For long, higher stop = tighter
        result = RiskManager.tighten_stop(48.00, 50.00, "long")
        assert result == 50.00

    def test_long_does_not_loosen(self):
        result = RiskManager.tighten_stop(50.00, 48.00, "long")
        assert result == 50.00  # keeps existing tighter stop

    def test_short_tightens_downward(self):
        # For short, lower stop = tighter
        result = RiskManager.tighten_stop(55.00, 53.00, "short")
        assert result == 53.00

    def test_short_does_not_loosen(self):
        result = RiskManager.tighten_stop(53.00, 55.00, "short")
        assert result == 53.00


class TestComputeTrailingStop:
    def test_long_raises_stop_to_ma(self):
        # MA moved up — trailing stop moves up
        result = RiskManager.compute_trailing_stop(52.0, 48.0, "long")
        assert result == 52.0

    def test_long_keeps_stop_if_ma_lower(self):
        # MA hasn't risen above current stop — keep stop
        result = RiskManager.compute_trailing_stop(46.0, 48.0, "long")
        assert result == 48.0

    def test_short_lowers_stop_to_ma(self):
        result = RiskManager.compute_trailing_stop(48.0, 52.0, "short")
        assert result == 48.0

    def test_short_keeps_stop_if_ma_higher(self):
        result = RiskManager.compute_trailing_stop(55.0, 52.0, "short")
        assert result == 52.0


# ---------------------------------------------------------------------------
# Composite can_enter gate
# ---------------------------------------------------------------------------

class TestCanEnter:
    def test_passes_all_checks(self, rm):
        fake_time = ET.localize(datetime.now().replace(hour=10, minute=0))
        with patch("risk.manager.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            ok, reason = rm.can_enter(
                open_position_count=2,
                daily_pnl=-1_000,
                weekly_pnl=-2_000,
                portfolio_value=100_000,
            )
        assert ok is True
        assert reason == ""

    def test_blocked_by_max_positions(self, rm):
        fake_time = ET.localize(datetime.now().replace(hour=10, minute=0))
        with patch("risk.manager.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            ok, reason = rm.can_enter(4, -500, -500, 100_000)
        assert ok is False
        assert reason == "max_positions"

    def test_blocked_by_daily_loss(self, rm):
        fake_time = ET.localize(datetime.now().replace(hour=10, minute=0))
        with patch("risk.manager.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            ok, reason = rm.can_enter(2, -3_500, -3_500, 100_000)
        assert ok is False
        assert reason == "daily_loss_limit"

    def test_blocked_by_trading_window(self, rm):
        fake_time = ET.localize(datetime.now().replace(hour=9, minute=31))
        with patch("risk.manager.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            ok, reason = rm.can_enter(0, 0, 0, 100_000)
        assert ok is False
        assert reason == "before_trading_window"


# ---------------------------------------------------------------------------
# Disabled-limit behavior (max_positions / daily_loss / weekly_loss == 0)
# ---------------------------------------------------------------------------

@pytest.fixture
def disabled_config():
    """All three position/loss limits disabled via the 0 sentinel."""
    return {
        "risk": {
            "risk_per_trade_pct": 0.3,
            "max_positions": 0,
            "max_position_pct": 15.0,
            "daily_loss_limit_pct": 0,
            "weekly_loss_limit_pct": 0,
        }
    }


class TestDisabledLimits:
    def test_max_positions_zero_allows_unlimited(self, disabled_config):
        rm = RiskManager(disabled_config)
        # Even with 100 already open, new entries must be allowed.
        assert rm.check_max_positions(100) is True
        assert rm.check_max_positions(0) is True

    def test_daily_loss_zero_allows_any_drawdown(self, disabled_config):
        rm = RiskManager(disabled_config)
        # Even a -50% day must not block.
        assert rm.check_daily_loss(-50_000, 100_000) is True

    def test_weekly_loss_zero_allows_any_drawdown(self, disabled_config):
        rm = RiskManager(disabled_config)
        assert rm.check_weekly_loss(-90_000, 100_000) is True

    def test_can_enter_passes_in_trading_window(self, disabled_config):
        """With all three disabled, can_enter only checks the trading
        window — no exposure or P&L gate fires."""
        rm = RiskManager(disabled_config)
        fake_time = ET.localize(datetime.now().replace(hour=10, minute=0))
        with patch("risk.manager.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            ok, reason = rm.can_enter(
                open_position_count=99,
                daily_pnl=-99_000,
                weekly_pnl=-99_000,
                portfolio_value=100_000,
            )
        assert ok is True, f"expected pass, got blocked: {reason}"
        assert reason == ""
