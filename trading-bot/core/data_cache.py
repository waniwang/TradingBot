"""
Shared daily bar cache for the trading bot.

Populated during premarket scan and read by on_bar callbacks.
Thread-safe via _cache_lock.
"""

from __future__ import annotations

import logging
import threading
from datetime import date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global caches (populated during the pre-market phase)
# ---------------------------------------------------------------------------

daily_bars_cache: dict[str, list[dict]] = {}
daily_closes_cache: dict[str, list[float]] = {}
daily_volumes_cache: dict[str, list[int]] = {}
daily_highs_cache: dict[str, list[float]] = {}
daily_lows_cache: dict[str, list[float]] = {}
cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Intraday price cache — updated on every on_bar tick from the stream.
# Used by fetch_current_price so day2_confirm never needs a REST snapshot call.
# Stores (price, date) so yesterday's prices don't bleed into today.
# ---------------------------------------------------------------------------

_intraday_price_cache: dict[str, tuple[float, date]] = {}


def update_intraday_price(ticker: str, price: float) -> None:
    """Record the latest streamed bar close for a ticker. Called from on_bar."""
    with cache_lock:
        _intraday_price_cache[ticker] = (price, date.today())


def get_intraday_price(ticker: str) -> float | None:
    """Return today's latest streamed price, or None if not yet received."""
    with cache_lock:
        entry = _intraday_price_cache.get(ticker)
    if entry is None:
        return None
    price, cached_date = entry
    return price if cached_date == date.today() else None


def clear_daily_caches():
    """Clear all daily bar caches. Called at start of each trading day."""
    with cache_lock:
        daily_bars_cache.clear()
        daily_closes_cache.clear()
        daily_volumes_cache.clear()
        daily_highs_cache.clear()
        daily_lows_cache.clear()
    logger.info("Daily bar caches cleared")


def prefetch_daily_bars(client, tickers: list[str], notify=None):
    """
    Pre-fetch daily bars for watchlist tickers using yfinance batch download.

    Populates caches so on_bar callbacks use cached data instead of
    making per-ticker REST calls to Alpaca (which are slow on IEX).
    """
    if not tickers:
        return
    logger.info("Pre-fetching daily bars for %d watchlist tickers...", len(tickers))
    try:
        bars_by_symbol = client.get_daily_bars_batch(tickers, days=130)
        with cache_lock:
            for ticker, df in bars_by_symbol.items():
                if df is None or df.empty:
                    continue
                bars_list = df.to_dict("records")
                daily_bars_cache[ticker] = bars_list
                daily_closes_cache[ticker] = [b["close"] for b in bars_list]
                daily_volumes_cache[ticker] = [int(b["volume"]) for b in bars_list]
                daily_highs_cache[ticker] = [b["high"] for b in bars_list]
                daily_lows_cache[ticker] = [b["low"] for b in bars_list]
        logger.info(
            "Pre-fetched daily bars for %d/%d tickers",
            len(bars_by_symbol),
            len(tickers),
        )
        if len(bars_by_symbol) == 0 and len(tickers) > 0:
            msg = (
                f"WARNING: Daily bars returned 0/{len(tickers)} tickers"
                " — signals may lack ATR/RVOL data"
            )
            logger.warning(msg)
            if notify:
                notify(msg)
    except Exception as e:
        logger.error("Daily bars pre-fetch failed: %s", e)
        if notify:
            notify(
                f"WARNING: Daily bars pre-fetch failed for {len(tickers)} tickers: {e}"
            )
