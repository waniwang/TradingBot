"""
Unit tests for signal modules.

Uses synthetic data — no network calls.
"""

import pytest
from signals import evaluate_signal, STRATEGY_REGISTRY
from signals.base import compute_orh, compute_orb_low, compute_vwap, compute_sma
from signals.breakout import check_breakout
from signals.episodic_pivot import check_episodic_pivot
from signals.parabolic_short import check_parabolic_short


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_candles(n: int, base_price: float = 50.0, step: float = 0.05) -> list[dict]:
    candles = []
    for i in range(n):
        p = base_price + i * step
        candles.append({
            "open": p,
            "high": p + 0.10,
            "low": p - 0.05,
            "close": p + 0.05,
            "volume": 50_000 + i * 1_000,
        })
    return candles


def make_daily_closes(n: int, start: float = 50.0, drift: float = 0.5) -> list[float]:
    return [round(start + i * drift, 2) for i in range(n)]


def make_daily_volumes(n: int, base: int = 1_000_000) -> list[int]:
    return [base + i * 1_000 for i in range(n)]


# ---------------------------------------------------------------------------
# Base helpers
# ---------------------------------------------------------------------------

class TestComputeOrh:
    def test_basic(self):
        candles = [
            {"high": 51.0, "low": 49.0, "close": 50.5},
            {"high": 52.0, "low": 50.0, "close": 51.0},
            {"high": 50.5, "low": 49.5, "close": 50.0},
            {"high": 51.5, "low": 50.5, "close": 51.0},
            {"high": 51.8, "low": 50.8, "close": 51.5},
        ]
        assert compute_orh(candles, n_minutes=5) == 52.0

    def test_single_candle(self):
        candles = [{"high": 100.0, "low": 95.0, "close": 98.0}]
        assert compute_orh(candles, n_minutes=1) == 100.0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            compute_orh([], n_minutes=5)

    def test_uses_only_first_n(self):
        candles = [
            {"high": 50.0, "low": 49.0, "close": 49.5},
            {"high": 51.0, "low": 50.0, "close": 50.5},
            {"high": 200.0, "low": 100.0, "close": 150.0},  # should be ignored
        ]
        assert compute_orh(candles, n_minutes=2) == 51.0


class TestComputeOrbLow:
    def test_basic(self):
        candles = [
            {"high": 52.0, "low": 49.5, "close": 51.0},
            {"high": 52.5, "low": 50.0, "close": 51.5},
            {"high": 51.0, "low": 50.5, "close": 50.8},
            {"high": 51.5, "low": 50.2, "close": 51.0},
            {"high": 51.8, "low": 50.3, "close": 51.5},
        ]
        assert compute_orb_low(candles, n_minutes=5) == 49.5


class TestComputeSma:
    def test_basic(self):
        closes = [10.0] * 20
        assert compute_sma(closes, 20) == 10.0

    def test_insufficient_data(self):
        assert compute_sma([10.0] * 5, 20) is None

    def test_trending(self):
        closes = list(range(1, 21))  # 1..20
        sma = compute_sma(closes, 20)
        assert sma == pytest.approx(10.5, rel=1e-3)


class TestComputeVwap:
    def test_basic(self):
        candles = [
            {"high": 51.0, "low": 49.0, "close": 50.0, "volume": 100_000},
            {"high": 52.0, "low": 50.0, "close": 51.0, "volume": 200_000},
        ]
        vwap = compute_vwap(candles)
        assert len(vwap) == 2
        assert vwap.iloc[-1] > 0

    def test_empty(self):
        vwap = compute_vwap([])
        assert vwap.empty


# ---------------------------------------------------------------------------
# Breakout signal
# ---------------------------------------------------------------------------

