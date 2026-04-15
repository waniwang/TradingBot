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
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE_FILE = Path(__file__).resolve().parent.parent / "broad_universe.txt"

# Throttling defaults — tuned to stay under Yahoo's per-IP rate limit while
# completing the ~5K-ticker scan well within the 3:00 → 3:50 PM execution window.
DEFAULT_BATCH_SIZE = 200
DEFAULT_INTER_BATCH_DELAY_S = 2.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_BASE_S = 4.0  # 4s, 16s, 64s


def load_universe(path: Path | None = None) -> list[str]:
    p = path or DEFAULT_UNIVERSE_FILE
    with open(p) as f:
        return [line.strip() for line in f if line.strip()]


def _is_rate_limit_error(exc: BaseException) -> bool:
    """yfinance raises YFRateLimitError; we duck-type to avoid hard import."""
    return type(exc).__name__ == "YFRateLimitError" or "rate limit" in str(exc).lower()


def _download_batch_with_retry(
    batch: list[str],
    max_retries: int,
    retry_backoff_base_s: float,
    sleep_fn=time.sleep,
):
    """Call yf.download with exponential backoff on rate-limit errors."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
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
            return df
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if not _is_rate_limit_error(e) or attempt == max_retries - 1:
                logger.warning("gap_screen batch download failed: %s", e)
                return None
            wait_s = retry_backoff_base_s * (4 ** attempt)
            logger.warning(
                "gap_screen rate-limited (attempt %d/%d), sleeping %.1fs",
                attempt + 1, max_retries, wait_s,
            )
            sleep_fn(wait_s)
    logger.warning("gap_screen batch exhausted retries: %s", last_exc)
    return None


def scan_broad_gaps(
    min_gap_pct: float = 8.0,
    min_price: float = 3.0,
    universe: list[str] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    inter_batch_delay_s: float = DEFAULT_INTER_BATCH_DELAY_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_base_s: float = DEFAULT_RETRY_BACKOFF_BASE_S,
    sleep_fn=time.sleep,
) -> list[dict]:
    """
    Return candidates from the broad universe whose today's Open gaps up
    >= min_gap_pct above prev close (and prev close >= min_price).

    Throttled to stay under Yahoo's rate limit: small batches with a delay
    between them and exponential-backoff retry on YFRateLimitError. With the
    defaults a 5K-ticker universe completes in ~2 minutes — well inside the
    50-minute scan-to-execute window.

    `sleep_fn` is injected so tests can run without real waits.

    Returns a list shaped like broker movers: [{symbol, percent_change, price}, ...]
    sorted by gap% descending, uncapped.
    """
    tickers = universe if universe is not None else load_universe()
    if not tickers:
        return []

    results: list[dict] = []
    total_batches = (len(tickers) + batch_size - 1) // batch_size

    for batch_idx, i in enumerate(range(0, len(tickers), batch_size)):
        batch = tickers[i : i + batch_size]
        df = _download_batch_with_retry(
            batch, max_retries=max_retries,
            retry_backoff_base_s=retry_backoff_base_s,
            sleep_fn=sleep_fn,
        )
        if df is None or df.empty:
            if batch_idx < total_batches - 1:
                sleep_fn(inter_batch_delay_s)
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

        # Pace the next batch to stay under Yahoo's per-IP rate limit
        if batch_idx < total_batches - 1:
            sleep_fn(inter_batch_delay_s)

    results.sort(key=lambda x: -x["percent_change"])
    logger.info(
        "gap_screen: %d candidates from %d-ticker universe (gap>=%.1f%%, price>=$%.1f)",
        len(results), len(tickers), min_gap_pct, min_price,
    )
    return results
