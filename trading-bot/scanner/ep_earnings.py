"""
EP Earnings EOD scanner.

Runs at ~3:00 PM ET to find earnings gap-up stocks using actual market Open prices.
Applies Spikeet-derived scanner filters as the universe/pre-filter layer.
Strategy entry filters (CHG-OPEN%, close_in_range, etc.) are applied separately.

Filters (in order):
  Phase A (Alpaca, fast):
    1. Security class heuristic (alpha symbols <= 5 chars)
    2. Previous close > min price ($3)
    3. Gap% > 8% (actual Open vs prev Close)
    4. Open > yesterday's high

  Phase B (yfinance daily bars, batch):
    5. Open > 200-day SMA
    6. Today's RVOL > 1.0 (today's volume / 14d avg daily volume)
    7. Prior 6-month gain < 50%

  Phase C (yfinance per-ticker, slowest):
    8. Market cap > $800M
    9. Security class = EQUITY (not ETF/warrant)
   10. Had earnings today
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def scan_ep_earnings(
    config: dict,
    client,
    max_results: int = 30,
) -> list[dict[str, Any]]:
    """
    EOD scanner for EP earnings gap-up candidates.

    Runs at ~3:00 PM ET using actual market Open prices (not premarket).
    Returns candidates sorted by gap% descending.

    Args:
        config: full app config dict
        client: AlpacaClient instance
        max_results: cap on returned candidates

    Returns:
        List of dicts with keys: ticker, gap_pct, open_price, prev_close,
        prev_high, current_price, today_volume, sma_200, market_cap, rvol,
        setup_type
    """
    sig_cfg = config.get("signals", {})
    min_gap_pct = float(sig_cfg.get("ep_earnings_min_gap_pct", 8.0))
    min_price = float(sig_cfg.get("ep_earnings_min_price", 3.0))
    min_market_cap = float(sig_cfg.get("ep_earnings_min_market_cap", 800_000_000))
    require_earnings = bool(sig_cfg.get("ep_earnings_require_earnings", True))
    require_open_above_prev_high = bool(sig_cfg.get("ep_earnings_require_open_above_prev_high", True))
    require_above_200d_sma = bool(sig_cfg.get("ep_earnings_require_above_200d_sma", True))
    min_rvol = float(sig_cfg.get("ep_earnings_min_rvol", 1.0))

    # ---------------------------------------------------------------
    # Phase A: Alpaca screener + snapshot filters (fast)
    # ---------------------------------------------------------------
    movers = client.get_market_movers_gainers(top=50)
    if not movers:
        logger.warning("EP Earnings scan: no market movers returned")
        return []

    # Symbol validation
    symbols = []
    for m in movers:
        sym = m["symbol"]
        if len(sym) <= 5 and sym.isalpha():
            symbols.append(sym)

    if not symbols:
        logger.info("EP Earnings scan: no valid symbols after initial filter")
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
            "setup_type": "episodic_pivot",
        })

    if not candidates:
        logger.info("EP Earnings scan: no candidates after Phase A filters")
        return []

    logger.info("EP Earnings scan Phase A: %d candidates after gap/price/open filters", len(candidates))

    # ---------------------------------------------------------------
    # Phase B: Daily bars enrichment (yfinance batch)
    # ---------------------------------------------------------------
    from signals.base import compute_sma

    tickers_to_check = [c["ticker"] for c in candidates]
    try:
        bars = client.get_daily_bars_batch(tickers_to_check, days=300)
    except Exception as e:
        logger.warning("EP Earnings scan: failed to fetch daily bars: %s", e)
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

        # Filter: today's RVOL (volume at scan time / 14d avg daily volume)
        if len(volumes) >= 14:
            avg_14d_vol = float(np.mean(volumes[-14:]))
            if avg_14d_vol > 0:
                rvol = c["today_volume"] / avg_14d_vol
                if rvol < min_rvol:
                    logger.debug(
                        "%s: RVOL %.2f < min %.2f (vol=%d, avg14d=%d), skipping",
                        sym, rvol, min_rvol, c["today_volume"], int(avg_14d_vol),
                    )
                    continue
                c["rvol"] = round(rvol, 2)
            else:
                c["rvol"] = 0.0
        else:
            c["rvol"] = 0.0

        # Filter: prior 6-month gain < 50%
        if len(closes) >= 60:
            prior_gain = (closes[-2] - closes[0]) / closes[0] * 100 if len(closes) >= 2 else 0
            if prior_gain >= 50:
                logger.debug("%s: prior 6m gain %.1f%% >= 50%%, skipping", sym, prior_gain)
                continue

        filtered_b.append(c)

    candidates = filtered_b

    if not candidates:
        logger.info("EP Earnings scan: no candidates after Phase B filters")
        return []

    logger.info("EP Earnings scan Phase B: %d candidates after SMA/RVOL filters", len(candidates))

    # ---------------------------------------------------------------
    # Phase C: Per-ticker yfinance for market cap, security class, earnings
    # ---------------------------------------------------------------
    from datetime import date as date_type

    import yfinance as yf

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

        # Earnings check
        if require_earnings:
            if not _check_earnings_today(sym, today):
                logger.debug("%s: no earnings today/yesterday, skipping", sym)
                continue

        filtered_c.append(c)

    candidates = filtered_c

    # Sort by gap% descending
    candidates.sort(key=lambda x: x["gap_pct"], reverse=True)
    result = candidates[:max_results]

    logger.info(
        "EP Earnings scan complete: %d candidates (filters: gap>=%.0f%%, price>$%.0f, mcap>$%.0fM)",
        len(result), min_gap_pct, min_price, min_market_cap / 1e6,
    )
    return result


def _get_ticker_info(ticker: str) -> tuple[float, str]:
    """
    Return (market_cap, quote_type) from yfinance.

    Returns (0.0, "") on failure.
    """
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info
        return (
            float(info.get("marketCap", 0) or 0),
            str(info.get("quoteType", "") or ""),
        )
    except Exception:
        logger.debug("%s: failed to fetch ticker info", ticker)
        return (0.0, "")


def _check_earnings_today(ticker: str, today: date) -> bool:
    """
    Check if ticker had earnings reported today or last evening (yesterday).

    Uses yfinance earnings calendar. Returns False on failure (conservative).
    """
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        dates = t.get_earnings_dates(limit=4)
        if dates is None or (hasattr(dates, "empty") and dates.empty):
            return False

        yesterday = today - timedelta(days=1)
        for dt in dates.index:
            d = dt.date() if hasattr(dt, "date") else dt
            if d in (today, yesterday):
                return True
        return False
    except Exception:
        logger.debug("%s: failed to fetch earnings dates", ticker)
        return False
