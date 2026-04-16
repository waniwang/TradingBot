"""
EP News EOD scanner.

Runs at ~3:00 PM ET to find news-driven gap-up stocks using actual market Open prices.
Applies the same Spikeet-derived universe filters as the EP Earnings scanner,
but EXCLUDES stocks that had earnings today (those are handled by ep_earnings.py).

Filters (in order):
  Phase A (broad universe via yfinance, then broker snapshots):
    1. Broad-universe gap pre-screen (yfinance): gap% >= 8%, prev_close >= $3
    2. Security class heuristic (alpha symbols <= 5 chars)
    3. Broker snapshot refresh (real-time prev_close/open/volume)
    4. Gap% >= 8% (actual Open vs prev Close)
    5. Open > yesterday's high

  Phase B (yfinance daily bars, batch):
    5. Open > 200-day SMA
    (RVOL is computed for enrichment but NOT filtered — Spikeet picks often have RVOL < 1)

  Phase C (yfinance per-ticker, slowest):
    8. Market cap >= $1B
    9. Security class = EQUITY (not ETF/warrant)
   10. Did NOT have earnings today (news catalyst, not earnings)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import yfinance as yf

from strategies.ep_earnings.scanner import _get_ticker_info

logger = logging.getLogger(__name__)


def scan_ep_news(
    config: dict,
    client,
    max_results: int = 30,
) -> list[dict[str, Any]]:
    """
    EOD scanner for EP news gap-up candidates.

    Runs at ~3:00 PM ET using actual market Open prices (not premarket).
    Returns candidates sorted by gap% descending.

    Args:
        config: full app config dict
        client: AlpacaClient instance
        max_results: cap on returned candidates

    Returns:
        List of dicts with keys: ticker, gap_pct, open_price, prev_close,
        prev_high, current_price, today_volume, sma_200, market_cap, rvol,
        today_high, today_low, setup_type
    """
    sig_cfg = config.get("signals", {})
    min_gap_pct = float(sig_cfg.get("ep_news_min_gap_pct", 8.0))
    min_price = float(sig_cfg.get("ep_news_min_price", 3.0))
    min_market_cap = float(sig_cfg.get("ep_news_min_market_cap", 1_000_000_000))
    exclude_earnings = bool(sig_cfg.get("ep_news_exclude_earnings", True))
    require_open_above_prev_high = bool(sig_cfg.get("ep_news_require_open_above_prev_high", True))
    require_above_200d_sma = bool(sig_cfg.get("ep_news_require_above_200d_sma", True))

    # ---------------------------------------------------------------
    # Phase A: broad-universe gap pre-screen (yfinance) + snapshot filters
    # ---------------------------------------------------------------
    from scanner.gap_screen import scan_broad_gaps

    # Pull fresh active-tradable universe from Alpaca so delisted tickers
    # don't waste yfinance retries. Falls back to static broad_universe.txt.
    universe = client.get_tradable_universe() or None
    movers = scan_broad_gaps(min_gap_pct=min_gap_pct, min_price=min_price, universe=universe)
    if not movers:
        logger.warning("EP News scan: no broad-universe gap candidates")
        return []

    # Symbol validation
    symbols = []
    for m in movers:
        sym = m["symbol"]
        if len(sym) <= 5 and sym.isalpha():
            symbols.append(sym)

    if not symbols:
        logger.info("EP News scan: no valid symbols after initial filter")
        return []

    # Fetch snapshots (includes prev_close, prev_high, open, daily_volume)
    snapshots = client.get_snapshots(symbols)

    candidates = []
    for sym in symbols:
        snap = snapshots.get(sym)
        if not snap:
            continue

        prev_close = snap["prev_close"]
        prev_high = snap.get("prev_high", 0)
        open_price = snap.get("open", 0)
        latest_price = snap["latest_price"]
        daily_volume = snap["daily_volume"]
        today_high = snap.get("today_high", 0)
        today_low = snap.get("today_low", 0)

        if prev_close <= 0 or open_price <= 0:
            continue

        # Filter: prev close > min price
        if prev_close < min_price:
            continue

        # Filter: gap% using actual market Open
        gap_pct = (open_price - prev_close) / prev_close * 100
        if gap_pct < min_gap_pct:
            continue

        # Filter: open > yesterday's high
        if require_open_above_prev_high and prev_high > 0 and open_price <= prev_high:
            logger.debug("%s: open %.2f <= prev high %.2f, skipping", sym, open_price, prev_high)
            continue

        candidates.append({
            "ticker": sym,
            "gap_pct": round(gap_pct, 2),
            "open_price": round(open_price, 2),
            "prev_close": round(prev_close, 2),
            "prev_high": round(prev_high, 2),
            "current_price": round(latest_price, 2),
            "today_volume": daily_volume,
            "today_high": round(today_high, 2),
            "today_low": round(today_low, 2),
            "setup_type": "ep_news",
        })

    if not candidates:
        logger.info("EP News scan: no candidates after Phase A filters")
        return []

    logger.info("EP News scan Phase A: %d candidates after gap/price/open filters", len(candidates))

    # ---------------------------------------------------------------
    # Phase B: Daily bars enrichment (yfinance batch)
    # ---------------------------------------------------------------
    from signals.base import compute_sma

    tickers_to_check = [c["ticker"] for c in candidates]
    try:
        bars = client.get_daily_bars_batch(tickers_to_check, days=300)
    except Exception as e:
        logger.warning("EP News scan: failed to fetch daily bars: %s", e)
        bars = {}

    filtered_b = []
    for c in candidates:
        sym = c["ticker"]
        df = bars.get(sym)
        if df is None or (hasattr(df, "empty") and df.empty):
            logger.debug("%s: no daily bars available, skipping", sym)
            continue

        closes = df["close"].values if hasattr(df, "values") else list(df["close"])
        volumes = df["volume"].values if hasattr(df, "values") else list(df["volume"])

        # Filter: open > 200-day SMA
        if require_above_200d_sma:
            sma_200 = compute_sma(list(closes), 200)
            if sma_200 is None:
                logger.debug("%s: insufficient data for 200d SMA, skipping", sym)
                continue
            if c["open_price"] <= sma_200:
                logger.debug("%s: open %.2f <= 200d SMA %.2f, skipping", sym, c["open_price"], sma_200)
                continue
            c["sma_200"] = round(sma_200, 2)
        else:
            c["sma_200"] = None

        # Compute RVOL for display/enrichment (not used as filter — Spikeet picks often have RVOL < 1)
        if len(volumes) >= 14:
            avg_14d_vol = float(np.mean(volumes[-14:]))
            c["rvol"] = round(c["today_volume"] / avg_14d_vol, 2) if avg_14d_vol > 0 else 0.0
        else:
            c["rvol"] = 0.0

        filtered_b.append(c)

    candidates = filtered_b

    if not candidates:
        logger.info("EP News scan: no candidates after Phase B filters")
        return []

    logger.info("EP News scan Phase B: %d candidates after SMA/RVOL filters", len(candidates))

    # ---------------------------------------------------------------
    # Phase C: Per-ticker yfinance for market cap, security class, NO earnings
    # ---------------------------------------------------------------
    today = date.today()
    filtered_c = []

    for c in candidates:
        sym = c["ticker"]

        # Market cap + security class check
        market_cap, quote_type = _get_ticker_info(sym)

        if market_cap < min_market_cap:
            logger.debug(
                "%s: market cap $%.0fM < $%.0fM min, skipping",
                sym, market_cap / 1e6, min_market_cap / 1e6,
            )
            continue

        if quote_type and quote_type.upper() != "EQUITY":
            logger.debug("%s: quoteType=%s (not EQUITY), skipping", sym, quote_type)
            continue

        c["market_cap"] = market_cap

        # Earnings exclusion: skip if this is an earnings gap OR if we can't confirm
        if exclude_earnings:
            confirmed_no_earnings = _confirm_no_earnings(sym, today)
            if not confirmed_no_earnings:
                logger.debug("%s: has earnings or earnings check failed, skipping", sym)
                continue

        filtered_c.append(c)

    candidates = filtered_c

    # Sort by gap% descending
    candidates.sort(key=lambda x: x["gap_pct"], reverse=True)
    result = candidates[:max_results]

    logger.info(
        "EP News scan complete: %d candidates (filters: gap>=%.0f%%, price>$%.0f, mcap>=$%.0fB)",
        len(result), min_gap_pct, min_price, min_market_cap / 1e9,
    )
    return result


def _confirm_no_earnings(ticker: str, today: date) -> bool:
    """
    Return True only if we *successfully confirmed* no earnings today/yesterday.

    Unlike _check_earnings_today (which returns False on failure, conservative
    for EP Earnings), this function is conservative for EP News: if the API
    fails, we return False (can't confirm no earnings → skip the stock).

    Returns:
        True  — API succeeded and no earnings found (safe to treat as news gap)
        False — earnings found OR API failed (skip this stock)
    """
    try:
        t = yf.Ticker(ticker)
        dates = t.get_earnings_dates(limit=4)
        if dates is None or (hasattr(dates, "empty") and dates.empty):
            return True  # API succeeded, no dates at all → confirmed no earnings

        yesterday = today - timedelta(days=1)
        for dt in dates.index:
            d = dt.date() if hasattr(dt, "date") else dt
            if d in (today, yesterday):
                return False  # confirmed: has earnings today/yesterday
        return True  # API succeeded, no matching dates → confirmed no earnings
    except Exception:
        logger.debug("%s: earnings check failed, skipping conservatively", ticker)
        return False  # API failed → can't confirm → skip
