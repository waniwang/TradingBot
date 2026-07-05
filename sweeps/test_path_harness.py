"""Unit tests for sweeps/path_harness.py — synthetic-bar spec enforcement.

Run:  trading-bot/.venv/bin/pytest sweeps/test_path_harness.py -v
"""

from __future__ import annotations

import numpy as np
import pytest

from sweeps.path_harness import ExitRules, PRODUCTION_RULES, simulate_path_exit

ENTRY = 100.0
D0 = np.datetime64("2026-01-05")  # a Monday


def mk(dates_offsets: list[int], ohlc: list[tuple]) -> tuple[np.ndarray, np.ndarray]:
    dates = np.array([D0 + np.timedelta64(o, "D") for o in dates_offsets],
                     dtype="datetime64[D]")
    return dates, np.array(ohlc, dtype=np.float64)


def weekdays(n: int) -> list[int]:
    """First n trading-day offsets after D0 (skip weekends)."""
    out, o = [], 0
    while len(out) < n:
        o += 1
        if (D0 + np.timedelta64(o, "D")).astype("datetime64[D]").item().weekday() < 5:
            out.append(o)
    return out


def flat_bars(n: int, px: float = 100.0) -> list[tuple]:
    return [(px, px + 0.5, px - 0.5, px)] * n


class TestHardStop:
    def test_stop_fill_at_stop_price(self):
        # Day 2 low pierces the -7% stop -> fill exactly at 93.
        dates, bars = mk([1, 2], [(100, 101, 99, 100), (99, 99.5, 92, 94)])
        r = simulate_path_exit(dates, bars, ENTRY, D0, ExitRules(time_partial_day=None))
        assert r.exit_reason == "stop"
        assert r.return_pct == pytest.approx(-7.0)
        assert r.exit_day == 2

    def test_gap_through_fills_at_open(self):
        # Day 1 opens at 88, below the 93 stop -> fill at the open, not the stop.
        dates, bars = mk([1], [(88, 90, 85, 89)])
        r = simulate_path_exit(dates, bars, ENTRY, D0, ExitRules(time_partial_day=None))
        assert r.exit_reason == "stop"
        assert r.return_pct == pytest.approx(-12.0)

    def test_same_day_stop_and_target_is_pessimistic(self):
        # Bar hits both the +10 target high AND the -7 stop low -> stop wins.
        rules = ExitRules(profit_target_pct=10.0, profit_target_fraction=0.5,
                          time_partial_day=None)
        dates, bars = mk([1], [(100, 112, 92, 95)])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        assert r.exit_reason == "stop"
        assert r.return_pct == pytest.approx(-7.0)


class TestProfitTarget:
    def test_partial_at_target_then_hold(self):
        rules = ExitRules(profit_target_pct=10.0, profit_target_fraction=0.5,
                          time_partial_day=None, max_hold_days=50)
        offs = weekdays(4)
        dates, bars = mk(offs, [(100, 111, 100, 108),  # target 110 touched
                                *flat_bars(3, 108)])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        # Path is only 4 bars, ends before max_hold -> delisted-style final leg.
        assert r.legs[0] == (0.5, pytest.approx(10.0), offs[0])
        assert r.legs[-1][0] == pytest.approx(0.5)
        assert r.return_pct == pytest.approx(0.5 * 10.0 + 0.5 * 8.0)

    def test_target_gap_open_fills_at_open(self):
        rules = ExitRules(profit_target_pct=10.0, profit_target_fraction=0.5,
                          time_partial_day=None)
        dates, bars = mk([1], [(115, 116, 112, 114)])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        assert r.legs[0][1] == pytest.approx(15.0)  # filled at open 115, not 110

    def test_full_scale_out_reason_target_final(self):
        rules = ExitRules(profit_target_pct=10.0, profit_target_fraction=1.0,
                          time_partial_day=None)
        dates, bars = mk([1, 2], [(100, 111, 100, 108), (108, 109, 107, 108)])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        assert r.exit_reason == "target_final"
        assert r.return_pct == pytest.approx(10.0)


