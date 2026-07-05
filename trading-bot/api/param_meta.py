"""Per-strategy parameter metadata (descriptions + phase/variation tags).

This is the source of truth for the dashboard's Strategies detail page —
the API merges this registry with live config values and the frontend renders
grouped sections with phase badges. Keys match those returned by
`GET /api/strategies` (i.e. post-prefix-strip, so `ep_earnings_min_gap_pct`
becomes `min_gap_pct`).

When a parameter is added to `config.yaml` without a matching entry here, the
dashboard still renders it but falls back to an empty description and the
"base" variation. Update this file alongside any new config key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Variation = Literal["base", "A", "B", "C"]
Phase = Literal["scan", "execute"]


@dataclass(frozen=True)
class ParamMeta:
    """Metadata describing one configuration parameter."""
    key: str
    variation: Variation = "base"
    phase: Phase = "scan"
    description: str = ""


# ── EP Earnings ──────────────────────────────────────────────────────

_EP_EARNINGS: list[ParamMeta] = [
    # Universal filters (apply to A and B)
    ParamMeta("min_gap_pct", "base", "scan",
              "Minimum intraday gap % vs prior close for the earnings gap to qualify."),
    ParamMeta("min_price", "base", "scan",
              "Minimum stock price (filters penny stocks)."),
    ParamMeta("min_market_cap", "base", "scan",
              "Minimum market cap in USD."),
    ParamMeta("require_earnings", "base", "scan",
              "Require the gap day to coincide with an earnings release."),
    ParamMeta("require_open_above_prev_high", "base", "scan",
              "Require today's open to be above yesterday's high."),
    ParamMeta("require_above_200d_sma", "base", "scan",
              "Require today's open to be above the 200-day SMA (long-term uptrend filter)."),
    ParamMeta("min_rvol", "base", "scan",
              "Minimum relative volume: today's volume / 14-day average."),
    ParamMeta("stop_loss_pct", "base", "execute",
              "Default stop-loss % below entry."),
    ParamMeta("max_hold_days", "base", "execute",
              "Maximum hold period in calendar days before forced exit."),

    # Strategy B — the only remaining variant (A and C dropped 2026-05-08)
    ParamMeta("b_min_close_in_range", "B", "scan",
              "Minimum close-in-range score for Strategy B."),
    ParamMeta("b_atr_pct_min", "B", "scan",
              "Minimum ATR% (14-day ATR as % of price)."),
    ParamMeta("b_atr_pct_max", "B", "scan",
              "Maximum ATR% — rejects very volatile stocks."),
]


# ── EP News ──────────────────────────────────────────────────────────

_EP_NEWS: list[ParamMeta] = [
    # Universal filters
    ParamMeta("min_gap_pct", "base", "scan",
              "Minimum intraday gap % vs prior close for the news gap to qualify."),
    ParamMeta("min_price", "base", "scan",
              "Minimum stock price (filters penny stocks)."),
    ParamMeta("min_market_cap", "base", "scan",
              "Minimum market cap in USD. Set higher than EP Earnings to filter thinner names."),
    ParamMeta("exclude_earnings", "base", "scan",
              "Skip stocks with an earnings release today (those are handled by EP Earnings)."),
    ParamMeta("require_open_above_prev_high", "base", "scan",
              "Require today's open to be above yesterday's high."),
    ParamMeta("require_above_200d_sma", "base", "scan",
              "Require today's open to be above the 200-day SMA."),
    ParamMeta("min_rvol", "base", "scan",
              "Minimum relative volume: today's volume / 14-day average."),
    ParamMeta("max_hold_days", "base", "execute",
              "Maximum hold period in trading days for A/B."),

    # Strategy A — NEWS-Tight (-7% stop)
    ParamMeta("a_stop_loss_pct", "A", "execute",
              "Stop-loss % below entry for Strategy A."),
    ParamMeta("a_chg_open_min", "A", "scan",
              "Minimum intraday change-from-open %."),
    ParamMeta("a_chg_open_max", "A", "scan",
              "Maximum intraday change-from-open %."),
    ParamMeta("a_min_close_in_range", "A", "scan",
              "Minimum close-in-range score (0-100)."),
    ParamMeta("a_max_downside_from_open", "A", "scan",
              "Maximum % the stock dipped below its open (tight)."),
    ParamMeta("a_prev_10d_max", "A", "scan",
              "Prior 10-day change % ceiling — must have sold off at least this much."),
    ParamMeta("a_atr_pct_min", "A", "scan",
              "Minimum ATR%."),
    ParamMeta("a_atr_pct_max", "A", "scan",
              "Maximum ATR%."),
    ParamMeta("a_max_volume_m", "A", "scan",
              "Maximum today's volume in millions of shares."),

    # Strategy B — NEWS-Relaxed (-7% stop, switched from -10% on 2026-05-08)
    ParamMeta("b_stop_loss_pct", "B", "execute",
              "Stop-loss % below entry for Strategy B."),
    ParamMeta("b_chg_open_min", "B", "scan",
              "Minimum intraday change-from-open %."),
    ParamMeta("b_chg_open_max", "B", "scan",
              "Maximum intraday change-from-open %."),
    ParamMeta("b_min_close_in_range", "B", "scan",
              "Minimum close-in-range score."),
    ParamMeta("b_max_close_in_range", "B", "scan",
              "Maximum close-in-range score (Strategy B caps the upper end too)."),
    ParamMeta("b_max_downside_from_open", "B", "scan",
              "Maximum % dipped below open (relaxed compared to A)."),
    ParamMeta("b_atr_pct_min", "B", "scan",
              "Minimum ATR%."),
    ParamMeta("b_atr_pct_max", "B", "scan",
              "Maximum ATR%."),
    ParamMeta("b_max_volume_m", "B", "scan",
              "Maximum today's volume in millions of shares."),
]


# ── Breakout ─────────────────────────────────────────────────────────
# (no A/B/C variations — all parameters are base)

_BREAKOUT: list[ParamMeta] = [
    ParamMeta("consolidation_days_min", "base", "scan",
              "Minimum trading days in the consolidation range (nightly scan)."),
    ParamMeta("consolidation_days_max", "base", "scan",
              "Maximum trading days in the consolidation range."),
    ParamMeta("volume_multiplier", "base", "execute",
              "Minimum RVOL vs 20-day average required on breakout."),
    ParamMeta("max_extension_pct", "base", "execute",
              "Max % above opening-range high before skipping the entry (anti-chase guard)."),
]


# ── Episodic Pivot ───────────────────────────────────────────────────
# Note: config.yaml uses `ep_` prefix, not `episodic_pivot_`, so none of these
# keys are surfaced today via the /api/strategies prefix-strip. Metadata is
# still listed so that if the config prefix is later fixed, descriptions are
# already in place.

_EPISODIC_PIVOT: list[ParamMeta] = [
    ParamMeta("min_gap_pct", "base", "scan",
              "Minimum overnight gap % to qualify (e.g. earnings / news catalyst)."),
    ParamMeta("volume_multiplier", "base", "execute",
              "Minimum RVOL vs 20-day average (time-of-day normalized)."),
    ParamMeta("max_extension_pct", "base", "execute",
              "Max % above opening-range high before skipping (anti-chase guard)."),
    ParamMeta("prior_rally_max_pct", "base", "scan",
              "Reject if stock is already up this % over the prior 6 months."),
    ParamMeta("stop_atr_mult", "base", "execute",
              "ATR multiplier cap on stop width."),
]


# ── Parabolic Short (disabled) ───────────────────────────────────────

_PARABOLIC_SHORT: list[ParamMeta] = [
    ParamMeta("min_gain_pct", "base", "scan",
              "Legacy fallback — used if per-cap keys aren't set."),
    ParamMeta("min_gain_pct_largecap", "base", "scan",
              "Large-cap (price > $50): minimum gain % to qualify as parabolic."),
    ParamMeta("min_gain_pct_smallcap", "base", "scan",
              "Small-cap (price < $20): minimum gain % to qualify."),
    ParamMeta("min_days", "base", "scan",
              "Minimum consecutive up days required."),
]


# ── EP Breakout (EP 2.0 Track A) ─────────────────────────────────────

_EP_BREAKOUT: list[ParamMeta] = [
    ParamMeta("min_gap_pct", "base", "scan",
              "Minimum gap % vs prior close for the gap event to qualify."),
    ParamMeta("min_price", "base", "scan",
              "Minimum prior close (filters penny stocks)."),
    ParamMeta("min_market_cap", "base", "scan",
              "BIG filter: minimum market cap ($5B). Large-cap theme leaders only."),
    ParamMeta("min_dollar_vol", "base", "scan",
              "LOUD filter: minimum gap-day dollar volume ($100M). Deliberately "
              "replaces the old share-volume CAP that rejected 2026's winners."),
    ParamMeta("min_atr_pct", "base", "scan",
              "VOLATILE filter: minimum 10-day ATR as % of close."),
    ParamMeta("require_open_above_prev_high", "base", "scan",
              "Require gap-day open above yesterday's high."),
    ParamMeta("require_above_200d_sma", "base", "scan",
              "Require gap-day open above the 200-day SMA."),
    ParamMeta("bo_min_days", "base", "execute",
              "Sessions of rest (never closing below the gap-day low) required "
              "before a breakout entry is allowed."),
    ParamMeta("bo_window", "base", "execute",
              "Sessions to wait for breakout confirmation; the watch row "
              "expires after this many sessions without a trigger."),
    ParamMeta("bo_max_premium_pct", "base", "execute",
              "Chase guard: skip if the confirming close is more than this % "
              "above the gap-day high."),
    ParamMeta("stop_loss_pct", "base", "execute",
              "GTC stop distance below entry."),
    ParamMeta("profit_target_pct", "base", "execute",
              "Price-target partial: sell the target fraction once price "
              "reaches entry x (1 + this%). Checked at 9:40 AM ET."),
    ParamMeta("profit_target_fraction", "base", "execute",
              "Fraction of the position sold at the profit target."),
    ParamMeta("breakeven_trigger_pct", "base", "execute",
              "After a daily close this % above entry, the stop moves to "
              "entry (breakeven lock, EOD check)."),
    ParamMeta("trail_ma_days", "base", "execute",
              "Runner exit: close below this-many-day SMA of closes exits the "
              "remainder at EOD. Active from day 1 for this setup."),
    ParamMeta("max_hold_days", "base", "execute",
              "Hard exit after this many calendar days."),
]


# ── Registry ─────────────────────────────────────────────────────────

PARAM_META: dict[str, list[ParamMeta]] = {
    "ep_earnings": _EP_EARNINGS,
    "ep_news": _EP_NEWS,
    "ep_breakout": _EP_BREAKOUT,
    "breakout": _BREAKOUT,
    "episodic_pivot": _EPISODIC_PIVOT,
    "parabolic_short": _PARABOLIC_SHORT,
}


# ── Phase display metadata (kept close to the registry it describes) ──

PHASE_LABELS: dict[str, dict[str, str]] = {
    "scan": {
        "short": "scan",
        "long": "Scan",
        "description": "Used when screening candidates during the strategy's scan job.",
    },
    "execute": {
        "short": "execute",
        "long": "Execute",
        "description": "Used at entry time when placing orders.",
    },
}


def build_config_params(slug: str, signals_cfg: dict) -> list[dict]:
    """Return an ordered list of {key, value, description, variation, phase}.

    Keys present in the config but missing from PARAM_META fall through with
    an empty description and default (base / scan) tags — this keeps the UI
    resilient to config drift.
    """
    prefix = f"{slug}_"
    live = {
        k[len(prefix):]: v
        for k, v in signals_cfg.items()
        if k.startswith(prefix)
    }

    meta_list = PARAM_META.get(slug, [])
    seen: set[str] = set()
    rows: list[dict] = []

    # 1. Emit registry entries in declared order (for rows present in live config).
    for m in meta_list:
        if m.key in live:
            rows.append({
                "key": m.key,
                "value": live[m.key],
                "description": m.description,
                "variation": m.variation,
                "phase": m.phase,
            })
            seen.add(m.key)

    # 2. Any live keys not in the registry get a placeholder row at the end.
    for k, v in live.items():
        if k in seen:
            continue
        rows.append({
            "key": k,
            "value": v,
            "description": "",
            "variation": "base",
            "phase": "scan",
        })

    return rows
