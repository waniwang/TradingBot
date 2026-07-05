"""Per-worker loader for Massive daily-path CSVs.

Builds {(symbol, entry_date_iso): (entry_close, dates, ohlc)} once per
process (lru_cache) from the per-symbol CSVs written by
scripts/fetch_massive_daily_paths.py, sliced per gap event.

entry_close is the Massive ADJUSTED close on the event date — the
simulation entry price. dates is (n,) datetime64[D], ohlc is (n,4) float64
covering up to MAX_FORWARD_BARS trading days after the event date.

Memory: ~6k events x 80 bars x 4 x 8B ~ 15 MB per worker — trivial.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

# 50 calendar days ~ 35 trading days; keep headroom for sweeps up to
# max_hold_days=70 calendar.
MAX_FORWARD_BARS = 55


def _event_index(events_csv: str | None) -> pd.DataFrame | None:
    if events_csv is None:
        return None
    return pd.read_csv(events_csv, parse_dates=["Date"])


@lru_cache(maxsize=2)
def load_paths(cache_dir: str) -> dict[tuple[str, str], tuple[float, np.ndarray, np.ndarray]]:
    """Load every symbol CSV under cache_dir and pre-slice nothing: the dict
    is keyed lazily per (symbol, date) via _SymbolPaths view objects would
    add complexity — instead we return a dict-like wrapper.

    Implementation note: we cannot know every event date here without the
    event lists, so we expose a PathLookup that slices on .get()."""
    return PathLookup(cache_dir)  # type: ignore[return-value]


class PathLookup:
    """Dict-like: .get((symbol, 'YYYY-MM-DD')) -> (entry_close, dates, ohlc) | None.

    Slices the symbol's daily series at the event date: entry_close is that
    date's close; the path is the next MAX_FORWARD_BARS trading days.
    Symbol frames are loaded lazily and memoized, so a ProcessPool worker
    only ever reads the symbols its combos touch."""

    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self._frames: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}
        # Events failing the Phase 2 validation gate (ticker reuse, OTC,
        # bad prints, missing data) are hard-excluded from simulation.
        self._excluded: set[tuple[str, str]] = set()
        excl = self.cache_dir / "_exclusions.csv"
        if excl.exists():
            e = pd.read_csv(excl)
            self._excluded = set(zip(e["symbol"], e["date"]))

    def _load_symbol(self, symbol: str) -> tuple[np.ndarray, np.ndarray] | None:
        if symbol in self._frames:
            return self._frames[symbol]
        f = self.cache_dir / f"{symbol}.csv"
        if not f.exists():
            self._frames[symbol] = None
            return None
        df = pd.read_csv(f)
        dates = df["date"].to_numpy(dtype="datetime64[D]")
        ohlc = df[["open", "high", "low", "close"]].to_numpy(dtype=np.float64)
        self._frames[symbol] = (dates, ohlc)
        return self._frames[symbol]

    def get(self, key: tuple[str, str]):
        symbol, date_iso = key
        if (symbol, date_iso) in self._excluded:
            return None
        loaded = self._load_symbol(symbol)
        if loaded is None:
            return None
        dates, ohlc = loaded
        d0 = np.datetime64(date_iso)
        idx = np.searchsorted(dates, d0)
        if idx >= len(dates) or dates[idx] != d0:
            return None  # no bar on the event date (halt / data gap)
        entry_close = float(ohlc[idx, 3])
        lo, hi = idx + 1, min(idx + 1 + MAX_FORWARD_BARS, len(dates))
        if lo >= len(dates):
            return None  # event on the last cached bar — no forward path
        return entry_close, dates[lo:hi], ohlc[lo:hi]
