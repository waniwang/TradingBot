"""Path-based exit simulation for EP swing sweeps.

Replays each trade day-by-day over its actual daily OHLC path (fetched from
Massive.com by scripts/fetch_massive_daily_paths.py), so exit rules the
checkpoint harness cannot express — profit targets, early partials,
breakeven moves, trailing stops — become sweepable.

Keep this file pure like harness.py: no I/O here except through
path_store.load_paths (per-process lru_cache). ProcessPool-safe.

Execution-model conventions (THE SPEC — tests assert these):

- Entry is at day-0 close. `bars` rows are days 1..n after entry, TRADING
  days as returned by Massive (halts/holidays are simply absent rows).
- `dates` gives each bar's session date; calendar-day rules
  (time_partial_day, max_hold_days) compare (date - entry_date).days,
  matching the live bot which counts calendar days.
- Hard stop (initial / breakeven / post-partial) is intraday GTC:
  if open <= stop -> fill at open (gap-through), elif low <= stop -> fill
  at stop.
- Profit-target partial is intraday: if open >= target -> fill at open,
  elif high >= target -> fill at target.
- Same-day stop-and-target ambiguity: PESSIMISTIC — stop is checked first.
- Trailing exits and breakeven arming evaluate on CLOSES and exit at that
  close (the live bot is an EOD scheduler; close-based trails are the only
  ones it can execute, and they dodge intraday path ambiguity):
    * hwm_close_pct: close < high-water-close * (1 - p/100) -> exit at close
    * n_day_low:     close < min of prior N closes           -> exit at close
    * ma_close:      close < N-day SMA of closes (incl today) -> exit at close
- Time partial / max hold execute at that day's close. (Live fires the
  partial at 9:40 AM the following morning; close-of-trigger-day is the
  closest daily-bar approximation and is applied consistently to baseline
  and candidates, so comparisons are apples-to-apples.)
- Stop adjustments (breakeven, post-partial) take effect NEXT bar.
- Truncated paths (delisting/halt): exit remaining at last available close,
  reason "delisted".
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from sweeps.harness import apply_filters, compute_metrics, load_data
from sweeps.path_store import load_paths


@dataclass(frozen=True)
class ExitRules:
    """One exit-rule combo. Frozen/hashable -> ProcessPool-safe."""

    stop_pct: float = 7.0
    # Profit-target partial: sell `fraction` of the ORIGINAL position when
    # price first touches entry*(1+pct/100). None disables.
    profit_target_pct: float | None = None
    profit_target_fraction: float = 0.0
    # Time-based partial (production D19 rule): at the first bar >= N
    # calendar days after entry (optionally only if in profit at that close),
    # sell `fraction` of the original position and move the stop on the
    # remainder to entry*stop_mult (effective next bar). None disables.
    time_partial_day: int | None = 19
    time_partial_fraction: float = 0.40
    time_partial_requires_profit: bool = True
    time_partial_stop_mult: float | None = 1.05
    # Breakeven/lock move: when a close reaches entry*(1+trigger/100), raise
    # the stop to entry*(1+lock/100) (effective next bar). None disables.
    breakeven_trigger_pct: float | None = None
    breakeven_lock_pct: float = 0.0
    # Trailing exit on closes: None | "hwm_close_pct" | "n_day_low" | "ma_close"
    trail_mode: str | None = None
    trail_param: float = 0.0
    max_hold_days: int = 50


# Production baseline as of 2026-07 (EP Earnings B + EP News A/B all share it).
PRODUCTION_RULES = ExitRules()


@dataclass(frozen=True)
class PathResult:
    return_pct: float  # position-weighted blended return
    exit_day: int      # calendar days from entry, final leg
    exit_reason: str   # stop|trail|target_final|max_hold|delisted
    legs: tuple[tuple[float, float, int], ...]  # (fraction, return_pct, cal_day)
    mfe_pct: float
    mae_pct: float
    day_of_mfe: int    # calendar days from entry to the high-water high


def simulate_path_exit(
    dates: np.ndarray,
    bars: np.ndarray,
    entry_price: float,
    entry_date: np.datetime64,
    rules: ExitRules,
) -> PathResult | None:
    """Replay one trade. dates: (n,) datetime64[D]; bars: (n,4) OHLC for the
    trading days AFTER entry. Returns None when the path is empty."""
    n = len(bars)
    if n == 0 or entry_price <= 0:
        return None

    ret = lambda px: (px - entry_price) / entry_price * 100.0  # noqa: E731

    stop = entry_price * (1 - rules.stop_pct / 100.0)
    pending_stop: float | None = None

    remaining = 1.0
    legs: list[tuple[float, float, int]] = []
    target_done = rules.profit_target_pct is None
    tp_done = rules.time_partial_day is None
    be_done = rules.breakeven_trigger_pct is None

    hwm_close = entry_price
    closes: list[float] = []

    mfe = 0.0
    mae = 0.0
    day_of_mfe = 0
    exit_reason = None
    exit_day = 0

    for i in range(n):
        cal_day = int((dates[i] - entry_date) / np.timedelta64(1, "D"))
        if pending_stop is not None:
            stop = max(stop, pending_stop)
            pending_stop = None

        o, h, l, c = float(bars[i, 0]), float(bars[i, 1]), float(bars[i, 2]), float(bars[i, 3])

        if ret(h) > mfe:
            mfe = ret(h)
            day_of_mfe = cal_day
        mae = min(mae, ret(l))

        # 1. Hard stop — intraday, gap-through at open, pessimistic-first.
        if o <= stop:
            legs.append((remaining, ret(o), cal_day))
            remaining = 0.0
            exit_reason, exit_day = "stop", cal_day
            break
        if l <= stop:
            legs.append((remaining, ret(stop), cal_day))
            remaining = 0.0
            exit_reason, exit_day = "stop", cal_day
            break

        # 2. Profit-target partial — intraday.
        if not target_done and remaining > 0:
            target = entry_price * (1 + rules.profit_target_pct / 100.0)
            fill = o if o >= target else (target if h >= target else None)
            if fill is not None:
                frac = min(rules.profit_target_fraction, remaining)
                if frac > 0:
                    legs.append((frac, ret(fill), cal_day))
                    remaining -= frac
                target_done = True

        # 3. Time partial (D19-style) — at close of the first qualifying bar.
        if not tp_done and remaining > 0 and cal_day >= rules.time_partial_day:
            if not rules.time_partial_requires_profit or c > entry_price:
                frac = min(rules.time_partial_fraction, remaining)
                if frac > 0:
                    legs.append((frac, ret(c), cal_day))
                    remaining -= frac
                if rules.time_partial_stop_mult is not None:
                    pending_stop = entry_price * rules.time_partial_stop_mult
                tp_done = True

        # 4. Breakeven/lock arming — on close, effective next bar.
        if not be_done and c >= entry_price * (1 + rules.breakeven_trigger_pct / 100.0):
            pending_stop = max(
                pending_stop or 0.0,
                entry_price * (1 + rules.breakeven_lock_pct / 100.0),
            )
            be_done = True

        # 5. Trailing exits — on closes.
        hwm_close = max(hwm_close, c)
        if rules.trail_mode is not None and remaining > 0:
            fire = False
            if rules.trail_mode == "hwm_close_pct":
                fire = c < hwm_close * (1 - rules.trail_param / 100.0)
            elif rules.trail_mode == "n_day_low":
                nn = int(rules.trail_param)
                fire = len(closes) >= nn and c < min(closes[-nn:])
            elif rules.trail_mode == "ma_close":
                nn = int(rules.trail_param)
                if len(closes) >= nn - 1:
                    sma = (sum(closes[-(nn - 1):]) + c) / nn if nn > 1 else c
                    fire = c < sma
            else:
                raise ValueError(f"unknown trail_mode: {rules.trail_mode}")
            if fire:
                legs.append((remaining, ret(c), cal_day))
                remaining = 0.0
                exit_reason, exit_day = "trail", cal_day
                closes.append(c)
                break
        closes.append(c)

        # 6. Max hold — at close.
        if cal_day >= rules.max_hold_days:
            legs.append((remaining, ret(c), cal_day))
            remaining = 0.0
            exit_reason, exit_day = "max_hold", cal_day
            break

        # Fully scaled out via partials?
        if remaining <= 1e-9:
            exit_reason, exit_day = "target_final", cal_day
            break

    if remaining > 1e-9:
        # Path ended before any terminal rule fired (delisting / data truncation).
        last_day = int((dates[n - 1] - entry_date) / np.timedelta64(1, "D"))
        legs.append((remaining, ret(float(bars[n - 1, 3])), last_day))
        exit_reason, exit_day = "delisted", last_day
    elif exit_reason is None:
        exit_reason, exit_day = "target_final", legs[-1][2]

    total_w = sum(w for w, _, _ in legs)
    blended = sum(w * r for w, r, _ in legs) / total_w

    return PathResult(
        return_pct=float(blended),
        exit_day=exit_day,
        exit_reason=exit_reason,
        legs=tuple(legs),
        mfe_pct=float(mfe),
        mae_pct=float(mae),
        day_of_mfe=day_of_mfe,
    )


def find_rested_breakout_entry(
    gap_high: float,
    gap_low: float,
    bars: np.ndarray,
    min_days: int = 3,
    window: int = 15,
    max_premium: float = 1.05,
) -> tuple[int, float] | None:
    """EP-2.0 Track A entry: after >= min_days of consolidation that never
    CLOSES below the gap-day low, buy the first close above the gap-day high
    within `window` bars — unless that close has already run past
    gap_high * max_premium (chasing).

    Returns (bar_index, entry_price) or None (no valid entry = no trade;
    this is the mechanism that skips gap-and-fade candidates entirely)."""
    for i in range(min(window, len(bars))):
        close = bars[i, 3]
        if close < gap_low:
            return None  # thesis broken before confirmation
        if i >= min_days and close > gap_high:
            if close <= gap_high * max_premium:
                return i, float(close)
            return None  # gapped past the buy zone
    return None


@dataclass(frozen=True)
class PathBacktestRequest:
    """One path-mode combo. Frozen so it's hashable + ProcessPool-safe.

    entry_params may include pseudo-params handled here (not in
    apply_filters):
      date_min / date_max — era cut (ISO strings)
      entry_mode          — "gap_day_close" (default) | "rested_breakout"
      bo_min_days / bo_window / bo_max_premium — rested_breakout knobs
      bo_stop_mode        — "fixed" (ExitRules.stop_pct) | "base_low"
                            (stop under min low of the 4 bars into entry,
                            clamped to [3%, 12%])
    exclude_entry_params, when non-empty, removes rows that ALSO pass that
    filter set — used for News B, where A wins on overlap."""

    strategy_name: str
    entry_params: tuple[tuple[str, object], ...]
    exit_params: tuple[tuple[str, object], ...]
    data_path: str
    paths_dir: str
    exclude_entry_params: tuple[tuple[str, object], ...] = ()

    @property
    def entry_dict(self) -> dict:
        return dict(self.entry_params)

    @property
    def exit_rules(self) -> ExitRules:
        return ExitRules(**dict(self.exit_params))


def run_path_backtest(req: PathBacktestRequest) -> dict:
    """Pure function: filter events (reusing harness.apply_filters), replay
    each surviving event over its Massive path, compute the standard metrics.

    Entry price = Massive adjusted day-0 close (NOT the Spikeet close): all
    returns live inside one consistently-adjusted price series; Spikeet
    supplies only the entry-day filter features."""
    df = load_data(req.data_path)

    entry_cfg = req.entry_dict
    date_min = entry_cfg.pop("date_min", None)
    date_max = entry_cfg.pop("date_max", None)
    entry_mode = entry_cfg.pop("entry_mode", "gap_day_close")
    bo_min_days = int(entry_cfg.pop("bo_min_days", 3))
    bo_window = int(entry_cfg.pop("bo_window", 15))
    bo_max_premium = float(entry_cfg.pop("bo_max_premium", 1.05))
    bo_stop_mode = entry_cfg.pop("bo_stop_mode", "fixed")
    if date_min is not None:
        df = df[df["Date"] >= pd.Timestamp(date_min)]
    if date_max is not None:
        df = df[df["Date"] <= pd.Timestamp(date_max)]

    trades = apply_filters(df, entry_cfg)
    if req.exclude_entry_params:
        overlap = apply_filters(df, dict(req.exclude_entry_params))
        trades = trades[~trades.index.isin(overlap.index)]

    base = {
        "strategy_name": req.strategy_name,
        "mode": "path",
        **{f"e_{k}": v for k, v in req.entry_params
           if k not in ("date_min", "date_max")},
        **dict(req.exit_params),
    }
    if len(trades) < 1:
        return {**base, "skipped": True, "reason": "no_trades_after_filter", "n": 0}

    paths = load_paths(req.paths_dir)
    rules = req.exit_rules

    rows = []
    missing = 0
    for t in trades.itertuples(index=False):
        key = (t.Symbol, pd.Timestamp(t.Date).strftime("%Y-%m-%d"))
        path = paths.get(key)
        if path is None:
            missing += 1
            continue
        entry_close, dates, bars = path

        if entry_mode == "rested_breakout":
            hit = find_rested_breakout_entry(
                float(t.High), float(t.Low), bars,
                min_days=bo_min_days, window=bo_window,
                max_premium=bo_max_premium)
            if hit is None:
                continue  # no confirmation = no trade (not "missing")
            ei, entry_px = hit
            if ei + 1 >= len(bars):
                continue
            trade_rules = rules
            if bo_stop_mode == "base_low":
                base_low = float(bars[max(0, ei - 3):ei + 1, 2].min())
                stop_pct = min(12.0, max(3.0,
                               (entry_px - base_low) / entry_px * 100 + 0.5))
                trade_rules = replace(rules, stop_pct=stop_pct)
            res = simulate_path_exit(dates[ei + 1:], bars[ei + 1:],
                                     entry_px, dates[ei], trade_rules)
        elif entry_mode == "gap_day_close":
            res = simulate_path_exit(
                dates, bars, entry_close, np.datetime64(key[1]), rules)
        else:
            raise ValueError(f"unknown entry_mode: {entry_mode}")

        if res is None:
            missing += 1
            continue
        rows.append({
            "Date": pd.Timestamp(t.Date),
            "Return%": res.return_pct,
            "Stopped": res.exit_reason == "stop",
        })

    out = pd.DataFrame(rows)
    metrics = compute_metrics(out) if len(out) else None
    if metrics is None:
        return {**base, "skipped": True, "reason": "no_results", "n": 0}

    return {**base, "skipped": False, "missing_paths": missing, **metrics}