class TestTimePartial:
    def test_d19_partial_fires_and_moves_stop(self):
        # Production rule: at first bar >= 19 calendar days, in profit ->
        # 40% off at close, stop -> 105 effective NEXT bar.
        offs = weekdays(16)  # ~22 calendar days
        first_d19 = next(o for o in offs if o >= 19)
        bars = flat_bars(len(offs), 110.0)
        # Bar after the partial trades down to 104 -> stopped at 105.
        idx_after = offs.index(first_d19) + 1
        bars[idx_after] = (108, 108, 104, 104)
        dates, arr = mk(offs, bars)
        r = simulate_path_exit(dates, arr, ENTRY, D0, PRODUCTION_RULES)
        partial_legs = [l for l in r.legs if l[0] == pytest.approx(0.40)]
        assert partial_legs and partial_legs[0][2] == first_d19
        assert r.exit_reason == "stop"
        stop_leg = [l for l in r.legs if l[0] == pytest.approx(0.60)][0]
        assert stop_leg[1] == pytest.approx(5.0)  # entry*1.05

    def test_d19_skipped_when_not_in_profit(self):
        offs = weekdays(16)
        dates, arr = mk(offs, flat_bars(len(offs), 95.0))  # underwater, above stop
        r = simulate_path_exit(dates, arr, ENTRY, D0, PRODUCTION_RULES)
        assert all(l[0] != pytest.approx(0.40) for l in r.legs[:-1])

    def test_stop_move_not_same_bar(self):
        # The partial bar itself dips to 104 AFTER the close-partial would
        # have fired; stop is still the original 93 that day -> no stop exit.
        offs = weekdays(15)
        first_d19 = next(o for o in offs if o >= 19)
        bars = flat_bars(len(offs), 110.0)
        i = offs.index(first_d19)
        bars[i] = (110, 110, 104, 110)  # low 104 > 93, would hit a 105 stop
        dates, arr = mk(offs, bars)
        r = simulate_path_exit(dates, arr, ENTRY, D0, PRODUCTION_RULES)
        assert r.exit_reason == "delisted"  # ran off the end, never stopped


class TestBreakeven:
    def test_breakeven_arms_next_bar(self):
        rules = ExitRules(breakeven_trigger_pct=8.0, breakeven_lock_pct=1.0,
                          time_partial_day=None)
        dates, bars = mk([1, 2, 3], [
            (100, 109, 100, 108.5),   # close >= 108 -> arm, effective day 2
            (108, 108, 100.5, 101),   # low 100.5 <= stop 101 -> stopped at 101
            (101, 102, 100, 101),
        ])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        assert r.exit_reason == "stop"
        assert r.return_pct == pytest.approx(1.0)
        assert r.exit_day == 2


class TestTrailing:
    def test_hwm_close_pct(self):
        rules = ExitRules(trail_mode="hwm_close_pct", trail_param=10.0,
                          time_partial_day=None)
        dates, bars = mk([1, 2, 3], [
            (100, 121, 100, 120),   # hwm_close 120
            (119, 119, 110, 112),   # 112 > 108 -> hold
            (110, 110, 106, 107),   # 107 < 120*0.9=108 -> exit at close 107
        ])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        assert r.exit_reason == "trail"
        assert r.return_pct == pytest.approx(7.0)

    def test_n_day_low(self):
        rules = ExitRules(trail_mode="n_day_low", trail_param=2,
                          time_partial_day=None)
        dates, bars = mk([1, 2, 3, 4], [
            (100, 106, 100, 105),
            (105, 108, 104, 107),
            (107, 107, 105, 106),
            (106, 106, 103, 104.5),  # close < min(107,106)=106 -> exit
        ])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        assert r.exit_reason == "trail"
        assert r.return_pct == pytest.approx(4.5)

    def test_ma_close(self):
        rules = ExitRules(trail_mode="ma_close", trail_param=3,
                          time_partial_day=None)
        dates, bars = mk([1, 2, 3, 4], [
            (100, 111, 100, 110),
            (110, 113, 109, 112),
            (112, 112, 108, 111),
            (111, 111, 100, 101),  # sma3 = (112+111+101)/3=108 -> 101 < 108
        ])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        assert r.exit_reason == "trail"
        assert r.return_pct == pytest.approx(1.0)


