"""
EP Breakout gap-event scanner (EP 2.0 Track A).

Runs at ~3:15 PM ET (after the EP earnings/news scans) to find BIG + LOUD +
VOLATILE gap events. Unlike the ep_earnings/ep_news scanners, passing
candidates are NOT executed the same day — they become stage="watching"
Watchlist rows that the daily 3:50 PM confirm job tracks for up to
`bo_window` sessions, entering only on a rested breakout above the gap-day
high (see plugin.py).

Filters (validated 2026-07-05, docs/research/ep2_validation.md):
  Phase A (Alpaca snapshot — full universe in ~5s):
    1. Gap% >= 8% (today_open vs prev_close)
    2. prev_close >= $3
    3. Open > yesterday's high
    4. Dollar volume today >= $100M (LOUD — replaces the old share-volume
       CAP that systematically rejected 2026's theme leaders)
  Phase B (yfinance daily bars, batch):
    5. Open > 200-day SMA
    6. ATR%(10d) >= 3% (VOLATILE)
  Phase C (yfinance per-ticker):
    7. Market cap >= $5B (BIG)
    8. Security class = EQUITY

Catalyst-agnostic by design: earnings AND news gaps both qualify (the
backtest universe was the union of both event datasets).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)


def scan_ep_breakout(
    config: dict,
    client,
    max_results: int = 20,
    notify=None,
) -> list[dict[str, Any]]:
    """EOD scanner for EP breakout gap events. Returns candidates sorted by
    gap% descending, each carrying the gap-day reference levels the confirm
    job needs (gap_high / gap_low are refreshed from daily bars at confirm
    time, so the 3:15 PM snapshot values here are provisional)."""
    sig_cfg = config.get("signals", {})
    min_gap_pct = float(sig_cfg.get("ep_breakout_min_gap_pct", 8.0))
    min_price = float(sig_cfg.get("ep_breakout_min_price", 3.0))
    min_market_cap = float(sig_cfg.get("ep_breakout_min_market_cap", 5_000_000_000))
    min_dollar_vol = float(sig_cfg.get("ep_breakout_min_dollar_vol", 100_000_000))
    min_atr_pct = float(sig_cfg.get("ep_breakout_min_atr_pct", 3.0))
    require_open_above_prev_high = bool(
        sig_cfg.get("ep_breakout_require_open_above_prev_high", True))
    require_above_200d_sma = bool(
        sig_cfg.get("ep_breakout_require_above_200d_sma", True))

    # ---------------------------------------------------------------
    # Phase A: Alpaca snapshot gap pre-screen
    # ---------------------------------------------------------------
    from scanner.gap_screen import scan_snapshot_gaps
    from core.retry import with_network_retries

    universe = with_network_retries(
        lambda: client.get_tradable_universe(),
        label="ep_breakout.get_tradable_universe",
    )
    movers = with_network_retries(
        lambda: scan_snapshot_gaps(
            client,
            min_gap_pct=min_gap_pct,
            min_price=min_price,
            universe=universe,
        ),
        label="ep_breakout.scan_snapshot_gaps",
    )
    if not movers:
        logger.info("EP Breakout scan: no gap candidates from snapshot scan")
        return []

    symbols = [m["symbol"] for m in movers]
    snapshots = with_network_retries(
        lambda: client.get_snapshots(symbols),
        label="ep_breakout.get_snapshots",
    )

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

        if prev_close <= 0 or open_price <= 0 or latest_price <= 0:
            continue
        if prev_close < min_price:
            continue

        gap_pct = (open_price - prev_close) / prev_close * 100
        if gap_pct < min_gap_pct:
            continue

        if require_open_above_prev_high and prev_high > 0 and open_price <= prev_high:
            continue

        # LOUD filter: running dollar volume at ~3:15 PM. Slightly
        # conservative vs the backtest's full-day figure (~90% of the day
        # has printed by then) — a name that only clears $100M in the last
        # 45 minutes was marginal anyway.
        dollar_vol = latest_price * daily_volume
        if dollar_vol < min_dollar_vol:
            logger.debug("%s: $vol %.0fM < %.0fM min, skipping",
                         sym, dollar_vol / 1e6, min_dollar_vol / 1e6)
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
            "dollar_vol": round(dollar_vol, 0),
            "setup_type": "ep_breakout",
        })

    if not candidates:
        logger.info("EP Breakout scan: no candidates after Phase A filters")
        return []

    logger.info("EP Breakout scan Phase A: %d candidates", len(candidates))

    # ---------------------------------------------------------------
    # Phase B: daily-bars enrichment — 200d SMA + ATR% (yfinance batch).
    # Fetch errors propagate (no silent bars={} fallback) per CLAUDE.md.
    # ---------------------------------------------------------------
    from signals.base import compute_sma, compute_atr_from_list

    tickers_to_check = [c["ticker"] for c in candidates]
    bars = with_network_retries(
        lambda: client.get_daily_bars_batch(tickers_to_check, days=300),
        label="ep_breakout.get_daily_bars_batch",
    )

    filtered_b = []
    for c in candidates:
        sym = c["ticker"]
        df = bars.get(sym)
        if df is None or (hasattr(df, "empty") and df.empty):
            logger.debug("%s: no daily bars, skipping", sym)
            continue

        closes = list(df["close"].values)
        highs = list(df["high"].values)
        lows = list(df["low"].values)

        if require_above_200d_sma:
            sma_200 = compute_sma(closes, 200)
            if sma_200 is None or c["open_price"] <= sma_200:
                logger.debug("%s: open below/no 200d SMA, skipping", sym)
                continue
            c["sma_200"] = round(sma_200, 2)
        else:
            c["sma_200"] = None

        # VOLATILE filter: 10-day ATR as % of last close
        atr = compute_atr_from_list(highs, lows, closes, period=10)
        if atr is None or closes[-1] <= 0:
            logger.debug("%s: cannot compute ATR, skipping", sym)
            continue
        atr_pct = atr / closes[-1] * 100
        if atr_pct < min_atr_pct:
            logger.debug("%s: ATR%% %.1f < %.1f min, skipping", sym, atr_pct, min_atr_pct)
            continue
        c["atr_pct"] = round(atr_pct, 2)

        filtered_b.append(c)

    candidates = filtered_b
    if not candidates:
        logger.info("EP Breakout scan: no candidates after Phase B filters")
        return []

    logger.info("EP Breakout scan Phase B: %d candidates", len(candidates))

    # ---------------------------------------------------------------
    # Phase C: market cap + security class (yfinance per-ticker)
    # ---------------------------------------------------------------
    filtered_c = []
    for c in candidates:
        sym = c["ticker"]
        market_cap, quote_type = _get_ticker_info(sym)
        if market_cap < min_market_cap:
            logger.debug("%s: mcap $%.1fB < $%.1fB min, skipping",
                         sym, market_cap / 1e9, min_market_cap / 1e9)
            continue
        if quote_type and quote_type.upper() != "EQUITY":
            logger.debug("%s: quoteType=%s, skipping", sym, quote_type)
            continue
        c["market_cap"] = market_cap
        filtered_c.append(c)

    candidates = filtered_c
    candidates.sort(key=lambda x: x["gap_pct"], reverse=True)
    result = candidates[:max_results]

    logger.info(
        "EP Breakout scan complete: %d candidates (gap>=%.0f%%, $vol>=$%.0fM, "
        "ATR>=%.1f%%, mcap>=$%.0fB)",
        len(result), min_gap_pct, min_dollar_vol / 1e6, min_atr_pct,
        min_market_cap / 1e9,
    )
    return result


def _get_ticker_info(ticker: str) -> tuple[float, str]:
    """Return (market_cap, quote_type) from yfinance. Raises on API failure."""
    info = yf.Ticker(ticker).info
    return (
        float(info.get("marketCap", 0) or 0),
        str(info.get("quoteType", "") or ""),
    )
