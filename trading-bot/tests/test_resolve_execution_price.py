"""Tests for core.execution.resolve_execution_price.

Locks in the behavior added after the 2026-04-22 MCRI incident: scanner price
goes stale between 3:00 PM scan and 3:50+ execute, so we fetch a live mid and
pick entry/stop with guardrails on price bump and bid-ask spread.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.execution import resolve_execution_price


def _mk_client(bid: float, ask: float):
    client = MagicMock()
    client.get_realtime_quote.return_value = {
        "ticker": "FAKE", "bid": bid, "ask": ask,
        "last_price": (bid + ask) / 2,
    }
    return client


def _cfg(
    refresh: bool = True,
    max_bump: float = 3.0,
    max_spread: float = 3.0,
) -> dict:
    return {
        "signals": {
            "ep_execute_refresh_price": refresh,
            "ep_execute_max_price_bump_pct": max_bump,
            "ep_execute_max_spread_pct": max_spread,
        }
    }


class TestResolveExecutionPrice:
    def test_refresh_disabled_uses_scan_entry(self):
        # Killswitch: even with a huge live bump, return scan values
        client = _mk_client(bid=999.0, ask=1000.0)
        result = resolve_execution_price(
            "FAKE", scan_entry=100.0, stop_pct=7.0,
            side="long", client=client, config=_cfg(refresh=False),
        )
        assert result is not None
        entry, stop, label = result
        assert entry == 100.0
        assert stop == 93.0  # 100 * 0.93
        assert "disabled" in label

    def test_mid_below_scan_returns_scan_entry(self):
        # Live price came back to us — stay with the scanner entry (cheap fill
        # is fine; strategy consistency wins)
        client = _mk_client(bid=98.0, ask=99.0)  # mid = 98.5, below 100
        result = resolve_execution_price(
            "FAKE", scan_entry=100.0, stop_pct=7.0,
            side="long", client=client, config=_cfg(),
        )
        assert result is not None
        entry, stop, _ = result
        assert entry == 100.0
        assert stop == 93.0

    def test_mid_within_bump_uses_refreshed(self):
        # Stock rallied 1.5% — within 3% cap → use live mid, recompute stop
        client = _mk_client(bid=101.4, ask=101.6)  # mid = 101.50
        result = resolve_execution_price(
            "FAKE", scan_entry=100.0, stop_pct=7.0,
            side="long", client=client, config=_cfg(),
        )
        assert result is not None
        entry, stop, label = result
        assert entry == 101.50
        # Stop is refreshed_entry * (1 - stop_pct/100), rounded to cents.
        # Allow cent-level tolerance to avoid float-rounding brittleness.
        assert abs(stop - 101.50 * 0.93) < 0.01
        assert "refreshed" in label
        assert "scan=$100.00" in label

    def test_mid_above_bump_cap_skips(self):
        # Stock rallied 4% — over 3% cap → None (retry next minute)
        client = _mk_client(bid=103.9, ask=104.1)  # mid = 104, +4%
        result = resolve_execution_price(
            "FAKE", scan_entry=100.0, stop_pct=7.0,
            side="long", client=client, config=_cfg(),
        )
        assert result is None

    def test_wide_spread_skips(self):
        # MCRI-like quote: bid 97.74 / ask 129.09 → 28% spread → skip
        client = _mk_client(bid=97.74, ask=129.09)
        result = resolve_execution_price(
            "MCRI", scan_entry=111.78, stop_pct=7.0,
            side="long", client=client, config=_cfg(),
        )
        assert result is None

    def test_zero_quote_skips(self):
        client = _mk_client(bid=0.0, ask=0.0)
        result = resolve_execution_price(
            "FAKE", scan_entry=100.0, stop_pct=7.0,
            side="long", client=client, config=_cfg(),
        )
        assert result is None

    def test_exception_propagates(self):
        client = MagicMock()
        client.get_realtime_quote.side_effect = RuntimeError("Alpaca down")
        # Trade-path rule: don't swallow broker errors, let _track_job fire an alert
        with pytest.raises(RuntimeError, match="Alpaca down"):
            resolve_execution_price(
                "FAKE", scan_entry=100.0, stop_pct=7.0,
                side="long", client=client, config=_cfg(),
            )

    def test_non_long_side_falls_back_to_scan(self):
        # Short side not yet validated — use scan defensively
        client = _mk_client(bid=101.0, ask=101.2)
        result = resolve_execution_price(
            "FAKE", scan_entry=100.0, stop_pct=7.0,
            side="short", client=client, config=_cfg(),
        )
        assert result is not None
        entry, stop, label = result
        assert entry == 100.0
        assert stop == 93.0
        assert "non-long" in label

    def test_custom_bump_cap_honored(self):
        # Widen the bump cap to 5% — the same 4% move now fills
        client = _mk_client(bid=103.9, ask=104.1)
        result = resolve_execution_price(
            "FAKE", scan_entry=100.0, stop_pct=7.0,
            side="long", client=client, config=_cfg(max_bump=5.0),
        )
        assert result is not None
        entry, _, _ = result
        assert entry == 104.0

    def test_custom_spread_cap_honored(self):
        # Tighten the spread cap to 0.5% — what used to pass now skips
        client = _mk_client(bid=100.5, ask=101.5)  # 1% spread
        result = resolve_execution_price(
            "FAKE", scan_entry=100.0, stop_pct=7.0,
            side="long", client=client, config=_cfg(max_spread=0.5),
        )
        assert result is None