class TestMaxHoldAndTruncation:
    def test_max_hold_at_close(self):
        rules = ExitRules(time_partial_day=None, max_hold_days=10)
        offs = weekdays(10)
        dates, arr = mk(offs, flat_bars(len(offs), 103.0))
        r = simulate_path_exit(dates, arr, ENTRY, D0, rules)
        assert r.exit_reason == "max_hold"
        assert r.return_pct == pytest.approx(3.0)
        assert r.exit_day >= 10

    def test_truncated_path_is_delisted(self):
        rules = ExitRules(time_partial_day=None, max_hold_days=50)
        dates, arr = mk([1, 2], flat_bars(2, 98.0))
        r = simulate_path_exit(dates, arr, ENTRY, D0, rules)
        assert r.exit_reason == "delisted"
        assert r.return_pct == pytest.approx(-2.0)


class TestMfeMae:
    def test_mfe_mae_tracked_until_exit(self):
        rules = ExitRules(time_partial_day=None)
        dates, bars = mk([1, 2, 3], [
            (100, 112, 99, 110),    # MFE +12 on day 1
            (110, 111, 103, 104),
            (103, 104, 92, 93.5),   # stop day: MAE floor at stop fill
        ])
        r = simulate_path_exit(dates, bars, ENTRY, D0, rules)
        assert r.exit_reason == "stop"
        assert r.mfe_pct == pytest.approx(12.0)
        assert r.day_of_mfe == 1
        assert r.mae_pct <= -7.0

    def test_production_rules_blend_weights_sum_to_one(self):
        offs = weekdays(40)
        dates, arr = mk(offs, flat_bars(len(offs), 108.0))
        r = simulate_path_exit(dates, arr, ENTRY, D0, PRODUCTION_RULES)
        assert sum(l[0] for l in r.legs) == pytest.approx(1.0)
        assert r.exit_reason == "max_hold"


class TestRestedBreakoutEntry:
    def _bars(self, closes, lo_off=0.5, hi_off=0.5):
        return np.array([(c, c + hi_off, c - lo_off, c) for c in closes],
                        dtype=np.float64)

    def test_no_entry_before_min_days(self):
        from sweeps.path_harness import find_rested_breakout_entry
        # closes above gap_high from day 0 — but must wait min_days=3
        bars = self._bars([101, 101.5, 102, 102.5, 103])
        hit = find_rested_breakout_entry(100.0, 92.0, bars, min_days=3)
        assert hit == (3, pytest.approx(102.5))

    def test_gap_low_violation_kills_setup(self):
        from sweeps.path_harness import find_rested_breakout_entry
        bars = self._bars([95, 91.0, 96, 102])  # closes below gap_low=92 on day 1
        assert find_rested_breakout_entry(100.0, 92.0, bars, min_days=3) is None

    def test_chase_rejected_above_premium(self):
        from sweeps.path_harness import find_rested_breakout_entry
        bars = self._bars([98, 97, 99, 106.5])  # first close over 100 is +6.5%
        assert find_rested_breakout_entry(100.0, 92.0, bars, min_days=3,
                                          max_premium=1.05) is None

    def test_no_confirmation_no_trade(self):
        from sweeps.path_harness import find_rested_breakout_entry
        bars = self._bars([98, 97, 99, 98, 97, 99])
        assert find_rested_breakout_entry(100.0, 92.0, bars, min_days=3,
                                          window=6) is None


class TestEdgeCases:
    def test_empty_path_returns_none(self):
        dates, bars = mk([], [])
        bars = bars.reshape(0, 4)
        assert simulate_path_exit(dates, bars, ENTRY, D0, PRODUCTION_RULES) is None

    def test_unknown_trail_mode_raises(self):
        rules = ExitRules(trail_mode="bogus", trail_param=5, time_partial_day=None)
        dates, bars = mk([1], flat_bars(1))
        with pytest.raises(ValueError):
            simulate_path_exit(dates, bars, ENTRY, D0, rules)
