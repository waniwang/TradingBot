"""Tests for core.execution.fetch_current_price.

Locks in the fix for the 2026-04-23 / 2026-04-24 EP day-2 confirm incident:
fetch_current_price was checking hasattr(snap, "latest_trade") on a plain
dict (returned by executor/alpaca_client.py::get_snapshots), so every call
fell through to None and produced 100% "no price data" failures (5/5 EP
earnings + 2/2 EP news on 2026-04-24).

The wrapper returns dicts with keys: latest_price, prev_close, prev_high,
daily_volume, open, today_high, today_low. fetch_current_price must read
those keys, not the raw Alpaca SDK attribute names.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core import data_cache
from core.execution import fetch_current_price


@pytest.fixture(autouse=True)
def _disable_intraday_cache(monkeypatch):
    """Force REST path for every test — the cache hit is exercised separately."""
    monkeypatch.setattr(data_cache, "get_intraday_price", lambda t: None)


def _client(snapshots: dict) -> MagicMock:
    client = MagicMock()
    client.get_snapshots.return_value = snapshots
    return client


class TestFetchCurrentPrice:
    def test_returns_latest_price_from_dict(self):
        """Regression: dict from wrapper must yield latest_price as a float."""
        client = _client({"AAPL": {"latest_price": 123.45, "prev_close": 120.0}})
        result = fetch_current_price(client, "AAPL", attempts=1, sleep_secs=0)
        assert result == 123.45

    def test_falls_back_to_day_open_when_no_last_trade(self):
        """If latest_price is 0 (no last_trade today), use daily bar open."""
        client = _client({"WXYZ": {"latest_price": 0, "open": 50.0, "today_high": 51.0}})
        result = fetch_current_price(client, "WXYZ", attempts=1, sleep_secs=0)
        assert result == 50.0

    def test_returns_none_when_ticker_absent(self):
        """Snapshot dict missing the ticker entirely → None (not raise)."""
        client = _client({})
        result = fetch_current_price(client, "MISSING", attempts=1, sleep_secs=0)
        assert result is None

    def test_returns_none_when_all_prices_zero(self):
        """No latest_price and no open → None."""
        client = _client({"DEAD": {"latest_price": 0, "open": 0}})
        result = fetch_current_price(client, "DEAD", attempts=1, sleep_secs=0)
        assert result is None

    def test_uses_intraday_cache_when_warm(self, monkeypatch):
        """If the stream cache has the ticker, skip REST entirely."""
        monkeypatch.setattr(data_cache, "get_intraday_price", lambda t: 99.99)
        client = _client({})
        client.get_snapshots.side_effect = AssertionError("REST should not be called")
        result = fetch_current_price(client, "CACHED", attempts=1, sleep_secs=0)
        assert result == 99.99

    def test_retries_on_exception_then_raises(self):
        """Exceptions retry, and the final exception propagates."""
        client = MagicMock()
        client.get_snapshots.side_effect = RuntimeError("alpaca 503")
        with pytest.raises(RuntimeError, match="alpaca 503"):
            fetch_current_price(client, "FLAKY", attempts=3, sleep_secs=0)
        assert client.get_snapshots.call_count == 3

    def test_retry_succeeds_after_transient_failure(self):
        """First call empty, second call returns price → return on success."""
        client = MagicMock()
        client.get_snapshots.side_effect = [
            {},  # empty (transient)
            {"NVDA": {"latest_price": 800.0}},
        ]
        result = fetch_current_price(client, "NVDA", attempts=3, sleep_secs=0)
        assert result == 800.0
        assert client.get_snapshots.call_count == 2
