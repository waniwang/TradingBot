"""
Unit tests for signal modules.

Uses synthetic data — no network calls.
"""

import pytest
from signals import evaluate_signal, STRATEGY_REGISTRY
from signals.base import (
    compute_orh, compute_orb_low, compute_vwap, compute_sma,
    compute_atr_from_list, compute_rvol, _cumulative_volume_fraction,
)
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
        # With RVOL: at 30 min mark, expected fraction ≈ 0.22
        # avg_vol ≈ 1M, so expected_by_now ≈ 220k. Need RVOL < 1.5 → vol < 330k
        current_volume = 200_000
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume, minutes_since_open=30,
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

class TestBreakoutStopLogic:
    def _make_valid_inputs(self):
        candles_1m = make_candles(30, base_price=50.0, step=0.10)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_closes = make_daily_closes(25, start=45.0, drift=0.2)
        daily_volumes = make_daily_volumes(25, base=1_000_000)
        current_price = orh + 0.20
        current_volume = 2_500_000
        return candles_1m, daily_closes, daily_volumes, current_price, current_volume

    def test_stop_is_lod(self):
        """Breakout stop should be LOD (Qullamaggie rule)."""
        candles_1m, daily_closes, daily_volumes, current_price, current_volume = (
            self._make_valid_inputs()
        )
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume,
        )
        assert result is not None
        today_low = min(c["low"] for c in candles_1m)
        assert result.stop_price == today_low

    def test_stop_capped_by_atr(self):
        """When LOD is far below entry, stop should be capped at 1x ATR."""
        candles_1m = make_candles(30, base_price=50.0, step=0.10)
        orh = compute_orh(candles_1m, n_minutes=5)
        # Insert an artificially low candle to create a wide stop
        candles_1m[10] = {
            "open": 30.0, "high": 30.5, "low": 20.0,
            "close": 30.0, "volume": 50_000,
        }

        daily_closes = make_daily_closes(25, start=45.0, drift=0.2)
        daily_volumes = make_daily_volumes(25, base=1_000_000)
        daily_highs = [c + 1.0 for c in daily_closes]
        daily_lows = [c - 1.0 for c in daily_closes]
        current_price = orh + 0.20
        current_volume = 2_500_000

        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume,
            daily_highs=daily_highs, daily_lows=daily_lows,
        )
        assert result is not None
        # Stop should NOT be 20.0 (the artificial LOD) — ATR cap kicks in
        assert result.stop_price > 20.0
        assert result.stop_price < result.entry_price


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


# ---------------------------------------------------------------------------
# compute_atr_from_list
# ---------------------------------------------------------------------------

class TestComputeAtrFromList:
    def test_basic(self):
        highs = [h + 1.0 for h in range(20)]
        lows = [l - 1.0 for l in range(20)]
        closes = list(range(20))
        atr = compute_atr_from_list(highs, lows, [float(c) for c in closes])
        assert atr is not None
        assert atr > 0

    def test_insufficient_data(self):
        assert compute_atr_from_list([10.0] * 5, [9.0] * 5, [9.5] * 5) is None

    def test_constant_bars(self):
        # ATR = high - low when all bars are identical
        n = 20
        highs = [11.0] * n
        lows = [9.0] * n
        closes = [10.0] * n
        atr = compute_atr_from_list(highs, lows, closes)
        assert atr is not None
        assert atr == pytest.approx(2.0, rel=0.01)


# ---------------------------------------------------------------------------
# EP with ATR cap
# ---------------------------------------------------------------------------

class TestEpisodicPivotAtrCap:
    def test_ep_stop_capped_by_atr(self):
        """EP stop should be capped at 1.5x ATR when LOD is very far."""
        candles_1m = make_candles(30, base_price=115.0, step=0.15)
        orh = compute_orh(candles_1m, n_minutes=5)
        # Insert an artificially low candle to make LOD very wide
        candles_1m[15] = {
            "open": 50.0, "high": 50.5, "low": 40.0,
            "close": 50.0, "volume": 50_000,
        }

        daily_volumes = make_daily_volumes(25, base=500_000)
        daily_closes = make_daily_closes(25, start=100.0, drift=0.5)
        daily_highs = [c + 2.0 for c in daily_closes]
        daily_lows = [c - 2.0 for c in daily_closes]
        current_price = orh + 0.50
        current_volume = 2_000_000
        gap_pct = 15.0

        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct,
            daily_highs=daily_highs, daily_lows=daily_lows,
            daily_closes=daily_closes,
        )
        assert result is not None
        # Stop should NOT be 40.0 (the artificial LOD)
        assert result.stop_price > 40.0
        assert result.stop_price < result.entry_price

    def test_ep_stop_is_lod_when_within_atr(self):
        """When LOD is within 1.5x ATR, the stop should remain at LOD."""
        candles_1m = make_candles(30, base_price=115.0, step=0.15)
        orh = compute_orh(candles_1m, n_minutes=5)

        daily_volumes = make_daily_volumes(25, base=500_000)
        current_price = orh + 0.50
        current_volume = 2_000_000
        gap_pct = 15.0

        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct,
        )
        assert result is not None
        lod = min(c["low"] for c in candles_1m)
        assert result.stop_price == lod


