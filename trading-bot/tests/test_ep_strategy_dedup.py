"""
A single ticker must not fire both Strategy A and Strategy B entries on the same
gap day. A is the tighter filter set, so when both pass we keep A and drop B.
This prevents the same idea from consuming two position slots and doubling the
risk budget.

Only EP News still has A/B dedup — EP Earnings A was dropped 2026-05-08, leaving
only B for that setup.

See strategies/ep_news/strategy.py::evaluate_ep_news_strategies for the
enforcement point.
"""

from __future__ import annotations

import pandas as pd

from strategies.ep_news.strategy import evaluate_ep_news_strategies


def _daily_bars_with_prev_10d(ticker: str, prev_10d_change_pct: float) -> dict:
    """Build a daily bar DataFrame where closes[-1] / closes[-11] produces the
    requested prev_10d_change_pct. The list length must be exactly 11 so
    closes[-11] is the first element (gap day minus 10 trading days)."""
    close_10d_ago = 100.0
    close_yesterday = close_10d_ago * (1 + prev_10d_change_pct / 100)
    closes = [close_10d_ago + i * (close_yesterday - close_10d_ago) / 10 for i in range(11)]
    assert len(closes) == 11
    df = pd.DataFrame({
        "close": closes,
        "high": [c * 1.02 for c in closes],
        "low": [c * 0.98 for c in closes],
    })
    return {ticker: df}


def _news_candidate(
    ticker: str = "XYZ",
    open_price: float = 10.0,
    current_price: float = 10.6,   # CHG-OPEN +6%
    today_high: float = 10.8,
    today_low: float = 9.95,        # close_in_range ~= 76, downside ~= 0.5%
    today_volume: float = 1_500_000,
) -> dict:
    return {
        "ticker": ticker,
        "open_price": open_price,
        "current_price": current_price,
        "today_high": today_high,
        "today_low": today_low,
        "today_volume": today_volume,
        "prev_close": open_price * 0.9,
        "prev_high": open_price * 0.95,
        "gap_pct": 11.0,
        "market_cap": 2_000_000_000,
        "rvol": 3.0,
        "sma_200": 8.0,
    }


def _news_config() -> dict:
    # Use production defaults. Widen ATR band so our synthetic bars don't need
    # to hit the live [3, 7] window — this test is about dedup logic, not ATR.
    return {
        "signals": {
            "ep_news_a_atr_pct_min": 0.0,
            "ep_news_a_atr_pct_max": 100.0,
            "ep_news_b_atr_pct_min": 0.0,
            "ep_news_b_atr_pct_max": 100.0,
        },
    }


class TestEPNewsDedup:
    def test_passes_both_a_and_b_yields_only_a(self):
        candidate = _news_candidate()
        daily_bars = _daily_bars_with_prev_10d("XYZ", prev_10d_change_pct=-25.0)

        entries, _ = evaluate_ep_news_strategies([candidate], daily_bars, _news_config())

        strategies = [e["ep_strategy"] for e in entries if e["ep_strategy"] in ("A", "B")]
        assert strategies == ["A"], (
            f"Expected only Strategy A when both A and B pass, got {strategies}"
        )
        assert entries[0]["stop_loss_pct"] == 7.0  # A's stop

    def test_fails_a_but_passes_b_yields_only_b(self):
        # Drive downside_from_open to ~4% so A's <3% gate fails, while keeping
        # close_in_range in B's [30, 80] window. With today_low=9.6 and
        # current_price=10.32: downside ~4%, close_in_range ~60%, chg_open ~3.2%.
        # B has no downside gate of its own (only A does), so only B survives.
        candidate = _news_candidate(today_low=9.6, current_price=10.32)
        daily_bars = _daily_bars_with_prev_10d("XYZ", prev_10d_change_pct=-12.0)

        entries, _ = evaluate_ep_news_strategies([candidate], daily_bars, _news_config())

        strategies = [e["ep_strategy"] for e in entries if e["ep_strategy"] in ("A", "B")]
        assert strategies == ["B"]
        # Both A and B use a -7% stop as of 2026-05-08 (was -10% for B before).
        assert entries[0]["stop_loss_pct"] == 7.0
