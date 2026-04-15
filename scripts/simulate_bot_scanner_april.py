"""
Simulate the bot's NEW scanner (post-fix) against every trading day in April 2026
using only the broad universe + yfinance daily bars. No Spikeet data involved.

Filters applied (match production scanner):
  1. prev_close >= $3
  2. gap% >= 8%
  3. open > prev_high
  4. open > 200d SMA
  (RVOL and prior-6mo-gain filters are removed)

This is the Phase A+B portion. Phase C (market cap, earnings-yes/no) is skipped
since it requires per-ticker yfinance .info calls and its sole function is to
split candidates between EP Earnings vs EP News — the universe itself is the
same pre-split.
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parent.parent
UNIVERSE = REPO / "trading-bot" / "broad_universe.txt"

MIN_GAP_PCT = 8.0
MIN_PRICE = 3.0


def load_universe() -> list[str]:
    with open(UNIVERSE) as f:
        return [line.strip() for line in f if line.strip()]


def yf_batch(tickers, start, end, batch_size=500):
    """Download daily bars for all tickers, concatenating batches."""
    out = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        print(f"  Downloading batch {i // batch_size + 1}/{(len(tickers) - 1) // batch_size + 1} "
              f"({len(batch)} tickers)...")
        df = yf.download(
            batch, start=start, end=end, group_by="ticker",
            progress=False, threads=True, auto_adjust=False,
        )
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            for sym in batch:
                if sym in df.columns.levels[0]:
                    sub = df[sym].dropna(how="all")
                    if not sub.empty:
                        out[sym] = sub.sort_index()
        else:
            sub = df.dropna(how="all")
            if not sub.empty:
                out[batch[0]] = sub.sort_index()
    return out


def check_ticker(sym: str, target_date, hist: pd.DataFrame):
    """Return candidate dict if passes all filters, else None."""
    ts = pd.Timestamp(target_date)
    if ts not in hist.index:
        return None
    today = hist.loc[ts]
    prior = hist.loc[:ts].iloc[:-1]
    if prior.empty:
        return None
    prev = prior.iloc[-1]
    try:
        open_p = float(today["Open"])
        prev_c = float(prev["Close"])
        prev_h = float(prev["High"])
    except (KeyError, ValueError, TypeError):
        return None
    if prev_c <= 0 or open_p <= 0:
        return None
    if prev_c < MIN_PRICE:
        return None
    gap = (open_p - prev_c) / prev_c * 100
    if gap < MIN_GAP_PCT:
        return None
    if open_p <= prev_h:
        return None
    closes = prior["Close"].tail(200).tolist()
    if len(closes) < 200:
        return None
    sma = float(np.mean(closes))
    if open_p <= sma:
        return None
    return {"symbol": sym, "gap_pct": round(gap, 2), "open": round(open_p, 2),
            "prev_close": round(prev_c, 2)}


def main():
    universe = load_universe()
    print(f"Universe: {len(universe)} tickers\n")

    start = "2025-03-01"  # enough history for 200d SMA by Apr 2026 (~275 trading days)
    end = "2026-04-15"
    print(f"Downloading daily bars {start} -> {end}...")
    data = yf_batch(universe, start, end)
    print(f"Got data for {len(data)} / {len(universe)} tickers\n")

    # Build list of April 2026 trading days (from any ticker's index)
    any_ticker = next(iter(data))
    all_dates = data[any_ticker].index
    april_days = sorted({
        d.date() for d in all_dates
        if d.date() >= datetime(2026, 4, 1).date() and d.date() <= datetime(2026, 4, 14).date()
    })
    print(f"April 2026 trading days: {april_days}\n")

    grand_total = 0
    for day in april_days:
        hits = []
        for sym, hist in data.items():
            c = check_ticker(sym, day, hist)
            if c:
                hits.append(c)
        hits.sort(key=lambda x: -x["gap_pct"])
        print(f"\n=== {day}  ({len(hits)} candidates) ===")
        if hits:
            print(f"{'Ticker':<8} {'Gap%':<8} {'Open':<9} {'PrevCl':<9}")
            for h in hits:
                print(f"{h['symbol']:<8} {h['gap_pct']:<8} ${h['open']:<8} ${h['prev_close']:<8}")
        grand_total += len(hits)

    print(f"\n{'=' * 60}\nGRAND TOTAL: {grand_total} candidate-days across {len(april_days)} trading days")


if __name__ == "__main__":
    main()
