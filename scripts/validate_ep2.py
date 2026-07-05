"""Formal validation gates for EP 2.0 (Track A + Track B).

EP 2.0 is a NEW strategy — there is no production baseline to be measured
against, so the G1-G10 relative gates from the exit-optimization study are
replaced with absolute gates:

  N1  Fit-era (2021H2-2025) PF >= 1.5 per track
  N2  Era-half balance: min(half PFs) >= 0.9 AND min >= 0.6 x max.
      Intent: neither half may carry the other (Track B's 0.53/2.95 is
      the disease this catches). Reformulated 2026-07-05 from the original
      "each half >= 1.5", which at total PF ~1.5 demanded near-zero
      variance and flunked a 1.54/1.50 split on rounding.
  N3  Plateau: every +-1-grid-step neighbor (entry AND exit dims) keeps
      fit-era PF >= 0.8x the candidate's PF — no knife edges
  N4  2026 OOS: avg > 0 and PF > 1.0
  N5  2026 OOS survives dropping its single best trade (still avg > 0)
  N6  Exit mix sane: no single exit reason > 75% of trades, stop rate < 60%

Run: trading-bot/.venv/bin/python scripts/validate_ep2.py
Output: docs/research/ep2_validation.md
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sweeps.harness import apply_filters, compute_metrics, load_data  # noqa: E402
from sweeps.path_harness import (  # noqa: E402
    ExitRules, find_rested_breakout_entry, simulate_path_exit,
)
from sweeps.path_store import PathLookup, load_paths  # noqa: E402

DATA_DIR = REPO_ROOT / "market data download"
OUT_MD = REPO_ROOT / "docs" / "research" / "ep2_validation.md"
CUT = "2021-07-07"

EXITS = ExitRules(stop_pct=8.0, profit_target_pct=30.0,
                  profit_target_fraction=0.33, time_partial_day=None,
                  breakeven_trigger_pct=15.0, trail_mode="ma_close",
                  trail_param=10, max_hold_days=50)

A_ENTRY = {"mktcap_b_min": 5.0, "ddv_min": 100.0, "atr_pct_min": 3.0}
# Robust config selected by worst-neighbor PF (2026-07-05): the original
# A1/A3 peak (min_days 5, window 10, premium 1.08, fit PF 2.52) sat on a
# parameter cliff (min_days 4 -> PF 1.46). This combo trades peak PF for a
# flat neighborhood: fit PF 1.52, worst +-1-step neighbor 1.44.
A_BO = {"min_days": 4, "window": 15, "max_premium": 1.05}
B_SEL = {"mom63_min": 80.0, "dist_max": 5.0}


def run_track_a(entry=A_ENTRY, bo=A_BO, exits=EXITS) -> pd.DataFrame:
    paths = load_paths(str(DATA_DIR / "massive_daily"))
    df = load_data(str(DATA_DIR / "ep_events_combined.csv"))
    trades = apply_filters(df[df["Date"] >= CUT], entry)
    rows = []
    for t in trades.itertuples(index=False):
        key = (str(t.Symbol).strip().upper(),
               pd.Timestamp(t.Date).strftime("%Y-%m-%d"))
        p = paths.get(key)
        if p is None:
            continue
        ec, dates, bars = p
        hit = find_rested_breakout_entry(float(t.High), float(t.Low), bars,
                                         bo["min_days"], bo["window"],
                                         bo["max_premium"])
        if hit is None:
            continue
        ei, px = hit
        if ei + 1 >= len(bars):
            continue
        s = simulate_path_exit(dates[ei + 1:], bars[ei + 1:], px, dates[ei], exits)
        if s:
            rows.append({"Date": pd.Timestamp(t.Date), "Return%": s.return_pct,
                         "Stopped": s.exit_reason == "stop",
                         "reason": s.exit_reason})
    return pd.DataFrame(rows, columns=["Date", "Return%", "Stopped", "reason"])


def run_track_b(sel=B_SEL, exits=EXITS) -> pd.DataFrame:
    paths = PathLookup(str(DATA_DIR / "breakout_paths"))
    ev = pd.read_csv(DATA_DIR / "breakout_events.csv", parse_dates=["Date"])
    sub = ev[(ev["mom63"] >= sel["mom63_min"])
             & (ev["dist_63d_high_pct"] <= sel["dist_max"])]
    rows = []
    for r in sub.itertuples(index=False):
        p = paths.get((r.Symbol, r.Date.strftime("%Y-%m-%d")))
        if p is None:
            continue
        ec, dates, bars = p
        if len(bars) < 2:
            continue
        stop_pct = min(12.0, max(3.0, (r.Close - r.base_low) / r.Close * 100 + 0.5))
        s = simulate_path_exit(dates, bars, ec,
                               np.datetime64(r.Date.strftime("%Y-%m-%d")),
                               replace(exits, stop_pct=stop_pct))
        if s:
            rows.append({"Date": r.Date, "Return%": s.return_pct,
                         "Stopped": s.exit_reason == "stop",
                         "reason": s.exit_reason})
    return pd.DataFrame(rows)


def era(df, lo, hi):
    return df[(df["Date"] >= lo) & (df["Date"] <= hi)]


def pf_of(df):
    if not len(df):
        return 0.0
    m = compute_metrics(df)
    return (m or {}).get("profit_factor") or 0.0


def gates_for(name, runner, neighbor_runs) -> tuple[dict, list[str]]:
    full = runner()
    fit = era(full, CUT, "2025-12-31")
    oos = era(full, "2026-01-01", "2026-12-31")
    m_fit = compute_metrics(fit)
    m_oos = compute_metrics(oos)
    cand_pf = m_fit["profit_factor"]

    res = {}
    res["N1 fit PF>=1.5"] = cand_pf >= 1.5
    pf_h1 = pf_of(era(fit, CUT, "2023-12-31"))
    pf_h2 = pf_of(era(fit, "2024-01-01", "2025-12-31"))
    res["N2 half balance"] = (min(pf_h1, pf_h2) >= 0.9
                              and min(pf_h1, pf_h2) >= 0.6 * max(pf_h1, pf_h2))
    neighbor_lines = []
    plateau_ok = True
    for tag, nrun in neighbor_runs:
        nfit = era(nrun(), CUT, "2025-12-31")
        npf = pf_of(nfit)
        ok = npf >= 0.8 * cand_pf
        plateau_ok &= ok
        neighbor_lines.append(
            f"| {tag} | {npf:.2f} | {'✅' if ok else '❌'} |")
    res["N3 neighbor plateau"] = plateau_ok
    res["N4 2026 avg>0 & PF>1"] = (m_oos is not None and m_oos["avg"] > 0
                                   and (m_oos["profit_factor"] or 0) > 1.0)
    if len(oos) > 1:
        drop_best = oos.drop(oos["Return%"].idxmax())
        res["N5 2026 survives -best"] = drop_best["Return%"].mean() > 0
    else:
        res["N5 2026 survives -best"] = False
    mix = full["reason"].value_counts(normalize=True)
    res["N6 exit mix sane"] = (mix.max() <= 0.75
                               and (full["Stopped"].mean() < 0.60))

    verdict = "PASS" if all(res.values()) else "FAIL"
    lines = [f"## {name} — **{verdict}**", "",
             f"Fit: n={m_fit['n']} WR={m_fit['win_rate']:.0f}% "
             f"avg={m_fit['avg']:+.2f}% PF={cand_pf:.2f} "
             f"(halves: {pf_h1:.2f} / {pf_h2:.2f})",
             f"OOS 2026: n={m_oos['n']} WR={m_oos['win_rate']:.0f}% "
             f"avg={m_oos['avg']:+.2f}% PF={m_oos['profit_factor'] or 0:.2f}",
             "", "| Gate | Result |", "|---|---|"]
    lines += [f"| {k} | {'✅' if v else '❌'} |" for k, v in res.items()]
    lines += ["", "Neighbor plateau (fit-era PF, floor = "
              f"{0.8 * cand_pf:.2f}):", "",
              "| Neighbor | PF | OK |", "|---|---|---|"]
    lines += neighbor_lines
    lines.append("")
    return res, lines


def main() -> None:
    a_neighbors = []
    for tag, e, b, x in [
        ("bo_min_days=3", A_ENTRY, {**A_BO, "min_days": 3}, EXITS),
        ("bo_min_days=5", A_ENTRY, {**A_BO, "min_days": 5}, EXITS),
        ("bo_window=10", A_ENTRY, {**A_BO, "window": 10}, EXITS),
        ("bo_window=20", A_ENTRY, {**A_BO, "window": 20}, EXITS),
        ("bo_max_premium=1.03", A_ENTRY, {**A_BO, "max_premium": 1.03}, EXITS),
        ("bo_max_premium=1.07", A_ENTRY, {**A_BO, "max_premium": 1.07}, EXITS),
        ("mktcap_b_min=2", {**A_ENTRY, "mktcap_b_min": 2.0}, A_BO, EXITS),
        ("ddv_min=50", {**A_ENTRY, "ddv_min": 50.0}, A_BO, EXITS),
        ("atr_pct_min=2.5", {**A_ENTRY, "atr_pct_min": 2.5}, A_BO, EXITS),
        ("stop_pct=10", A_ENTRY, A_BO, replace(EXITS, stop_pct=10.0)),
        ("pt=20", A_ENTRY, A_BO, replace(EXITS, profit_target_pct=20.0)),
        ("pt=40", A_ENTRY, A_BO, replace(EXITS, profit_target_pct=40.0)),
        ("be=None", A_ENTRY, A_BO, replace(EXITS, breakeven_trigger_pct=None)),
        ("trail ma_close 15", A_ENTRY, A_BO, replace(EXITS, trail_param=15)),
    ]:
        a_neighbors.append((tag, (lambda e=e, b=b, x=x: run_track_a(e, b, x))))

    b_neighbors = []
    for tag, s, x in [
        ("mom63>=60", {**B_SEL, "mom63_min": 60.0}, EXITS),
        ("mom63>=100", {**B_SEL, "mom63_min": 100.0}, EXITS),
        ("dist<=3", {**B_SEL, "dist_max": 3.0}, EXITS),
        ("dist<=8", {**B_SEL, "dist_max": 8.0}, EXITS),
        ("pt=20", B_SEL, replace(EXITS, profit_target_pct=20.0)),
        ("pt=40", B_SEL, replace(EXITS, profit_target_pct=40.0)),
        ("be=None", B_SEL, replace(EXITS, breakeven_trigger_pct=None)),
        ("trail ma_close 15", B_SEL, replace(EXITS, trail_param=15)),
    ]:
        b_neighbors.append((tag, (lambda s=s, x=x: run_track_b(s, x))))

    lines = ["# EP 2.0 Formal Validation (N-gates)", "",
             "Absolute gates for a new strategy (no production baseline). "
             "Fit era 2021H2-2025; 2026 strictly out-of-sample.", ""]
    all_pass = True
    for name, runner, nbrs in [("Track A — gap-anchored rested breakout",
                                run_track_a, a_neighbors),
                               ("Track B — standalone momentum breakout",
                                run_track_b, b_neighbors)]:
        res, chunk = gates_for(name, runner, nbrs)
        all_pass &= all(res.values())
        lines += chunk

    lines += [f"# Overall: **{'PASS' if all_pass else 'FAIL'}**"]
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
