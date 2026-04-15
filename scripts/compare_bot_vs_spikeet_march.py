"""
March 2026 head-to-head:
  PATH A: bot scanner candidates  -> strategies A/B/C -> day-2 confirm -> trades
  PATH B: Spikeet's picks          -> strategies A/B/C -> day-2 confirm -> trades

Same strategy + day-2 logic in both paths; only the candidate universe differs.
"""
from __future__ import annotations

import argparse
import sys
import importlib.util
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


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_earn = _load("earn_strat", REPO / "trading-bot" / "strategies" / "ep_earnings" / "strategy.py")
_news = _load("news_strat", REPO / "trading-bot" / "strategies" / "ep_news" / "strategy.py")

UNIVERSE = REPO / "trading-bot" / "broad_universe.txt"
CONFIG = REPO / "trading-bot" / "config.yaml"
EARN_XLSX = REPO / "market data download" / "2026 EP Selection EARNINGS.xlsx"
NEWS_XLSX = REPO / "market data download" / "2026 EP Selection NEWS V2.xlsx"

MIN_GAP_PCT = 8.0
MIN_PRICE = 3.0


def load_universe():
    with open(UNIVERSE) as f:
        return [l.strip() for l in f if l.strip()]


def load_config():
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def load_spikeet_march():
    picks = {}  # (sym, date) -> set of kinds
    for kind, path in [("earn", EARN_XLSX), ("news", NEWS_XLSX)]:
        df = pd.read_excel(path)
        df["Date"] = pd.to_datetime(df["Date"])
        sub = df[(df["Date"] >= "2026-03-01") & (df["Date"] <= "2026-03-31")]
        for _, r in sub.iterrows():
            picks.setdefault((r["Symbol"], r["Date"].date()), set()).add(kind)
    return picks


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


def build_candidate(sym, target_date, hist, apply_bot_filters=True):
    """Build candidate dict. If apply_bot_filters, enforce Phase A+B filters."""
    ts = pd.Timestamp(target_date)
    if ts not in hist.index:
        return None
    today = hist.loc[ts]
    prior = hist.loc[:ts].iloc[:-1]
    if prior.empty:
        return None
    prev = prior.iloc[-1]
    try:
        open_p = float(today["Open"]); close_p = float(today["Close"])
        high_p = float(today["High"]); low_p = float(today["Low"])
        vol = int(today["Volume"])
        prev_c = float(prev["Close"]); prev_h = float(prev["High"])
    except (KeyError, ValueError, TypeError):
        return None
    if prev_c <= 0 or open_p <= 0:
        return None
    gap = (open_p - prev_c) / prev_c * 100
    if apply_bot_filters:
        if prev_c < MIN_PRICE: return None
        if gap < MIN_GAP_PCT: return None
        if open_p <= prev_h: return None
        closes_200 = prior["Close"].tail(200).tolist()
        if len(closes_200) < 200 or open_p <= float(np.mean(closes_200)):
            return None
    return {
        "ticker": sym, "gap_pct": round(gap, 2), "open_price": open_p,
        "prev_close": prev_c, "current_price": close_p,
        "today_high": high_p, "today_low": low_p, "today_volume": vol,
    }