class TestBreakoutSignal:
    def _make_valid_inputs(self):
        """All conditions satisfied: price above ORH, above 20d MA, volume elevated."""
        candles_1m = make_candles(30, base_price=50.0, step=0.10)
        # ORH from first 5 candles: max high = 50.0 + 4*0.10 + 0.10 = 50.50
        orh = compute_orh(candles_1m, n_minutes=5)

        daily_closes = make_daily_closes(25, start=45.0, drift=0.2)
        daily_volumes = make_daily_volumes(25, base=1_000_000)

        current_price = orh + 0.20   # above ORH
        current_volume = 2_500_000   # > 1.5x avg of 1M

        return candles_1m, daily_closes, daily_volumes, current_price, current_volume

    def test_fires_when_all_conditions_met(self):
        candles_1m, daily_closes, daily_volumes, current_price, current_volume = (
            self._make_valid_inputs()
        )
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume
        )
        assert result is not None
        assert result.ticker == "AAPL"
        assert result.setup_type == "breakout"
        assert result.side == "long"
        assert result.entry_price == current_price
        assert result.stop_price < current_price

    def test_no_signal_when_price_below_orh(self):
        candles_1m, daily_closes, daily_volumes, current_price, current_volume = (
            self._make_valid_inputs()
        )
        orh = compute_orh(candles_1m, n_minutes=5)
        current_price = orh - 0.10  # below ORH
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume
        )
        assert result is None

    def test_no_signal_when_price_below_20d_ma(self):
        candles_1m, daily_closes, daily_volumes, current_price, current_volume = (
            self._make_valid_inputs()
        )
        # Make MA very high so price is below it
        daily_closes = make_daily_closes(25, start=100.0, drift=1.0)  # MA ~120, price ~50
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume
        )
        assert result is None

    def test_no_signal_when_volume_low(self):
        candles_1m, daily_closes, daily_volumes, current_price, current_volume = (
            self._make_valid_inputs()
        )
        current_volume = 500_000  # below 1.5x avg of 1M
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume
        )
        assert result is None

    def test_no_signal_when_not_enough_candles(self):
        candles_1m = make_candles(3)  # need at least 5
        daily_closes = make_daily_closes(25, 45.0)
        daily_volumes = make_daily_volumes(25)
        result = check_breakout("AAPL", candles_1m, daily_closes, daily_volumes, 50.0, 2_000_000)
        assert result is None

    def test_entry_price_set_correctly(self):
        candles_1m, daily_closes, daily_volumes, current_price, current_volume = (
            self._make_valid_inputs()
        )
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume
        )
        assert result is not None
        assert result.entry_price == current_price

    def test_stop_below_entry(self):
        candles_1m, daily_closes, daily_volumes, current_price, current_volume = (
            self._make_valid_inputs()
        )
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume
        )
        assert result is not None
        assert result.stop_price < result.entry_price


# ---------------------------------------------------------------------------
# EP signal
# ---------------------------------------------------------------------------

class TestEpisodicPivotSignal:
    def _make_valid_inputs(self):
        candles_1m = make_candles(30, base_price=115.0, step=0.15)
        orh = compute_orh(candles_1m, n_minutes=5)

        daily_volumes = make_daily_volumes(25, base=500_000)
        current_price = orh + 0.50
        current_volume = 2_000_000  # > 2x avg of 500k
        gap_pct = 15.0

        return candles_1m, daily_volumes, current_price, current_volume, gap_pct

    def test_fires_when_all_conditions_met(self):
        candles_1m, daily_volumes, current_price, current_volume, gap_pct = (
            self._make_valid_inputs()
        )
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct
        )
        assert result is not None
        assert result.setup_type == "episodic_pivot"
        assert result.side == "long"
        assert result.gap_pct == gap_pct

    def test_no_signal_when_gap_below_threshold(self):
        candles_1m, daily_volumes, current_price, current_volume, _ = (
            self._make_valid_inputs()
        )
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct=5.0  # below 10% threshold
        )
        assert result is None

    def test_no_signal_when_price_below_orh(self):
        candles_1m, daily_volumes, current_price, current_volume, gap_pct = (
            self._make_valid_inputs()
        )
        orh = compute_orh(candles_1m, n_minutes=5)
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            orh - 0.10, current_volume, gap_pct
        )
        assert result is None

    def test_no_signal_when_volume_insufficient(self):
        candles_1m, daily_volumes, current_price, _, gap_pct = (
            self._make_valid_inputs()
        )
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, 100_000, gap_pct  # low volume
        )
        assert result is None

    def test_stop_is_lod(self):
        candles_1m, daily_volumes, current_price, current_volume, gap_pct = (
            self._make_valid_inputs()
        )
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct
        )
        assert result is not None
        lod = min(c["low"] for c in candles_1m)
        assert result.stop_price == lod

    def test_config_overrides_gap_threshold(self):
        candles_1m, daily_volumes, current_price, current_volume, _ = (
            self._make_valid_inputs()
        )
        config = {"signals": {"ep_min_gap_pct": 20.0}}
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct=15.0,
            config=config
        )
        assert result is None  # gap 15% < new threshold 20%


# ---------------------------------------------------------------------------
# Parabolic Short signal
# ---------------------------------------------------------------------------