# ---------------------------------------------------------------------------
# RVOL (time-of-day relative volume)
# ---------------------------------------------------------------------------

class TestCumulativeVolumeFraction:
    def test_at_open(self):
        assert _cumulative_volume_fraction(0) == 0.0

    def test_at_close(self):
        assert _cumulative_volume_fraction(390) == 1.0

    def test_after_close(self):
        assert _cumulative_volume_fraction(400) == 1.0

    def test_at_5_minutes(self):
        assert _cumulative_volume_fraction(5) == pytest.approx(0.065, rel=1e-3)

    def test_at_30_minutes(self):
        assert _cumulative_volume_fraction(30) == pytest.approx(0.22, rel=1e-3)

    def test_monotonically_increasing(self):
        prev = 0.0
        for m in range(1, 391):
            cur = _cumulative_volume_fraction(m)
            assert cur >= prev, f"fraction decreased at minute {m}"
            prev = cur

    def test_interpolation_midpoints(self):
        # 10 min is between anchors (5, 0.065) and (15, 0.14)
        f10 = _cumulative_volume_fraction(10)
        assert 0.065 < f10 < 0.14


class TestComputeRvol:
    def test_basic(self):
        # At 5 min in, 6.5% of daily volume expected
        # If today_volume equals expected → RVOL = 1.0
        avg_daily = 1_000_000
        expected_at_5min = avg_daily * 0.065
        rvol = compute_rvol(int(expected_at_5min), avg_daily, 5)
        assert rvol == pytest.approx(1.0, rel=0.01)

    def test_high_rvol_at_open(self):
        # 200k volume in first 5 min vs 1M daily avg → expected 65k → RVOL ≈ 3.08
        rvol = compute_rvol(200_000, 1_000_000, 5)
        assert rvol == pytest.approx(3.08, rel=0.05)

    def test_low_rvol_midday(self):
        # 300k at 120 min mark (50% expected) → expected 500k → RVOL = 0.6
        rvol = compute_rvol(300_000, 1_000_000, 120)
        assert rvol == pytest.approx(0.6, rel=0.01)

    def test_zero_avg_volume(self):
        assert compute_rvol(100_000, 0, 30) == 0.0

    def test_zero_minutes(self):
        assert compute_rvol(100_000, 1_000_000, 0) == 0.0

    def test_negative_minutes(self):
        assert compute_rvol(100_000, 1_000_000, -5) == 0.0


# ---------------------------------------------------------------------------
# Breakout extension guard
# ---------------------------------------------------------------------------

class TestBreakoutExtensionGuard:
    def _make_valid_inputs(self):
        candles_1m = make_candles(30, base_price=50.0, step=0.10)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_closes = make_daily_closes(25, start=45.0, drift=0.2)
        daily_volumes = make_daily_volumes(25, base=1_000_000)
        current_volume = 2_500_000
        return candles_1m, daily_closes, daily_volumes, orh, current_volume

    def test_signal_fires_within_extension(self):
        candles_1m, daily_closes, daily_volumes, orh, current_volume = (
            self._make_valid_inputs()
        )
        # 0.5% above ORH — well within 3% default
        current_price = orh * 1.005
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume,
        )
        assert result is not None

    def test_no_signal_when_too_extended(self):
        candles_1m, daily_closes, daily_volumes, orh, current_volume = (
            self._make_valid_inputs()
        )
        # 5% above ORH — beyond 3% default
        current_price = orh * 1.05
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume,
        )
        assert result is None

    def test_config_overrides_max_extension(self):
        candles_1m, daily_closes, daily_volumes, orh, current_volume = (
            self._make_valid_inputs()
        )
        current_price = orh * 1.05  # 5% above ORH
        # Raise limit to 6% — should now fire
        config = {"signals": {"breakout_max_extension_pct": 6.0}}
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume, config=config,
        )
        assert result is not None

    def test_extension_pct_in_notes(self):
        candles_1m, daily_closes, daily_volumes, orh, current_volume = (
            self._make_valid_inputs()
        )
        current_price = orh * 1.01
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price, current_volume,
        )
        assert result is not None
        assert "ORH" in result.notes
        assert "RVOL" in result.notes


