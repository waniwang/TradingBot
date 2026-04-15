"""
Broad-universe gap pre-screen (broker-agnostic).

Replaces the prior "top 50 gainers from broker" approach. Uses yfinance daily
bars (same source as Phase B enrichment) so it works identically for Alpaca
and IB bots, and is not capped by either broker's scanner API.

At ~3pm ET, yfinance returns the current trading day's partial daily bar
with an accurate Open value (set at 9:30am) — sufficient for the gap=open/
prev_close calculation.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE_FILE = Path(__file__).resolve().parent.parent / "broad_universe.txt"


def load_universe(path: Path | None = None) -> list[str]:
    p = path or DEFAULT_UNIVERSE_FILE
    with open(p) as f:
        return [line.strip() for line in f if line.strip()]


def scan_broad_gaps(
    min_gap_pct: float = 8.0,
    min_price: float = 3.0,
    universe: list[str] | None = None,
    batch_size: int = 500,
) -> list[dict]:
    """
    Return candidates from the broad universe whose today's Open gaps up
    >= min_gap_pct above prev close (and prev close >= min_price).

    Returns a list shaped like broker movers: [{symbol, percent_change, price}, ...]
    sorted by gap% descending, uncapped.
    """
    tickers = universe if universe is not None else load_universe()
    if not tickers:
        return []

    results: list[dict] = []
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            df = yf.download(
                batch,
                period="5d",
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=True,
                auto_adjust=False,
            )
        except Exception as e:
            logger.warning("gap_screen batch %d failed: %s", i, e)
            continue

        if df is None or df.empty:
            continue

        multi = isinstance(df.columns, pd.MultiIndex)
        for sym in batch:
            try:
                sub = df[sym].dropna(how="all") if multi else df.dropna(how="all")
                if sub is None or sub.empty or len(sub) < 2:
                    continue
                sub = sub.sort_index()
                today = sub.iloc[-1]
                prev = sub.iloc[-2]
                open_p = float(today["Open"])
                prev_c = float(prev["Close"])
                if prev_c <= 0 or open_p <= 0:
                    continue
                if prev_c < min_price:
                    continue
                gap = (open_p - prev_c) / prev_c * 100
                if gap < min_gap_pct:
                    continue
                results.append({
                    "symbol": sym,
                    "percent_change": round(gap, 2),
                    "price": round(open_p, 2),
                })
            except (KeyError, ValueError, TypeError):
                continue

    results.sort(key=lambda x: -x["percent_change"])
    logger.info(
        "gap_screen: %d candidates from %d-ticker universe (gap>=%.1f%%, price>=$%.1f)",
        len(results), len(tickers), min_gap_pct, min_price,
    )
    return results