class TestParabolicShortSignal:
    def _make_valid_inputs(self):
        # Daily closes: stock tripled in 3 days — clearly parabolic (> 50% gain)
        # gain from closes[-4]=10.8 to recent_high of 18.0 = 66.7%
        daily_closes = [10.0, 10.5, 10.8, 16.0, 17.0, 18.0]

        # 1m candles: 25 candles at the parabolic peak (~18) with huge volume
        # → VWAP anchored near 18. Then 5 crash candles at ~13 (below ORB low).
        # current_price = 13.0, VWAP ≈ 17.8 → VWAP failure confirmed.
        candles_1m = []

        # High-volume anchor candles at peak (keep VWAP at ~18)
        for i in range(25):
            p = 18.0 + i * 0.01
            candles_1m.append({
                "open": p,
                "high": p + 0.15,
                "low": p - 0.05,   # ORB low from first 5: min_low ≈ 17.95
                "close": p,
                "volume": 1_000_000,
            })

        # Sharp drop candles — below ORB low, below VWAP
        for i in range(5):
            p = 13.0 - i * 0.10
            candles_1m.append({
                "open": p + 0.05,
                "high": p + 0.10,
                "low": p - 0.05,
                "close": p,
                "volume": 80_000,
            })

        orb_low = compute_orb_low(candles_1m, n_minutes=5)
        current_price = 12.9  # well below ORB low (~17.95) and well below VWAP (~18)

        return daily_closes, candles_1m, current_price

    def test_fires_when_all_conditions_met(self):
        daily_closes, candles_1m, current_price = self._make_valid_inputs()
        result = check_parabolic_short(
            "MEME", candles_1m, daily_closes, current_price, 100_000
        )
        assert result is not None
        assert result.setup_type == "parabolic_short"
        assert result.side == "short"

    def test_no_signal_when_gain_insufficient(self):
        daily_closes, candles_1m, current_price = self._make_valid_inputs()
        # Flat daily closes — no parabolic move
        flat_closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5]
        result = check_parabolic_short(
            "MEME", candles_1m, flat_closes, current_price, 100_000
        )
        assert result is None

    def test_no_signal_when_price_above_orb_low(self):
        daily_closes, candles_1m, _ = self._make_valid_inputs()
        orb_low = compute_orb_low(candles_1m, n_minutes=5)
        current_price = orb_low + 1.00  # clearly above ORB low
        result = check_parabolic_short(
            "MEME", candles_1m, daily_closes, current_price, 100_000
        )
        assert result is None

    def test_stop_is_day_high(self):
        daily_closes, candles_1m, current_price = self._make_valid_inputs()
        result = check_parabolic_short(
            "MEME", candles_1m, daily_closes, current_price, 100_000
        )
        assert result is not None
        day_high = max(c["high"] for c in candles_1m)
        assert result.stop_price == day_high

    def test_stop_above_entry_for_short(self):
        daily_closes, candles_1m, current_price = self._make_valid_inputs()
        result = check_parabolic_short(
            "MEME", candles_1m, daily_closes, current_price, 100_000
        )
        assert result is not None
        assert result.stop_price > result.entry_price

    def test_daily_highs_used_for_gain_calc(self):
        """When daily_highs are provided, the parabolic gain should use them."""
        daily_closes, candles_1m, current_price = self._make_valid_inputs()
        # daily_closes: [10.0, 10.5, 10.8, 16.0, 17.0, 18.0]
        # base_price = closes[-4] = 10.8
        # With higher daily_highs, the gain should still qualify
        daily_highs = [10.5, 11.0, 11.5, 17.0, 18.5, 19.5]
        result = check_parabolic_short(
            "MEME", candles_1m, daily_closes, current_price, 100_000,
            daily_highs=daily_highs,
        )
        assert result is not None
        assert result.setup_type == "parabolic_short"

    def test_daily_highs_boost_borderline_case(self):
        """A borderline case that fails with closes but passes with highs."""
        # daily_closes gain from base=10.8 to max(closes[-3:])=15.0 = 38.9% < 50%
        daily_closes = [10.0, 10.5, 10.8, 13.0, 14.0, 15.0]
        # But highs reach 17.0 → gain = (17.0 - 10.8) / 10.8 = 57.4% > 50%
        daily_highs = [10.5, 11.0, 11.5, 15.0, 16.0, 17.0]

        # Build candles to produce ORB low ~18, then crash to 12
        candles_1m = []
        for i in range(25):
            p = 18.0 + i * 0.01
            candles_1m.append({
                "open": p, "high": p + 0.15, "low": p - 0.05,
                "close": p, "volume": 1_000_000,
            })
        for i in range(5):
            p = 12.0 - i * 0.10
            candles_1m.append({
                "open": p + 0.05, "high": p + 0.10, "low": p - 0.05,
                "close": p, "volume": 80_000,
            })

        # Without daily_highs: should fail (38.9% < 50%)
        result_no_highs = check_parabolic_short(
            "MEME", candles_1m, daily_closes, 11.5, 100_000
        )
        assert result_no_highs is None

        # With daily_highs: should pass (57.4% > 50%)
        result_with_highs = check_parabolic_short(
            "MEME", candles_1m, daily_closes, 11.5, 100_000,
            daily_highs=daily_highs,
        )
        assert result_with_highs is not None