def evaluate_trades(candidates_by_date, data, cfg, label, with_phase_c=False):
    """Apply all strategies + day-2 confirm, return list of (date, sym, strat, day2_ret).

    If with_phase_c=True, each qualifying signal is additionally checked against
    Phase C (market cap, security class, earnings-today/no-earnings) before being
    kept. earn_* signals use kind='earn', news_* signals use kind='news'.
    """
    trades = []
    qualifiers_count = 0
    phase_c_drops = 0
    for day, cands in sorted(candidates_by_date.items()):
        for cand in cands:
            hist = data[cand["ticker"]]
            ts = pd.Timestamp(day)
            prior = hist.loc[:ts]
            closes = prior["Close"].tolist()
            highs = prior["High"].tolist()
            lows = prior["Low"].tolist()

            signals = []
            ef = _earn.compute_features(cand, closes, highs, lows)
            if _earn.evaluate_strategy_a(cand, ef, cfg): signals.append("earn_A")
            if _earn.evaluate_strategy_b(cand, ef, cfg): signals.append("earn_B")
            if _earn.evaluate_strategy_c(cand, ef, cfg): signals.append("earn_C")
            nf = _news.compute_features(cand, closes, highs, lows)
            if _news.evaluate_strategy_a(cand, nf, cfg): signals.append("news_A")
            if _news.evaluate_strategy_b(cand, nf, cfg): signals.append("news_B")
            if _news.evaluate_strategy_c(cand, nf, cfg): signals.append("news_C")
            qualifiers_count += len(signals)

            if with_phase_c and signals:
                kept = []
                for tag in signals:
                    kind = "earn" if tag.startswith("earn_") else "news"
                    ok, _reason = passes_phase_c(cand["ticker"], day, kind=kind)
                    if ok:
                        kept.append(tag)
                    else:
                        phase_c_drops += 1
                signals = kept

            for tag in signals:
                if tag.endswith("_C"):
                    future = hist.loc[ts:].iloc[1:2]
                    if future.empty:
                        continue
                    day2_close = float(future.iloc[0]["Close"])
                    ret = (day2_close - cand["current_price"]) / cand["current_price"] * 100
                    if ret > 0:
                        trades.append((day, cand["ticker"], tag, round(ret, 2)))
                else:
                    # A/B enter on day 1 — use day-2 close for fair ROI comparison
                    future = hist.loc[ts:].iloc[1:2]
                    ret = None
                    if not future.empty:
                        d2 = float(future.iloc[0]["Close"])
                        ret = round((d2 - cand["current_price"]) / cand["current_price"] * 100, 2)
                    trades.append((day, cand["ticker"], tag, ret))
    extra = f"  phase_c_drops={phase_c_drops}" if with_phase_c else ""
    print(f"[{label}] qualifiers={qualifiers_count}  trades(after day-2)={len(trades)}{extra}")
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-phase-c", action="store_true",
                    help="Also apply live Phase C (mcap, quoteType, earnings). "
                         "Slower: requires per-ticker yfinance info calls.")
    args = ap.parse_args()

    cfg = load_config()
    universe = load_universe()
    spikeet = load_spikeet_march()

    # Union of all symbols we need data for
    all_syms = set(universe) | {sym for sym, _ in spikeet.keys()}
    print(f"Downloading {len(all_syms)} symbols 2025-03-01 -> 2026-04-02...")
    data = yf_batch(sorted(all_syms), "2025-03-01", "2026-04-02")
    print(f"Got {len(data)} tickers\n")

    # Enumerate March trading days
    any_t = next(iter(data))
    march_days = sorted({d.date() for d in data[any_t].index
                          if d.date().year == 2026 and d.date().month == 3})

    # -------- PATH A: bot scanner ----------
    print("=" * 70 + "\nPATH A: Bot scanner -> strategies -> day-2\n" + "=" * 70)
    bot_cands = {}
    for day in march_days:
        hits = []
        for sym, hist in data.items():
            c = build_candidate(sym, day, hist, apply_bot_filters=True)
            if c: hits.append(c)
        if hits: bot_cands[day] = hits
    bot_trades = evaluate_trades(bot_cands, data, cfg, "BOT", with_phase_c=args.with_phase_c)

    # -------- PATH B: Spikeet picks ---------
    print("\n" + "=" * 70 + "\nPATH B: Spikeet picks -> strategies -> day-2\n" + "=" * 70)
    spi_cands = {}
    for (sym, d), kinds in spikeet.items():
        if sym not in data:
            continue
        c = build_candidate(sym, d, data[sym], apply_bot_filters=False)
        if c: spi_cands.setdefault(d, []).append(c)
    spi_trades = evaluate_trades(spi_cands, data, cfg, "SPIKEET", with_phase_c=args.with_phase_c)

    # -------- Compare ---------
    print("\n" + "=" * 70 + "\nCOMPARISON\n" + "=" * 70)

    def fmt(trades):
        return pd.DataFrame(trades, columns=["date", "ticker", "strategy", "day2_ret_%"])

    df_bot = fmt(bot_trades)
    df_spi = fmt(spi_trades)

    bot_set = {(t[0], t[1], t[2]) for t in bot_trades}
    spi_set = {(t[0], t[1], t[2]) for t in spi_trades}

    print(f"\nBot-path trades:     {len(bot_set)}")
    print(f"Spikeet-path trades: {len(spi_set)}")
    print(f"Shared (same day+ticker+strategy): {len(bot_set & spi_set)}")
    print(f"Bot-only:     {len(bot_set - spi_set)}")
    print(f"Spikeet-only: {len(spi_set - bot_set)}")

    print("\n--- BOT-PATH TRADES ---")
    print(df_bot.to_string(index=False) if not df_bot.empty else "(none)")
    print("\n--- SPIKEET-PATH TRADES ---")
    print(df_spi.to_string(index=False) if not df_spi.empty else "(none)")

    print("\n--- BOT-ONLY (not in Spikeet path) ---")
    for t in sorted(bot_set - spi_set):
        print(f"  {t[0]}  {t[1]:<6}  {t[2]}")
    print("\n--- SPIKEET-ONLY (bot path would have missed) ---")
    for t in sorted(spi_set - bot_set):
        print(f"  {t[0]}  {t[1]:<6}  {t[2]}")

    # Aggregate day-2 returns
    bot_rets = [r for _, _, _, r in bot_trades if r is not None]
    spi_rets = [r for _, _, _, r in spi_trades if r is not None]
    print(f"\nBot-path mean day-2 return:     {np.mean(bot_rets):+.2f}% across {len(bot_rets)} trades")
    print(f"Spikeet-path mean day-2 return: {np.mean(spi_rets):+.2f}% across {len(spi_rets)} trades")


if __name__ == "__main__":
    main()
