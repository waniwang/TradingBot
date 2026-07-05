"""Build the combined EP events dataset for Track A (gap-anchored EP 2.0).

Merges all four Spikeet EP Selection files, dedupes (symbol, date) — an
event can appear in both EARNINGS and NEWS; catalyst flags are OR-ed —
and adds context columns the sweeps need:

    is_earnings, is_news       catalyst flags
    prior_gaps_90d             prior gap events for the symbol in 90 days
    prior_gaps_365d            ... in 365 days
    dollar_vol_m               gap-day Close x Volume / 1e6

Prior-gap counts use the FULL event history (2020+) so early-window events
get correct flags even though pre-2021-07 rows have no price paths.

Output: market data download/ep_events_combined.csv  (readable by
sweeps.harness.load_data — CSV support added alongside this script).

Run: trading-bot/.venv/bin/python scripts/build_ep_events_combined.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
DATA_DIR = REPO_ROOT / "market data download"
OUT = DATA_DIR / "ep_events_combined.csv"

FILES = {
    "earnings": ["2020-2025 EP Selection EARNINGS.xlsx",
                 "2026 EP Selection EARNINGS.xlsx"],
    "news": ["2020-2025 EP Selection NEWS V2.xlsx",
             "2026 EP Selection NEWS V2.xlsx"],
}


def main() -> None:
    frames = []
    for catalyst, files in FILES.items():
        for f in files:
            df = pd.read_excel(DATA_DIR / f)
            df["Date"] = pd.to_datetime(df["Date"])
            df["catalyst"] = catalyst
            frames.append(df)
    ev = pd.concat(frames, ignore_index=True)
    ev["Symbol"] = ev["Symbol"].astype(str).str.strip().str.upper()

    # catalyst flags before dedupe (an event can be in both files)
    flags = (ev.groupby(["Symbol", "Date"])["catalyst"]
             .agg(lambda s: set(s)).reset_index())
    flags["is_earnings"] = flags["catalyst"].apply(lambda s: int("earnings" in s))
    flags["is_news"] = flags["catalyst"].apply(lambda s: int("news" in s))

    ev = ev.drop_duplicates(subset=["Symbol", "Date"]).drop(columns=["catalyst"])
    ev = ev.merge(flags[["Symbol", "Date", "is_earnings", "is_news"]],
                  on=["Symbol", "Date"], how="left")
    ev = ev.sort_values("Date").reset_index(drop=True)

    ev["dollar_vol_m"] = ev["Close"] * ev["Volume"] / 1e6

    p90, p365 = [], []
    hist: dict[str, list] = {}
    for r in ev.itertuples(index=False):
        lst = hist.setdefault(r.Symbol, [])
        p90.append(sum(1 for d in lst if (r.Date - d).days <= 90))
        p365.append(sum(1 for d in lst if (r.Date - d).days <= 365))
        lst.append(r.Date)
    ev["prior_gaps_90d"] = p90
    ev["prior_gaps_365d"] = p365

    ev.to_csv(OUT, index=False)
    n26 = (ev["Date"] >= "2026-01-01").sum()
    print(f"Wrote {OUT}: {len(ev)} unique events "
          f"({ev['is_earnings'].sum()} earnings-flagged, "
          f"{ev['is_news'].sum()} news-flagged, {n26} in 2026)")
    print(f"repeat-gap (90d) rate: {(ev['prior_gaps_90d'] >= 1).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