# ---------------------------------------------------------------------------
# Breakout with daily_lows
# ---------------------------------------------------------------------------

class TestBreakoutDailyLows:
    def _make_valid_inputs_with_lows(self):
        candles_1m = make_candles(30, base_price=50.0, step=0.10)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_closes = make_daily_closes(25, start=45.0, drift=0.2)
        daily_volumes = make_daily_volumes(25, base=1_000_000)
        daily_lows = [c - 1.0 for c in daily_closes]  # lows 1.0 below closes
        current_price = orh + 0.20
        current_volume = 2_500_000
        return candles_1m, daily_closes, daily_volumes, daily_lows, current_price, current_volume

    def test_stop_uses_prior_day_low(self):
        candles_1m, daily_closes, daily_volumes, daily_lows, current_price, current_volume = (
            self._make_valid_inputs_with_lows()
        )
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume, daily_lows=daily_lows,
        )
        assert result is not None
        assert result.stop_price == daily_lows[-1]

    def test_stop_falls_back_to_today_low_without_daily_lows(self):
        candles_1m, daily_closes, daily_volumes, _, current_price, current_volume = (
            self._make_valid_inputs_with_lows()
        )
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume,
        )
        assert result is not None
        today_low = min(c["low"] for c in candles_1m)
        assert result.stop_price == today_low


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

class TestStrategyRegistry:
    def test_all_strategies_registered(self):
        assert "breakout" in STRATEGY_REGISTRY
        assert "episodic_pivot" in STRATEGY_REGISTRY
        assert "parabolic_short" in STRATEGY_REGISTRY

    def test_unknown_setup_type_returns_none(self):
        result = evaluate_signal("nonexistent", "AAPL", candles_1m=[], current_price=50.0,
                                 current_volume=0, daily_closes=[], daily_volumes=[])
        assert result is None

    def test_evaluate_breakout_via_registry(self):
        candles_1m = make_candles(30, base_price=50.0, step=0.10)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_closes = make_daily_closes(25, start=45.0, drift=0.2)
        daily_volumes = make_daily_volumes(25, base=1_000_000)

        result = evaluate_signal(
            "breakout", "AAPL",
            candles_1m=candles_1m,
            daily_closes=daily_closes,
            daily_volumes=daily_volumes,
            current_price=orh + 0.20,
            current_volume=2_500_000,
        )
        assert result is not None
        assert result.setup_type == "breakout"

    def test_evaluate_episodic_pivot_via_registry(self):
        candles_1m = make_candles(30, base_price=115.0, step=0.15)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_volumes = make_daily_volumes(25, base=500_000)

        result = evaluate_signal(
            "episodic_pivot", "NVDA",
            candles_1m=candles_1m,
            daily_closes=[],
            daily_volumes=daily_volumes,
            current_price=orh + 0.50,
            current_volume=2_000_000,
            gap_pct=15.0,
        )
        assert result is not None
        assert result.setup_type == "episodic_pivot"

    def test_evaluate_parabolic_short_via_registry(self):
        daily_closes = [10.0, 10.5, 10.8, 16.0, 17.0, 18.0]
        candles_1m = []
        for i in range(25):
            p = 18.0 + i * 0.01
            candles_1m.append({
                "open": p, "high": p + 0.15, "low": p - 0.05,
                "close": p, "volume": 1_000_000,
            })
        for i in range(5):
            p = 13.0 - i * 0.10
            candles_1m.append({
                "open": p + 0.05, "high": p + 0.10, "low": p - 0.05,
                "close": p, "volume": 80_000,
            })

        result = evaluate_signal(
            "parabolic_short", "MEME",
            candles_1m=candles_1m,
            daily_closes=daily_closes,
            daily_volumes=[],
            current_price=12.9,
            current_volume=100_000,
        )
        assert result is not None
        assert result.setup_type == "parabolic_short"
