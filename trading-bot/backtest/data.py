"""
Historical data fetcher for backtesting.

Downloads daily OHLCV bars via yfinance and caches to local parquet files
to avoid repeated downloads.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path("backtest_cache")


def fetch_historical_bars(
    tickers: list[str],
    start_date: str,
    end_date: str,
    cache_dir: Path | None = None,
    batch_size: int = 500,
) -> dict[str, pd.DataFrame]:
    """
    Download daily OHLCV for a list of tickers via yfinance.

    Caches each ticker to a parquet file under cache_dir.
    If cached data covers the requested range, it is loaded from disk.

    Args:
        tickers: list of stock symbols
        start_date: "YYYY-MM-DD" start of data range
        end_date: "YYYY-MM-DD" end of data range
        cache_dir: directory for parquet cache (default: backtest_cache/)
        batch_size: number of tickers per yfinance batch

    Returns:
        dict mapping ticker -> DataFrame with columns
        [date, open, high, low, close, volume]
    """
    import yfinance as yf

    cache = cache_dir or CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)

    result: dict[str, pd.DataFrame] = {}
    to_download: list[str] = []

    # Check cache first
    for ticker in tickers:
        parquet_path = cache / f"{ticker}_{start_date}_{end_date}.parquet"
        if parquet_path.exists():
            try:
                df = pd.read_parquet(parquet_path)
                if not df.empty:
                    result[ticker] = df
                    continue
            except Exception:
                pass
        to_download.append(ticker)

    if to_download:
        logger.info(
            "Downloading %d tickers from yfinance (%d cached)",
            len(to_download), len(result),
        )

        # Download in batches
        for i in range(0, len(to_download), batch_size):
            batch = to_download[i : i + batch_size]
            batch_str = " ".join(batch)
            try:
                raw = yf.download(
                    batch_str,
                    start=start_date,
                    end=end_date,
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
            except Exception as e:
                logger.error("yfinance download failed for batch %d: %s", i, e)
                continue

            for ticker in batch:
                try:
                    if len(batch) == 1:
                        df = raw.copy()
                    else:
                        df = raw[ticker].copy()

                    df = df.dropna(subset=["Close"])
                    if df.empty:
                        continue

                    df = df.reset_index()
                    df.columns = [c.lower() for c in df.columns]

                    # Standardize column names
                    col_map = {}
                    for col in df.columns:
                        if col in ("date", "datetime"):
                            col_map[col] = "date"

                    df = df.rename(columns=col_map)

                    # Keep only needed columns
                    keep = ["date", "open", "high", "low", "close", "volume"]
                    df = df[[c for c in keep if c in df.columns]]

                    # Cache to parquet
                    parquet_path = cache / f"{ticker}_{start_date}_{end_date}.parquet"
                    df.to_parquet(parquet_path, index=False)
                    result[ticker] = df
                except Exception as e:
                    logger.debug("Failed to process %s: %s", ticker, e)

    logger.info("Historical data ready: %d/%d tickers", len(result), len(tickers))
    return result


def get_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 constituents from Wikipedia."""
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )
        df = tables[0]
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        return tickers
    except Exception as e:
        logger.error("Failed to fetch S&P 500 tickers: %s", e)
        return []
