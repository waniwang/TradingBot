"""
March 2026 end-to-end simulation:
  1. Bot scanner (broad universe + new filter set, no Spikeet data) per trading day
  2. Strategy A/B/C evaluation (earnings rules + news rules applied to all)
  3. Compare trades + scanner hits to Spikeet's March 2026 selection

Fields simulated per (symbol, date) using yfinance daily bars only:
  - Phase A: prev_close >= $3, gap >= 8%, open > prev_high
  - Phase B: open > 200d SMA
  - Strategy features: CHG-OPEN%, close_in_range, downside_from_open,
                       prev_10d_change_pct, atr_pct (10D)
  - Earnings Strategy A/B/C + News Strategy A/B/C rule checks
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "trading-bot"))
sys.path.insert(0, str(REPO / "scripts"))
from _phase_c import passes_phase_c  # noqa: E402

# Bypass package __init__ (conflicted plugin.py) by loading strategy.py directly
import importlib.util
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
_earn = _load("earn_strat", REPO / "trading-bot" / "strategies" / "ep_earnings" / "strategy.py")
_news = _load("news_strat", REPO / "trading-bot" / "strategies" / "ep_news" / "strategy.py")
compute_earn_features = _earn.compute_features
eval_earn_a, eval_earn_b, eval_earn_c = _earn.evaluate_strategy_a, _earn.evaluate_strategy_b, _earn.evaluate_strategy_c
compute_news_features = _news.compute_features
eval_news_a, eval_news_b, eval_news_c = _news.evaluate_strategy_a, _news.evaluate_strategy_b, _news.evaluate_strategy_c

UNIVERSE = REPO / "trading-bot" / "broad_universe.txt"
CONFIG = REPO / "trading-bot" / "config.yaml"
SPIKEET_EARN = REPO / "market data download" / "2026 EP Selection EARNINGS.xlsx"
SPIKEET_NEWS = REPO / "market data download" / "2026 EP Selection NEWS V2.xlsx"

MIN_GAP_PCT = 8.0
MIN_PRICE = 3.0


def load_universe():
    with open(UNIVERSE) as f:
        return [line.strip() for line in f if line.strip()]


def load_config():
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def load_spikeet_march():
    out = {"earn": set(), "news": set()}  # set of (sym, date)
    for kind, path in [("earn", SPIKEET_EARN), ("news", SPIKEET_NEWS)]:
        df = pd.read_excel(path)
        df["Date"] = pd.to_datetime(df["Date"])
        sub = df[(df["Date"] >= "2026-03-01") & (df["Date"] <= "2026-03-31")]
        for _, r in sub.iterrows():
            out[kind].add((r["Symbol"], r["Date"].date()))
    return out


def yf_batch(tickers, start, end, batch_size=500):
    out = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        print(f"  batch {i // batch_size + 1}/{(len(tickers) - 1) // batch_size + 1}...")
        df = yf.download(batch, start=start, end=end, group_by="ticker",
                          progress=False, threads=True, auto_adjust=False)
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


def build_candidate(sym, target_date, hist):
    """Run Phase A+B filters. Return candidate dict with all fields strategies need, or None."""
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
        close_p = float(today["Close"])
        high_p = float(today["High"])
        low_p = float(today["Low"])
        vol = int(today["Volume"])
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
    closes_200 = prior["Close"].tail(200).tolist()
    if len(closes_200) < 200:
        return None
    if open_p <= float(np.mean(closes_200)):
        return None
    return {
        "ticker": sym, "gap_pct": round(gap, 2), "open_price": open_p,
        "prev_close": prev_c, "current_price": close_p,
        "today_high": high_p, "today_low": low_p, "today_volume": vol,
    }


def get_daily_lists(hist, target_date):
    """Return (closes, highs, lows) prior-to-target, oldest first."""
    ts = pd.Timestamp(target_date)
    prior = hist.loc[:ts]  # include target day — strategies expect today's close as last element
    return (prior["Close"].tolist(), prior["High"].tolist(), prior["Low"].tolist())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-phase-c", action="store_true",
                    help="Also apply live Phase C (mcap, quoteType, earnings). Slower.")
    args = ap.parse_args()

    cfg = load_config()
    spikeet = load_spikeet_march()
    print(f"Spikeet March: {len(spikeet['earn'])} earnings + {len(spikeet['news'])} news picks\n")

    universe = load_universe()
    print(f"Universe: {len(universe)} tickers")
    print("Downloading daily bars 2025-03-01 -> 2026-04-01...")
    data = yf_batch(universe, "2025-03-01", "2026-04-02")
    print(f"Got data for {len(data)} tickers\n")

    # Find March trading days
    any_t = next(iter(data))
    march_days = sorted({
        d.date() for d in data[any_t].index
        if d.date().year == 2026 and d.date().month == 3
    })
    print(f"March 2026 trading days: {len(march_days)}\n")

    all_scanner_hits = []   # [(date, sym, gap)]
    all_trades = []         # [(date, sym, strategy_tag)]

    for day in march_days:
        day_candidates = []
        for sym, hist in data.items():
            c = build_candidate(sym, day, hist)
            if c:
                day_candidates.append((c, hist))
                all_scanner_hits.append((day, sym, c["gap_pct"]))

        day_trades = []
        for cand, hist in day_candidates:
            closes, highs, lows = get_daily_lists(hist, day)
            # Earnings strategies
            ef = compute_earn_features(cand, closes, highs, lows)
            if eval_earn_a(cand, ef, cfg): day_trades.append((cand["ticker"], "earn_A", hist, cand["current_price"]))
            if eval_earn_b(cand, ef, cfg): day_trades.append((cand["ticker"], "earn_B", hist, cand["current_price"]))
            if eval_earn_c(cand, ef, cfg): day_trades.append((cand["ticker"], "earn_C", hist, cand["current_price"]))
            # News strategies
            nf = compute_news_features(cand, closes, highs, lows)
            if eval_news_a(cand, nf, cfg): day_trades.append((cand["ticker"], "news_A", hist, cand["current_price"]))
            if eval_news_b(cand, nf, cfg): day_trades.append((cand["ticker"], "news_B", hist, cand["current_price"]))
            if eval_news_c(cand, nf, cfg): day_trades.append((cand["ticker"], "news_C", hist, cand["current_price"]))

        # Optional Phase C gate (mcap / EQUITY / earnings-today-or-not)
        if args.with_phase_c and day_trades:
            gated = []
            dropped = 0
            for sym, tag, hist, p in day_trades:
                kind = "earn" if tag.startswith("earn_") else "news"
                ok, _ = passes_phase_c(sym, day, kind=kind)
                if ok: gated.append((sym, tag, hist, p))
                else:  dropped += 1
            if dropped:
                print(f"    [phase-c] dropped {dropped} signal(s)")
            day_trades = gated

        # Day-2 confirmation for C strategies (A/B enter on Day 1, no confirm)
        confirmed = []
        for sym, tag, hist, day1_close in day_trades:
            if tag.endswith("_C"):
                # Find next trading day's close
                ts = pd.Timestamp(day)
                future = hist.loc[ts:].iloc[1:2]  # next bar after signal day
                if future.empty:
                    confirmed.append((sym, tag, "skipped_no_day2"))
                    continue
                day2_close = float(future.iloc[0]["Close"])
                ret = (day2_close - day1_close) / day1_close * 100
                if ret > 0:
                    confirmed.append((sym, tag, f"confirmed_+{ret:.2f}%"))
                else:
                    confirmed.append((sym, tag, f"rejected_{ret:.2f}%"))
            else:
                confirmed.append((sym, tag, "day1_entry"))

        passes = [c for c in confirmed if c[2].startswith("confirmed") or c[2] == "day1_entry"]
        print(f"{day}: {len(day_candidates):>3} scanner | {len(day_trades):>2} qualify | "
              f"{len(passes):>2} after day-2 check")
        for sym, tag, status in confirmed:
            print(f"    {sym:<6} {tag:<7} {status}")
            if status.startswith("confirmed") or status == "day1_entry":
                all_trades.append((day, sym, tag))

    # ---- Summary ----
    print(f"\n{'=' * 70}\nMARCH 2026 SUMMARY")
    print(f"  Scanner hits: {len(all_scanner_hits)}")
    print(f"  Trade signals (passing any strategy): {len(all_trades)}")
    print(f"  Unique trade tickers: {len({t[1] for t in all_trades})}")

    # Strategy breakdown
    from collections import Counter
    by_strat = Counter(t[2] for t in all_trades)
    print("\n  By strategy:")
    for s, n in sorted(by_strat.items()):
        print(f"    {s}: {n}")

    # ---- Spikeet comparison ----
    bot_scanner_set = {(s, d) for d, s, _ in all_scanner_hits}
    spikeet_all = spikeet["earn"] | spikeet["news"]
    overlap = bot_scanner_set & spikeet_all
    spikeet_only = spikeet_all - bot_scanner_set
    bot_only = bot_scanner_set - spikeet_all

    print(f"\n  Spikeet March picks: {len(spikeet_all)} unique (sym, date)")
    print(f"  Bot scanner hits:    {len(bot_scanner_set)} unique (sym, date)")
    print(f"  Overlap:             {len(overlap)}")
    print(f"  Spikeet-only (bot missed): {len(spikeet_only)}")
    if spikeet_only:
        for s, d in sorted(spikeet_only, key=lambda x: (x[1], x[0])):
            print(f"    {d} {s}")
    print(f"  Bot-only (not in Spikeet): {len(bot_only)}")

    # Trade comparison: which Spikeet picks did our strategies actually trade?
    bot_trade_set = {(s, d) for d, s, _ in all_trades}
    trade_overlap = bot_trade_set & spikeet_all
    print(f"\n  Bot trade signals that are also Spikeet picks: {len(trade_overlap)}/{len(bot_trade_set)}")

    # Dump CSVs
    pd.DataFrame(all_scanner_hits, columns=["date", "ticker", "gap_pct"]).to_csv(
        REPO / "bot_march_scanner_hits.csv", index=False)
    pd.DataFrame(all_trades, columns=["date", "ticker", "strategy"]).to_csv(
        REPO / "bot_march_trades.csv", index=False)
    print(f"\nWrote: bot_march_scanner_hits.csv, bot_march_trades.csv")


if __name__ == "__main__":
    main()
