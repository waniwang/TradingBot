"""Phase 3 diagnosis: are the EP swing strategies turning winners into losers?

Tests Shay's hypothesis (profits not taken early enough -> winners round-trip
into -7% stops) with real daily price paths, BEFORE any optimization.

Three cohorts, same tables:
  1. Live 2026 trades (interim source: 2026_Full_EP_Strategies_BC_Trades.csv
     until the GH-Actions prod dump lands; pass --live-csv to swap in).
  2. All 2026 dataset events passing CURRENT production entry filters.
  3. 2021-H2..2025 events passing the same filters (base rate for context).

Key stats per cohort: % stopped within the 50d hold, % of stop-outs that
were >= +3/+5/+8/+10/+15% green BEFORE the stop fill, median day of peak,
median giveback (MFE - realized under production exit rules).

Decision point (from the plan): if <25% of stop-outs were ever >= +5% green
first, the hypothesis is weak -> optimization budget shifts toward entry
filters/regime. If high, exits are the lever.

Run: trading-bot/.venv/bin/python scripts/ep_path_diagnosis.py
Output: docs/research/ep_2026_diagnosis.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sweeps.harness import apply_filters, load_data  # noqa: E402
from sweeps.path_harness import (  # noqa: E402
    ExitRules, PRODUCTION_RULES, simulate_path_exit,
)
from sweeps.path_store import load_paths  # noqa: E402

DATA_DIR = REPO_ROOT / "market data download"
CACHE_DIR = DATA_DIR / "massive_daily"
OUT_MD = REPO_ROOT / "docs" / "research" / "ep_2026_diagnosis.md"
PLAN_BOUNDARY = pd.Timestamp("2021-07-07")

STRATEGY_FILTERS = {
    "Earnings B": {
        "dataset": "EARNINGS",
        "cfg": {"chg_open_min": 0.0, "close_in_range_min": 50.0,
                "atr_pct_min": 2.0, "atr_pct_max": 5.0, "mktcap_b_min": 0.8},
    },
    "News A": {
        "dataset": "NEWS",
        "cfg": {"chg_open_min": 2.0, "chg_open_max": 10.0,
                "close_in_range_min": 50.0, "downside_from_open_max": 3.0,
                "atr_pct_min": 3.0, "atr_pct_max": 7.0,
                "volume_max": 3_000_000, "mktcap_b_min": 1.0},
    },
    "News B": {
        "dataset": "NEWS",
        "cfg": {"chg_open_min": 2.0, "chg_open_max": 10.0,
                "close_in_range_min": 30.0, "close_in_range_max": 80.0,
                "downside_from_open_max": 6.0, "atr_pct_min": 3.0,
                "atr_pct_max": 7.0, "volume_max": 5_000_000,
                "mktcap_b_min": 1.0},
    },
}
GREEN_LEVELS = [3.0, 5.0, 8.0, 10.0, 15.0]


def compute_path_stats(dates, bars, entry_price, entry_date,
                       stop_pct=7.0, horizon_days=50) -> dict | None:
    """Walk the path until the -stop_pct GTC stop fills or the calendar
    horizon ends. Returns MFE/MAE, peak timing, green-before-stop flags."""
    if len(bars) == 0 or entry_price <= 0:
        return None
    stop = entry_price * (1 - stop_pct / 100.0)
    r = lambda px: (px - entry_price) / entry_price * 100.0  # noqa: E731

    mfe = mae = 0.0
    day_of_mfe = 0
    stopped = False
    stop_day = None
    end_i = len(bars) - 1
    for i in range(len(bars)):
        cal_day = int((dates[i] - np.datetime64(entry_date)) / np.timedelta64(1, "D"))
        o, h, l = float(bars[i, 0]), float(bars[i, 1]), float(bars[i, 2])
        # gap-through: MFE must not credit a high AFTER the stop fill
        if o <= stop or l <= stop:
            stopped = True
            stop_day = cal_day
            mae = min(mae, r(o) if o <= stop else -stop_pct)
            end_i = i
            break
        if r(h) > mfe:
            mfe, day_of_mfe = r(h), cal_day
        mae = min(mae, r(l))
        if cal_day >= horizon_days:
            end_i = i
            break

    return {
        "stopped": stopped,
        "stop_day": stop_day,
        "mfe_before_exit": mfe,       # highs credited only before the stop fill
        "mae": mae,
        "day_of_mfe": day_of_mfe,
        "bars_used": end_i + 1,
    }


def cohort_rows(events: list[dict], paths, label: str) -> pd.DataFrame:
    """events: [{symbol, date(iso), entry_price(optional), variant}]."""
    rows = []
    missing = 0
    for ev in events:
        p = paths.get((ev["symbol"], ev["date"]))
        if p is None:
            missing += 1
            continue
        entry_close, dates, bars = p
        entry = ev.get("entry_price") or entry_close
        st = compute_path_stats(dates, bars, entry, ev["date"])
        if st is None:
            missing += 1
            continue
        sim = simulate_path_exit(dates, bars, entry, np.datetime64(ev["date"]),
                                 PRODUCTION_RULES)
        rows.append({
            "cohort": label, "symbol": ev["symbol"], "date": ev["date"],
            "variant": ev.get("variant", ""),
            "realized_pct": sim.return_pct if sim else np.nan,
            "exit_reason": sim.exit_reason if sim else "",
            "exit_day": sim.exit_day if sim else np.nan,
            **st,
        })
    df = pd.DataFrame(rows)
    df.attrs["missing"] = missing
    return df


def summarize(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0}
    stopped = df[df["stopped"]]
    out = {
        "n": n,
        "stop_rate": len(stopped) / n * 100,
        "avg_realized": df["realized_pct"].mean(),
        "win_rate": (df["realized_pct"] > 0).mean() * 100,
        "median_day_of_mfe": df["day_of_mfe"].median(),
        "median_mfe": df["mfe_before_exit"].median(),
        "median_giveback": (df["mfe_before_exit"] - df["realized_pct"]).median(),
    }
    for lvl in GREEN_LEVELS:
        out[f"stops_green_{lvl:g}"] = (
            (stopped["mfe_before_exit"] >= lvl).mean() * 100 if len(stopped) else np.nan
        )
    return out


def fmt_summary_table(summaries: dict[str, dict]) -> list[str]:
    hdr = ("| Cohort | n | Stop % | Stops green ≥+3% | ≥+5% | ≥+8% | ≥+10% "
           "| ≥+15% | Med day of peak | Med MFE | Med giveback | Avg realized |")
    sep = "|" + "---|" * 12
    lines = [hdr, sep]
    for name, s in summaries.items():
        if s.get("n", 0) == 0:
            lines.append(f"| {name} | 0 | — | — | — | — | — | — | — | — | — | — |")
            continue
        g = lambda k: ("—" if pd.isna(s[k]) else f"{s[k]:.0f}%")  # noqa: E731
        lines.append(
            f"| {name} | {s['n']} | {s['stop_rate']:.0f}% "
            f"| {g('stops_green_3')} | {g('stops_green_5')} | {g('stops_green_8')} "
            f"| {g('stops_green_10')} | {g('stops_green_15')} "
            f"| D{s['median_day_of_mfe']:.0f} | {s['median_mfe']:+.1f}% "
            f"| {s['median_giveback']:.1f}pp | {s['avg_realized']:+.2f}% |"
        )
    return lines


def smell_test(events_by_strategy, paths) -> list[str]:
    """Single-scenario check: production vs sell-50%-at-+8% vs +10%/40%."""
    variants = {
        "Production (D19 partial only)": PRODUCTION_RULES,
        "+ sell 50% at +8%": ExitRules(profit_target_pct=8.0,
                                       profit_target_fraction=0.5),
        "+ sell 40% at +10%": ExitRules(profit_target_pct=10.0,
                                        profit_target_fraction=0.4),
    }
    lines = ["| Strategy / cohort | Exit variant | n | WR | Avg % | PF |",
             "|---|---|---|---|---|---|"]
    for strat, events in events_by_strategy.items():
        for vname, rules in variants.items():
            rets = []
            for ev in events:
                p = paths.get((ev["symbol"], ev["date"]))
                if p is None:
                    continue
                entry_close, dates, bars = p
                sim = simulate_path_exit(dates, bars,
                                         ev.get("entry_price") or entry_close,
                                         np.datetime64(ev["date"]), rules)
                if sim:
                    rets.append(sim.return_pct)
            if not rets:
                continue
            a = np.array(rets)
            wins, losses = a[a > 0], a[a <= 0]
            pf = (wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else np.inf
            lines.append(
                f"| {strat} | {vname} | {len(a)} | {(a > 0).mean()*100:.0f}% "
                f"| {a.mean():+.2f}% | {pf:.2f} |")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-csv", type=Path,
                    default=REPO_ROOT / "2026_Full_EP_Strategies_BC_Trades.csv")
    args = ap.parse_args()

    paths = load_paths(str(CACHE_DIR))

    # ---- Cohort 1: live trades ----
    lt = pd.read_csv(args.live_csv)
    live_events = [{
        "symbol": str(r.Symbol).strip().upper(),
        "date": pd.Timestamp(r._4).strftime("%Y-%m-%d"),  # Entry Date
        "entry_price": float(r._5),                        # Entry Price
        "variant": f"{r.Type} {r.Strategy}",
    } for r in lt.itertuples(index=False)]
    live_df = cohort_rows(live_events, paths, "Live 2026 trades")

    # ---- Cohorts 2+3: dataset events through production filters ----
    datasets = {
        "EARNINGS_2026": load_data(str(DATA_DIR / "2026 EP Selection EARNINGS.xlsx")),
        "NEWS_2026": load_data(str(DATA_DIR / "2026 EP Selection NEWS V2.xlsx")),
        "EARNINGS_HIST": load_data(str(DATA_DIR / "2020-2025 EP Selection EARNINGS.xlsx")),
        "NEWS_HIST": load_data(str(DATA_DIR / "2020-2025 EP Selection NEWS V2.xlsx")),
    }
    summaries: dict[str, dict] = {}
    per_strategy_2026: dict[str, list[dict]] = {}
    cohort_frames = [live_df]
    summaries["Live 2026 trades (all)"] = summarize(live_df)

    for strat, spec in STRATEGY_FILTERS.items():
        for era, key in [("2026", f"{spec['dataset']}_2026"),
                         ("2021H2-2025", f"{spec['dataset']}_HIST")]:
            df = apply_filters(datasets[key], spec["cfg"])
            if era != "2026":
                df = df[df["Date"] >= PLAN_BOUNDARY]
            # News: A wins on overlap — drop A-passing rows from B.
            if strat == "News B":
                a_idx = apply_filters(datasets[key],
                                      STRATEGY_FILTERS["News A"]["cfg"]).index
                df = df[~df.index.isin(a_idx)]
            events = [{"symbol": str(r.Symbol).strip().upper(),
                       "date": pd.Timestamp(r.Date).strftime("%Y-%m-%d")}
                      for r in df.itertuples(index=False)]
            label = f"{strat} — {era} candidates"
            cdf = cohort_rows(events, paths, label)
            cohort_frames.append(cdf)
            summaries[label] = summarize(cdf)
            if era == "2026":
                per_strategy_2026[strat] = events

    # ---- Report ----
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    all_df = pd.concat(cohort_frames, ignore_index=True)

    stopped_2026 = all_df[all_df["cohort"].str.contains("2026 candidates")
                          & all_df["stopped"]]
    verdict_green5 = (
        (stopped_2026["mfe_before_exit"] >= 5.0).mean() * 100
        if len(stopped_2026) else float("nan")
    )
    hypothesis = ("CONFIRMED — exits are the lever"
                  if verdict_green5 >= 25.0
                  else "WEAK — look at entries/regime instead")

    lines = [
        "# EP 2026 Diagnosis — Are We Turning Winners Into Losers?",
        "",
        "Generated by `scripts/ep_path_diagnosis.py` on Massive daily paths. "
        "Production exit rules: -7% GTC stop, D19+ 40% partial (stop -> "
        "entry x1.05), 50d max hold. Realized returns are path-simulated "
        "under production rules; 2026-candidate cohorts include trades the "
        "bot missed (bigger sample than live fills).",
        "",
        f"## Verdict: {hypothesis}",
        "",
        f"Across all 2026 candidate stop-outs, **{verdict_green5:.0f}%** were "
        f">= +5% green before the -7% stop filled (decision threshold: 25%).",
        "",
        "## Cohort table",
        "",
        *fmt_summary_table(summaries),
        "",
        "Notes: 'Stops green >= +X%' = of trades that hit the -7% stop, the "
        "share whose high reached +X% over entry BEFORE the stop fill. "
        "'Med giveback' = median (MFE - realized): how much open profit the "
        "average trade surrenders under current exits.",
        "",
        "## Smell test — one early profit-target on top of production rules",
        "",
        "(2026 candidate cohorts only; NOT an optimization — just directional "
        "evidence for where the sweep should focus.)",
        "",
        *smell_test(per_strategy_2026, paths),
        "",
        "## Live 2026 trades — per-trade detail",
        "",
        "| Symbol | Variant | Entry | Realized | Exit | MFE before exit | Peak day | Stopped |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in live_df.sort_values("date").itertuples(index=False):
        lines.append(
            f"| {r.symbol} | {r.variant} | {r.date} | {r.realized_pct:+.1f}% "
            f"| {r.exit_reason} (D{r.exit_day:.0f}) | {r.mfe_before_exit:+.1f}% "
            f"| D{r.day_of_mfe:.0f} | {'Y' if r.stopped else ''} |")

    missing_live = live_df.attrs.get("missing", 0)
    if missing_live:
        lines += ["", f"({missing_live} live trades skipped — no Massive path "
                  f"or excluded by validation.)"]

    OUT_MD.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[: 30 + len(summaries)]))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
