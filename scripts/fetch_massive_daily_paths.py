"""Fetch daily OHLCV paths from Massive.com for every EP gap-event symbol.

Purpose: the Spikeet EP Selection datasets only carry checkpoint forward
returns (2nd day / 10D / 20D / 50D), so path-dependent exits (profit targets,
early partials, trailing stops) cannot be simulated from them. This script
pulls the full daily bar path per symbol from Massive (Polygon-compatible
API) so sweeps/path_harness.py can replay every trade day by day.

Fetch strategy: ONE call per unique symbol covering
[earliest gap date - 30 calendar days, latest gap date + 120 calendar days].
2,018 symbols => a few minutes on the paid Starter tier.

Cache layout (resumable by construction):

    market data download/massive_daily/
        _manifest.csv     # symbol,from,to,n_bars,status(ok|empty|error),fetched_at,error
        AAPL.csv          # date,open,high,low,close,volume   (adjusted=true)

A symbol is skipped on re-run if the manifest has status=ok for a window
covering the requested one. Symbol CSVs are written atomically
(tmp file -> rename). Symbols with zero results are recorded status=empty
and NOT retried unless --refetch.

Usage (from repo root):

    trading-bot/.venv/bin/python scripts/fetch_massive_daily_paths.py \
        --cache-dir "market data download/massive_daily" --workers 8

    # refetch one symbol
    ... --refetch AAPL

Read-only against Massive; never touches the trading pipeline.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://api.massive.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "market data download"

DEFAULT_DATASETS = [
    DATA_DIR / "2020-2025 EP Selection EARNINGS.xlsx",
    DATA_DIR / "2020-2025 EP Selection NEWS V2.xlsx",
    DATA_DIR / "2026 EP Selection EARNINGS.xlsx",
    DATA_DIR / "2026 EP Selection NEWS V2.xlsx",
]
# Live-trade CSV symbols must have paths too (entry dates can differ from
# the Spikeet gap dates by a day on day-2 confirm variants).
LIVE_TRADES_CSV = REPO_ROOT / "2026_Full_EP_Strategies_BC_Trades.csv"

PRE_DAYS = 30    # calendar days of history before earliest event (SMA/ATR context)
POST_DAYS = 120  # calendar days after latest event (covers 50 trading days + buffer)

# Massive Starter plan = rolling 5-year lookback. Requests entirely before
# the boundary 403; overlapping ones are silently clipped. Clamp our windows
# so symbols whose FIRST event predates the boundary still fetch cleanly for
# their in-window events. Refreshed each run (boundary rolls daily).
PLAN_HISTORY_YEARS = 5
PLAN_BOUNDARY = (pd.Timestamp.now().normalize()
                 - pd.DateOffset(years=PLAN_HISTORY_YEARS)
                 + pd.Timedelta(days=2)).strftime("%Y-%m-%d")


def _env_file_key() -> str | None:
    """Read MASSIVE_API_KEY from moomoo-bot1's gitignored .env (where the
    Massive integration lives) or a local .env, without importing anything."""
    candidates = [
        REPO_ROOT / ".env",
        Path.home() / "Documents/moomoo-bot1/moomoo-api/.env",
    ]
    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("MASSIVE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def collect_symbol_windows(dataset_paths: list[Path]) -> pd.DataFrame:
    """One row per unique symbol: symbol, fetch_from, fetch_to (union of
    event windows across every dataset the symbol appears in)."""
    frames = []
    for p in dataset_paths:
        df = pd.read_excel(p, usecols=["Symbol", "Date"])
        df["Date"] = pd.to_datetime(df["Date"])
        frames.append(df)
    if LIVE_TRADES_CSV.exists():
        lt = pd.read_csv(LIVE_TRADES_CSV, usecols=["Symbol", "Entry Date"])
        lt = lt.rename(columns={"Entry Date": "Date"})
        lt["Date"] = pd.to_datetime(lt["Date"])
        frames.append(lt)

    allev = pd.concat(frames, ignore_index=True)
    allev["Symbol"] = allev["Symbol"].astype(str).str.strip().str.upper()
    allev = allev.dropna(subset=["Symbol", "Date"])

    g = allev.groupby("Symbol")["Date"].agg(["min", "max"]).reset_index()
    g["fetch_from"] = (
        (g["min"] - timedelta(days=PRE_DAYS))
        .clip(lower=pd.Timestamp(PLAN_BOUNDARY))
        .dt.strftime("%Y-%m-%d")
    )
    today = pd.Timestamp.now().normalize()
    g["fetch_to"] = (g["max"] + timedelta(days=POST_DAYS)).clip(upper=today).dt.strftime("%Y-%m-%d")
    # Drop symbols whose entire event window predates the plan boundary —
    # their fetch would 403 and their events are excluded from path sims.
    dropped = g[g["fetch_to"] <= g["fetch_from"]]
    if len(dropped):
        print(f"NOTE: {len(dropped)} symbols entirely before plan boundary "
              f"{PLAN_BOUNDARY} — skipped (events excluded from path sims)")
    g = g[g["fetch_to"] > g["fetch_from"]]
    return g.rename(columns={"Symbol": "symbol"})[["symbol", "fetch_from", "fetch_to"]]


def fetch_symbol_daily(
    session: requests.Session,
    api_key: str,
    symbol: str,
    start: str,
    end: str,
    adjusted: bool = True,
    max_retries: int = 3,
) -> pd.DataFrame:
    """GET /v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}?adjusted=true.
    Returns df: date, open, high, low, close, volume (date ascending).
    Empty df when Massive has no bars (delisted before window / unknown)."""
    url = f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
    params = {
        "adjusted": "true" if adjusted else "false",
        "sort": "asc",
        "limit": 50000,
        "apiKey": api_key,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    results: list[dict] = []
    next_url = None
    attempt = 0
    while True:
        try:
            if next_url:
                resp = session.get(next_url, params={"apiKey": api_key},
                                   headers=headers, timeout=30)
            else:
                resp = session.get(url, params=params, headers=headers, timeout=30)
        except requests.RequestException as exc:
            attempt += 1
            if attempt > max_retries:
                raise RuntimeError(f"network error after {max_retries} retries: {exc}")
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 429:
            attempt += 1
            if attempt > max_retries:
                raise RuntimeError("rate limited (429) after retries")
            time.sleep(5 * attempt)
            continue
        if resp.status_code in (401, 403):
            raise RuntimeError(f"auth failed ({resp.status_code}): check MASSIVE_API_KEY")
        if resp.status_code == 404:
            return pd.DataFrame()
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        payload = resp.json()
        results.extend(payload.get("results") or [])
        next_url = payload.get("next_url")
        attempt = 0
        if not next_url:
            break

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    # Polygon keys: o,h,l,c,v,t (UTC epoch ms, midnight-stamped daily bars)
    df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(
        "America/New_York").dt.strftime("%Y-%m-%d")
    out = df.rename(columns={"o": "open", "h": "high", "l": "low",
                             "c": "close", "v": "volume"})
    return out[["date", "open", "high", "low", "close", "volume"]].sort_values(
        "date").reset_index(drop=True)


def load_manifest(cache_dir: Path) -> dict[str, dict]:
    mpath = cache_dir / "_manifest.csv"
    if not mpath.exists():
        return {}
    rows = {}
    with open(mpath, newline="") as f:
        for row in csv.DictReader(f):
            rows[row["symbol"]] = row  # last write wins
    return rows


def append_manifest(cache_dir: Path, lock: threading.Lock, row: dict) -> None:
    mpath = cache_dir / "_manifest.csv"
    fieldnames = ["symbol", "from", "to", "n_bars", "status", "fetched_at", "error"]
    with lock:
        new_file = not mpath.exists()
        with open(mpath, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if new_file:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in fieldnames})


def write_atomic(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    tmp.rename(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Massive daily paths for EP symbols.")
    ap.add_argument("--cache-dir", type=Path,
                    default=DATA_DIR / "massive_daily")
    ap.add_argument("--datasets", nargs="*", type=Path, default=DEFAULT_DATASETS)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--refetch", help="Force refetch of one comma-separated symbol list.")
    ap.add_argument("--api-key", default=os.environ.get("MASSIVE_API_KEY") or _env_file_key())
    args = ap.parse_args()

    if not args.api_key:
        raise SystemExit("MASSIVE_API_KEY not found in env, repo .env, or moomoo-bot1 .env")

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    windows = collect_symbol_windows([p for p in args.datasets if p.exists()])
    manifest = load_manifest(args.cache_dir)
    refetch = set((args.refetch or "").upper().split(",")) - {""}

    todo = []
    for row in windows.itertuples(index=False):
        prev = manifest.get(row.symbol)
        if row.symbol in refetch:
            todo.append(row)
            continue
        if prev and prev["status"] in ("ok", "empty"):
            # Covered already? Only skip if the cached window contains this one.
            if prev["from"] <= row.fetch_from and prev["to"] >= row.fetch_to:
                continue
        todo.append(row)

    print(f"{len(windows)} unique symbols; {len(todo)} to fetch "
          f"({len(windows) - len(todo)} cached)")

    lock = threading.Lock()
    local = threading.local()

    def get_session() -> requests.Session:
        if not hasattr(local, "session"):
            local.session = requests.Session()
        return local.session

    counts = {"ok": 0, "empty": 0, "error": 0}
    errors: list[tuple[str, str]] = []

    def work(row) -> tuple[str, str]:
        sym = row.symbol
        try:
            df = fetch_symbol_daily(get_session(), args.api_key, sym,
                                    row.fetch_from, row.fetch_to)
            status = "ok" if len(df) else "empty"
            if len(df):
                write_atomic(args.cache_dir / f"{sym}.csv", df)
            append_manifest(args.cache_dir, lock, {
                "symbol": sym, "from": row.fetch_from, "to": row.fetch_to,
                "n_bars": len(df), "status": status,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            })
            return sym, status
        except Exception as exc:
            append_manifest(args.cache_dir, lock, {
                "symbol": sym, "from": row.fetch_from, "to": row.fetch_to,
                "n_bars": 0, "status": "error",
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "error": str(exc)[:200],
            })
            return sym, f"error: {exc}"

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(work, row): row.symbol for row in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            sym, status = fut.result()
            key = status.split(":")[0] if status.startswith("error") else status
            counts[key] = counts.get(key, 0) + 1
            if status.startswith("error"):
                errors.append((sym, status))
                print(f"[{i}/{len(todo)}] {sym}: {status}", file=sys.stderr)
            elif i % 100 == 0 or i == len(todo):
                print(f"[{i}/{len(todo)}] ... last={sym} {status} "
                      f"({time.time()-started:.0f}s)")

    print(f"\nDone in {time.time()-started:.0f}s: {counts}")
    if errors:
        print(f"\n{len(errors)} errors (re-run to retry):")
        for sym, err in errors[:20]:
            print(f"  {sym}: {err}")

    # Coverage summary vs event needs
    manifest = load_manifest(args.cache_dir)
    ok = sum(1 for m in manifest.values() if m["status"] == "ok")
    empty = [s for s, m in manifest.items() if m["status"] == "empty"]
    err_syms = [s for s, m in manifest.items() if m["status"] == "error"]
    print(f"\nManifest: {ok} ok, {len(empty)} empty, {len(err_syms)} error "
          f"of {len(windows)} needed")
    if empty:
        print(f"empty (no Massive data): {','.join(sorted(empty)[:40])}")
    if err_syms:
        print(f"errors: {','.join(sorted(err_syms)[:40])}")


if __name__ == "__main__":
    main()
