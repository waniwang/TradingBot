"""Phase 2 gate: validate Massive daily bars against Spikeet gap-day OHLC.

For every event in the four EP Selection datasets, join the Spikeet gap-day
OHLC against the Massive bar on the same date and flag mismatches beyond
tolerance. Events whose mismatch is a clean split ratio are classified
`split_adjust` (expected: Spikeet is as-traded, Massive is adjusted).

Also cross-checks Spikeet's `50thD change%` against the Massive path's
50th-bar close return on a random sample — quantifying the
checkpoint-vs-path baseline shift BEFORE anyone blames the new simulator.

Gate: >=98% of events must either match within tolerance or be explained
(split / known truncation). Unexplained mismatches are listed and excluded
from downstream simulation via the exclusions CSV.

Run: trading-bot/.venv/bin/python scripts/validate_massive_vs_spikeet.py
Outputs:
    docs/research/massive_validation.md
    market data download/massive_daily/_exclusions.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DATA_DIR = REPO_ROOT / "market data download"
CACHE_DIR = DATA_DIR / "massive_daily"
OUT_MD = REPO_ROOT / "docs" / "research" / "massive_validation.md"
OUT_EXCL = CACHE_DIR / "_exclusions.csv"

DATASETS = {
    "2020-2025 EARNINGS": DATA_DIR / "2020-2025 EP Selection EARNINGS.xlsx",
    "2020-2025 NEWS": DATA_DIR / "2020-2025 EP Selection NEWS V2.xlsx",
    "2026 EARNINGS": DATA_DIR / "2026 EP Selection EARNINGS.xlsx",
    "2026 NEWS": DATA_DIR / "2026 EP Selection NEWS V2.xlsx",
}

TOL = 0.005          # 0.5% per-field tolerance
SAMPLE_50D = 200     # events for the checkpoint cross-check
RNG_SEED = 42

# Massive Starter plan boundary (rolling 5y; see fetch_massive_daily_paths).
# Events before this date are excluded from path sims BY PLAN DECISION
# (2026-07-05, Shay: proceed with 4.5 years) — the gate only judges
# in-window events.
PLAN_BOUNDARY = pd.Timestamp("2021-07-07")


def load_symbol(symbol: str, _cache={}) -> pd.DataFrame | None:
    if symbol in _cache:
        return _cache[symbol]
    f = CACHE_DIR / f"{symbol}.csv"
    df = pd.read_csv(f, index_col="date") if f.exists() else None
    _cache[symbol] = df
    return df


def is_clean_split_ratio(r: float) -> bool:
    """True when r looks like a split factor: n, 1/n, or n/m for small ints."""
    if r <= 0:
        return False
    for num in range(1, 51):
        for den in (1, 2, 3, 4, 5, 10, 20):
            if abs(r - num / den) / (num / den) < 0.01 and (num, den) != (1, 1):
                return True
    return False


OTC_SUFFIX_RE = __import__("re").compile(r"^[A-Z]{4}[FY]$|^[A-Z]{4}Q$")


def check_event(row) -> dict:
    sym = str(row.Symbol).strip().upper()
    date_iso = pd.Timestamp(row.Date).strftime("%Y-%m-%d")
    out = {"symbol": sym, "date": date_iso}

    # Foreign ordinaries / unsponsored ADRs (…F, …Y) and bankruptcy tickers
    # (…Q) trade OTC — the live bot CANNOT trade them on Alpaca, so they are
    # excluded from the backtest universe entirely (universe alignment, not
    # data cleaning). Their Spikeet-vs-Massive diffs are thin-print artifacts.
    if OTC_SUFFIX_RE.match(sym):
        out["status"] = "untradeable_otc"
        return out

    bars = load_symbol(sym)
    if bars is None:
        out["status"] = "missing_symbol"
        return out
    if date_iso not in bars.index:
        out["status"] = "missing_bar"
        return out

    bar = bars.loc[date_iso]
    diffs = {}
    ratios = []
    for spike_col, mass_col in [("Open", "open"), ("High", "high"),
                                ("Low", "low"), ("Close", "close")]:
        sv = float(getattr(row, spike_col))
        mv = float(bar[mass_col])
        if sv <= 0 or mv <= 0:
            out["status"] = "bad_price"
            return out
        diffs[spike_col] = abs(sv - mv) / sv
        ratios.append(sv / mv)

    out["max_diff_pct"] = max(diffs.values()) * 100
    if max(diffs.values()) < TOL:
        out["status"] = "match"
        return out

    # Consistent ratio across O/H/L/C that is a clean split factor?
    r = float(np.mean(ratios))
    if max(abs(x - r) / r for x in ratios) < 0.01 and is_clean_split_ratio(r):
        out["status"] = "split_adjust"
        out["ratio"] = round(r, 4)
        return out

    out["status"] = "mismatch"
    out["spikeet_close"] = float(row.Close)
    out["massive_close"] = float(bar["close"])
    return out


def checkpoint_crosscheck(events: pd.DataFrame) -> dict:
    """Spikeet `50thD change%` vs Massive 50th-forward-bar close return."""
    sample = events.dropna(subset=["50thD change%"]).sample(
        min(SAMPLE_50D, len(events)), random_state=RNG_SEED)
    diffs = []
    for row in sample.itertuples(index=False):
        sym = str(row.Symbol).strip().upper()
        date_iso = pd.Timestamp(row.Date).strftime("%Y-%m-%d")
        bars = load_symbol(sym)
        if bars is None or date_iso not in bars.index:
            continue
        idx = bars.index.get_loc(date_iso)
        if idx + 50 >= len(bars):
            continue
        entry = float(bars.iloc[idx]["close"])
        c50 = float(bars.iloc[idx + 50]["close"])
        path_ret = (c50 - entry) / entry * 100
        diffs.append(path_ret - float(getattr(row, "_18")))  # 50thD change% position
    if not diffs:
        return {"n": 0}
    a = np.array(diffs)
    return {"n": len(a), "mean_diff": a.mean(), "median_abs": np.median(np.abs(a)),
            "p90_abs": np.percentile(np.abs(a), 90)}


def main() -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    all_rows = []
    summaries = []
    frames = {}

    pre_boundary_counts = {}
    for name, path in DATASETS.items():
        df = pd.read_excel(path)
        df["Date"] = pd.to_datetime(df["Date"])
        pre_boundary_counts[name] = int((df["Date"] < PLAN_BOUNDARY).sum())
        df = df[df["Date"] >= PLAN_BOUNDARY]
        frames[name] = df
        results = [check_event(r) for r in df.itertuples(index=False)]
        rdf = pd.DataFrame(results)
        rdf["dataset"] = name
        all_rows.append(rdf)
        counts = rdf["status"].value_counts().to_dict()
        otc = counts.get("untradeable_otc", 0)
        n = len(rdf) - otc  # gate judges the TRADEABLE universe only
        ok = counts.get("match", 0) + counts.get("split_adjust", 0)
        summaries.append({
            "dataset": name, "events": n, "otc_excluded": otc,
            "match": counts.get("match", 0),
            "split_adjust": counts.get("split_adjust", 0),
            "missing_symbol": counts.get("missing_symbol", 0),
            "missing_bar": counts.get("missing_bar", 0),
            "mismatch": counts.get("mismatch", 0),
            "explained_pct": ok / n * 100 if n else 0.0,
        })

    res = pd.concat(all_rows, ignore_index=True)
    sm = pd.DataFrame(summaries)

    # Checkpoint cross-check on the biggest dataset
    big = frames["2020-2025 NEWS"]
    # Positional name for '50thD change%' differs via itertuples; re-resolve:
    col_idx = list(big.columns).index("50thD change%")
    cc = checkpoint_crosscheck(big.rename(columns={"50thD change%": "50thD change%"}))

    # Everything not match/split-explained is excluded from path simulation:
    # OTC (untradeable), mismatches (ticker reuse / bad prints), missing data.
    bad = res[res["status"].isin(
        ["mismatch", "missing_symbol", "missing_bar", "untradeable_otc",
         "bad_price"])]
    bad.to_csv(OUT_EXCL, index=False)

    tradeable = res[res["status"] != "untradeable_otc"]
    with_data = tradeable[~tradeable["status"].isin(
        ["missing_symbol", "missing_bar"])]
    n_good = len(with_data[with_data["status"].isin(["match", "split_adjust"])])

    # Gate A — ACCURACY: of events Massive has data for, >=98% must match or
    # be split-explained; the rest are excluded from simulation.
    accuracy_pct = n_good / len(with_data) * 100 if len(with_data) else 0.0
    # Gate B — COVERAGE: events lost to provider gaps (renames/delistings
    # Massive can't serve) must stay under 2% of the tradeable universe.
    n_missing = len(tradeable) - len(with_data)
    missing_pct = n_missing / len(tradeable) * 100 if len(tradeable) else 100.0

    gate_a = "PASS" if accuracy_pct >= 98.0 else "FAIL"
    gate_b = "PASS" if missing_pct <= 2.0 else "FAIL"
    gate = "PASS" if (gate_a == "PASS" and gate_b == "PASS") else "FAIL"
    overall_explained = accuracy_pct

    lines = [
        "# Massive vs Spikeet Price Validation",
        "",
        f"Generated by `scripts/validate_massive_vs_spikeet.py`. "
        f"Tolerance: {TOL*100:.1f}% per OHLC field.",
        "",
        f"## Gate: **{gate}**",
        "",
        f"- Gate A (accuracy): **{gate_a}** — {accuracy_pct:.2f}% of "
        f"{len(with_data):,} events-with-data match or are split-explained "
        f"(need >=98%); the {len(with_data) - n_good} others are excluded "
        f"from simulation",
        f"- Gate B (coverage): **{gate_b}** — {n_missing} of "
        f"{len(tradeable):,} tradeable in-window events ({missing_pct:.2f}%) "
        f"have no Massive path (renames/delistings; tolerable <=2%), "
        f"excluded and listed in the CSV",
        "",
        f"Scope: events on/after {PLAN_BOUNDARY.date()} (Massive Starter 5y "
        f"window). Excluded by plan decision: "
        + ", ".join(f"{k}: {v}" for k, v in pre_boundary_counts.items()),
        "",
        "OTC foreign ordinaries / bankruptcy tickers (…F/…Y/…Q) are excluded "
        "from the gate AND from all path simulations — the live bot cannot "
        "trade them on Alpaca (universe alignment).",
        "",
        "| Dataset | Tradeable | OTC excl | Match | Split-adj | Missing sym | Missing bar | Mismatch | Explained % |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['dataset']} | {s['events']} | {s['otc_excluded']} "
            f"| {s['match']} | {s['split_adjust']} "
            f"| {s['missing_symbol']} | {s['missing_bar']} | {s['mismatch']} "
            f"| {s['explained_pct']:.2f}% |"
        )

    lines += [
        "",
        "## Checkpoint cross-check (Spikeet 50thD vs Massive 50th-bar close)",
        "",
    ]
    if cc.get("n"):
        lines += [
            f"- Sample: {cc['n']} events (2020-2025 NEWS)",
            f"- Mean diff (path - checkpoint): {cc['mean_diff']:+.2f}pp",
            f"- Median |diff|: {cc['median_abs']:.2f}pp",
            f"- P90 |diff|: {cc['p90_abs']:.2f}pp",
            "",
            "Non-zero diffs here quantify the checkpoint-data noise floor: "
            "Spikeet's 50thD column and a true 50-trading-day path CAN differ "
            "(holiday alignment, adjusted vs as-traded). This is the baseline "
            "shift to expect when the path simulator restates old backtests.",
        ]
    else:
        lines.append("- No usable sample (paths missing?)")

    excluded = bad.groupby("dataset")["symbol"].count().to_dict() if len(bad) else {}
    lines += [
        "",
        f"## Exclusions ({len(bad)} events) -> `{OUT_EXCL.name}`",
        "",
        "Events with missing/unexplained prices are EXCLUDED from path "
        "simulation (listed in the CSV): "
        + (", ".join(f"{k}: {v}" for k, v in excluded.items()) if excluded else "none"),
    ]
    if len(bad):
        worst = bad[bad["status"] == "mismatch"].nlargest(
            min(15, len(bad)), "max_diff_pct", keep="all")
        if len(worst):
            lines += ["", "Worst unexplained mismatches:", "",
                      "| Symbol | Date | Dataset | Max diff % | Spikeet close | Massive close |",
                      "|---|---|---|---|---|---|"]
            for r in worst.itertuples(index=False):
                lines.append(
                    f"| {r.symbol} | {r.date} | {r.dataset} | {r.max_diff_pct:.2f} "
                    f"| {getattr(r, 'spikeet_close', '')} | {getattr(r, 'massive_close', '')} |")

    OUT_MD.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:20]))
    print(f"\nWrote {OUT_MD} and {OUT_EXCL}")
    sys.exit(0 if gate == "PASS" else 1)


if __name__ == "__main__":
    main()
