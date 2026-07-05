"""EP 2.0 Track B scanner: standalone momentum-leader base breakouts.

Scans the full-market grouped-daily cache (massive_grouped/) for
Qullamaggie-style breakouts WITHOUT a gap prerequisite:

  1. Liquidity: 20d avg dollar-vol >= $50M, close >= $10, plain symbols
  2. Momentum leader: 21d return >= mom21_min OR 63d return >= mom63_min
  3. Base: prior `base_days` bars range-bound (high-low range <= tight% of
     close) AND close within 15% of the 63d high (near highs, not broken)
  4. Trigger: today's close > max high of the base window (breakout close),
     with volume >= vol_x times the 20d average

Emits one row per event (deduped per symbol: no second event within 15
bars) with features + the forward daily path columns the simulator needs.

Output:
  market data download/breakout_events.csv       events + features
  market data download/breakout_paths/           per-symbol forward paths
      <SYMBOL>.csv  (date,open,high,low,close,volume — full series so the
      simulator can slice any event date; reuses sweeps/path_store.py)

Run: trading-bot/.venv/bin/python scripts/scan_market_breakouts.py
Runtime: ~2-4 min (12M rows -> ~2k liquid symbols -> events).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
DATA_DIR = REPO_ROOT / "market data download"
GROUPED = DATA_DIR / "massive_grouped"
OUT_EVENTS = DATA_DIR / "breakout_events.csv"
OUT_PATHS = DATA_DIR / "breakout_paths"

PLAIN_SYM = re.compile(r"^[A-Z]{1,5}$")

# Scanner thresholds (defaults; sweepable via re-run flags later)
DV_MIN_M = 50.0          # 20d avg dollar-vol floor ($M)
PRICE_MIN = 10.0
MOM21_MIN = 25.0         # % over 21 bars
MOM63_MIN = 50.0         # % over 63 bars
BASE_DAYS = 10
BASE_TIGHT_PCT = 12.0    # base (maxH-minL)/close ceiling
NEAR_HIGH_PCT = 15.0     # close within X% of 63d high
VOL_X = 1.5              # breakout-day volume vs 20d avg
DEDUP_BARS = 15


def load_panel() -> pd.DataFrame:
    files = sorted(GROUPED.glob("2*.csv"))
    frames = []
    for f in files:
        df = pd.read_csv(f)
        df = df[df["symbol"].astype(str).str.match(PLAIN_SYM, na=False)]
        df["date"] = f.stem
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    return panel


def main() -> None:
    print("loading panel…")
    panel = load_panel()
    print(f"  {len(panel):,} rows, {panel['symbol'].nunique():,} symbols, "
          f"{panel['date'].nunique()} days")

    # restrict to symbols that are ever liquid enough (cheap pre-cut)
    panel["dv_m"] = panel["close"] * panel["volume"] / 1e6
    liquid_days = panel[panel["dv_m"] >= DV_MIN_M].groupby("symbol").size()
    keep = set(liquid_days[liquid_days >= 60].index)
    panel = panel[panel["symbol"].isin(keep)].sort_values(["symbol", "date"])
    print(f"  liquid universe: {len(keep):,} symbols, {len(panel):,} rows")

    OUT_PATHS.mkdir(parents=True, exist_ok=True)
    events = []
    for sym, g in panel.groupby("symbol", sort=False):
        g = g.reset_index(drop=True)
        n = len(g)
        if n < 80:
            continue
        c = g["close"].to_numpy()
        h = g["high"].to_numpy()
        l = g["low"].to_numpy()
        v = g["volume"].to_numpy()
        dv = g["dv_m"].to_numpy()

        dv20 = pd.Series(dv).rolling(20).mean().to_numpy()
        v20 = pd.Series(v).rolling(20).mean().to_numpy()
        mom21 = np.full(n, np.nan)
        mom63 = np.full(n, np.nan)
        mom21[21:] = (c[21:] / c[:-21] - 1) * 100
        mom63[63:] = (c[63:] / c[:-63] - 1) * 100
        hi63 = pd.Series(h).rolling(63).max().to_numpy()

        last_evt = -10**9
        for i in range(63 + BASE_DAYS, n - 1):
            if i - last_evt < DEDUP_BARS:
                continue
            if not (dv20[i] >= DV_MIN_M and c[i] >= PRICE_MIN):
                continue
            if not (mom21[i] >= MOM21_MIN or mom63[i] >= MOM63_MIN):
                continue
            base_h = h[i - BASE_DAYS:i]
            base_l = l[i - BASE_DAYS:i]
            base_range = (base_h.max() - base_l.min()) / c[i] * 100
            if base_range > BASE_TIGHT_PCT:
                continue
            if c[i] < hi63[i] * (1 - NEAR_HIGH_PCT / 100):
                continue
            if not (c[i] > base_h.max()):
                continue
            if not (v20[i] > 0 and v[i] >= VOL_X * v20[i]):
                continue
            events.append({
                "Symbol": sym, "Date": g["date"].iloc[i],
                "Close": c[i], "High": h[i], "Low": l[i],
                "base_high": float(base_h.max()),
                "base_low": float(base_l.min()),
                "base_range_pct": round(base_range, 2),
                "mom21": round(float(mom21[i]), 1),
                "mom63": round(float(mom63[i]), 1) if not np.isnan(mom63[i]) else np.nan,
                "dv20_m": round(float(dv20[i]), 1),
                "vol_x": round(float(v[i] / v20[i]), 2),
                "dist_63d_high_pct": round(float((hi63[i] - c[i]) / hi63[i] * 100), 2),
            })
            last_evt = i

        # persist the symbol's full series once for path simulation
        out = g[["date", "open", "high", "low", "close", "volume"]]
        out.to_csv(OUT_PATHS / f"{sym}.csv", index=False)

    ev = pd.DataFrame(events)
    ev.to_csv(OUT_EVENTS, index=False)
    if len(ev):
        ev["Date"] = pd.to_datetime(ev["Date"])
        by_year = ev.groupby(ev["Date"].dt.year).size().to_dict()
        print(f"\n{len(ev)} breakout events across {ev['Symbol'].nunique()} symbols")
        print(f"by year: {by_year}")
    print(f"Wrote {OUT_EVENTS} and per-symbol paths to {OUT_PATHS}/")


if __name__ == "__main__":
    main()
