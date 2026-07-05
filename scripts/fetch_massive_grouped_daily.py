"""Fetch full-market US daily OHLCV from Massive.com grouped-daily endpoint.

One call per trading day returns every US stock's daily bar — the raw
material for the EP 2.0 Track B scanner (standalone momentum-leader base
breakouts, no gap prerequisite).

    GET /v2/aggs/grouped/locale/us/market/stocks/{YYYY-MM-DD}?adjusted=true

Cache layout (resumable):

    market data download/massive_grouped/
        _manifest.csv          # date, n_rows, status(ok|empty|error), fetched_at
        2021-07-06.csv         # symbol,open,high,low,close,volume
        ...

~1,130 trading days for 2021-07-06..today; Starter tier is unlimited so the
run is bandwidth-bound (~10-20 min). Weekends are skipped by construction;
market holidays return empty and are recorded status=empty (not retried).

Run: trading-bot/.venv/bin/python scripts/fetch_massive_grouped_daily.py
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://api.massive.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "market data download"
DEFAULT_START = "2021-07-06"  # Massive Starter plan 5y boundary


def _env_file_key() -> str | None:
    for env_path in (REPO_ROOT / ".env",
                     Path.home() / "Documents/moomoo-bot1/moomoo-api/.env"):
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("MASSIVE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def fetch_day(session: requests.Session, api_key: str, day: str,
              max_retries: int = 3) -> pd.DataFrame:
    url = f"{BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{day}"
    params = {"adjusted": "true", "apiKey": api_key}
    headers = {"Authorization": f"Bearer {api_key}"}
    attempt = 0
    while True:
        try:
            resp = session.get(url, params=params, headers=headers, timeout=60)
        except requests.RequestException as exc:
            attempt += 1
            if attempt > max_retries:
                raise RuntimeError(f"network error after retries: {exc}")
            time.sleep(2 * attempt)
            continue
        if resp.status_code == 429:
            attempt += 1
            if attempt > max_retries:
                raise RuntimeError("rate limited (429) after retries")
            time.sleep(5 * attempt)
            continue
        if resp.status_code in (401, 403):
            raise RuntimeError(f"auth failed ({resp.status_code})")
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:150]}")
        results = resp.json().get("results") or []
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df = df.rename(columns={"T": "symbol", "o": "open", "h": "high",
                                "l": "low", "c": "close", "v": "volume"})
        return df[["symbol", "open", "high", "low", "close", "volume"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=DATA_DIR / "massive_grouped")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None, help="default: today")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--api-key", default=os.environ.get("MASSIVE_API_KEY") or _env_file_key())
    args = ap.parse_args()
    if not args.api_key:
        raise SystemExit("MASSIVE_API_KEY not found")

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    mpath = args.cache_dir / "_manifest.csv"
    done = {}
    if mpath.exists():
        with open(mpath, newline="") as f:
            for row in csv.DictReader(f):
                done[row["date"]] = row["status"]

    end = args.end or pd.Timestamp.now().strftime("%Y-%m-%d")
    days = [d.strftime("%Y-%m-%d")
            for d in pd.bdate_range(args.start, end)]
    todo = [d for d in days if done.get(d) not in ("ok", "empty")]
    print(f"{len(days)} weekdays in range; {len(todo)} to fetch")

    lock = threading.Lock()
    local = threading.local()

    def get_session():
        if not hasattr(local, "s"):
            local.s = requests.Session()
        return local.s

    def work(day: str):
        try:
            df = fetch_day(get_session(), args.api_key, day)
            status = "ok" if len(df) else "empty"
            if len(df):
                tmp = args.cache_dir / f"{day}.csv.tmp"
                df.to_csv(tmp, index=False)
                tmp.rename(args.cache_dir / f"{day}.csv")
            row = {"date": day, "n_rows": len(df), "status": status,
                   "fetched_at": datetime.now().isoformat(timespec="seconds")}
        except Exception as exc:
            row = {"date": day, "n_rows": 0, "status": "error",
                   "fetched_at": datetime.now().isoformat(timespec="seconds"),
                   "error": str(exc)[:150]}
        with lock:
            new = not mpath.exists()
            with open(mpath, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["date", "n_rows", "status",
                                                  "fetched_at", "error"])
                if new:
                    w.writeheader()
                w.writerow(row)
        return day, row["status"]

    started = time.time()
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(work, d) for d in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            day, status = fut.result()
            counts[status] = counts.get(status, 0) + 1
            if status == "error":
                print(f"[{i}/{len(todo)}] {day}: ERROR", file=sys.stderr)
            elif i % 100 == 0 or i == len(todo):
                print(f"[{i}/{len(todo)}] {day} {status} ({time.time()-started:.0f}s)")
    print(f"\nDone in {time.time()-started:.0f}s: {counts}")


if __name__ == "__main__":
    main()