# ---------------------------------------------------------------------------
# EP extension guard
# ---------------------------------------------------------------------------

class TestEPExtensionGuard:
    def _make_valid_inputs(self):
        candles_1m = make_candles(30, base_price=115.0, step=0.15)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_volumes = make_daily_volumes(25, base=500_000)
        current_volume = 2_000_000
        gap_pct = 15.0
        return candles_1m, daily_volumes, orh, current_volume, gap_pct

    def test_signal_fires_within_extension(self):
        candles_1m, daily_volumes, orh, current_volume, gap_pct = (
            self._make_valid_inputs()
        )
        # 0.4% above ORH — within 5% EP default
        current_price = orh + 0.50
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct,
        )
        assert result is not None

    def test_no_signal_when_too_extended(self):
        candles_1m, daily_volumes, orh, current_volume, gap_pct = (
            self._make_valid_inputs()
        )
        # 8% above ORH — beyond 5% EP default
        current_price = orh * 1.08
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct,
        )
        assert result is None

    def test_config_overrides_max_extension(self):
        candles_1m, daily_volumes, orh, current_volume, gap_pct = (
            self._make_valid_inputs()
        )
        current_price = orh * 1.08  # 8% above ORH
        # Raise EP limit to 10%
        config = {"signals": {"ep_max_extension_pct": 10.0}}
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct, config=config,
        )
        assert result is not None

    def test_ep_rvol_in_notes(self):
        candles_1m, daily_volumes, orh, current_volume, gap_pct = (
            self._make_valid_inputs()
        )
        current_price = orh + 0.50
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price, current_volume, gap_pct,
        )
        assert result is not None
        assert "RVOL" in result.notes


# ---------------------------------------------------------------------------
# RVOL integration with signals
# ---------------------------------------------------------------------------

class TestBreakoutRvolIntegration:
    def test_high_rvol_early_morning_fires(self):
        """At 5 min after open with strong volume, signal should fire."""
        candles_1m = make_candles(10, base_price=50.0, step=0.10)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_closes = make_daily_closes(25, start=45.0, drift=0.2)
        daily_volumes = make_daily_volumes(25, base=1_000_000)

        # 200k volume in 5 min → RVOL = 200k / (1M * 0.065) ≈ 3.08x > 1.5x
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price=orh + 0.20,
            current_volume=200_000,
            minutes_since_open=5,
        )
        assert result is not None
        assert result.volume_ratio > 1.5

    def test_low_rvol_early_morning_rejects(self):
        """At 5 min after open with normal volume, signal should not fire."""
        candles_1m = make_candles(10, base_price=50.0, step=0.10)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_closes = make_daily_closes(25, start=45.0, drift=0.2)
        daily_volumes = make_daily_volumes(25, base=1_000_000)

        # 50k volume in 5 min → RVOL = 50k / (1M * 0.065) ≈ 0.77x < 1.5x
        result = check_breakout(
            "AAPL", candles_1m, daily_closes, daily_volumes,
            current_price=orh + 0.20,
            current_volume=50_000,
            minutes_since_open=5,
        )
        assert result is None


class TestEPRvolIntegration:
    def test_high_rvol_early_morning_fires(self):
        candles_1m = make_candles(10, base_price=115.0, step=0.15)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_volumes = make_daily_volumes(25, base=500_000)

        # 300k in 5 min → RVOL = 300k / (500k * 0.065) ≈ 9.2x > 2.0x
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price=orh + 0.50,
            current_volume=300_000,
            gap_pct=15.0,
            minutes_since_open=5,
        )
        assert result is not None

    def test_low_rvol_rejects(self):
        candles_1m = make_candles(10, base_price=115.0, step=0.15)
        orh = compute_orh(candles_1m, n_minutes=5)
        daily_volumes = make_daily_volumes(25, base=500_000)

        # 20k in 5 min → RVOL = 20k / (500k * 0.065) ≈ 0.62x < 2.0x
        result = check_episodic_pivot(
            "NVDA", candles_1m, daily_volumes,
            current_price=orh + 0.50,
            current_volume=20_000,
            gap_pct=15.0,
            minutes_since_open=5,
        )
        assert result is None
