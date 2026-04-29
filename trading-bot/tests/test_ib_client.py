"""
Tests for IBClient bug fixes uncovered by the 2026-04-29 first-execute attempt:

1. _account_value subscription leak — calling accountSummaryAsync() on every
   lookup blew through IBKR's quota (Error 322) and caused subsequent calls
   to return 0.0, which tripped ZeroDivisionError in risk.manager.

2. get_realtime_quote was missing entirely — core/execution.py calls it
   during EP execute price refresh, but IBClient never implemented it.

These tests stub out the underlying ib_async IB instance so we can exercise
the caching + dispatch logic without a live connection.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# Skip the whole module if ib_async isn't installed (some dev envs).
ib_async = pytest.importorskip("ib_async")


from executor.ib_client import IBClient


def _make_client(connected: bool = True) -> IBClient:
    """Build an IBClient with stubbed IB instance + event loop."""
    cfg = {"environment": "paper", "ibkr": {"host": "127.0.0.1", "port": 4002, "client_id": 99}}
    client = IBClient(cfg)
    client._ib = MagicMock()
    client._connected = connected

    # Replace _run with a synchronous executor so we don't need a real loop.
    # _run normally runs an awaitable on the background asyncio loop; for the
    # tests below the relevant methods either return MagicMock results
    # (accountSummaryAsync) or actual lists, so we just call the coro and
    # return whatever it would return.
    def _fake_run(coro, timeout=10):
        # If the caller passed a coroutine that wraps a mock return value,
        # just close it and return the mock's return_value. Tests below
        # configure the mock return value directly.
        try:
            coro.close()
        except Exception:
            pass
        # The mocked accountSummaryAsync returns a list directly, not a coro,
        # so when _run is called with that list, return it as-is.
        return coro if not hasattr(coro, "close") else None
    # We won't use _fake_run for accountSummaryAsync — instead the test patches
    # the method to return its list directly and we override _run to just
    # call the coroutine factory.

    return client


class FakeAccountValue:
    def __init__(self, tag, value, account="DUP178005", currency="USD"):
        self.tag = tag
        self.value = value
        self.account = account
        self.currency = currency


# ---------------------------------------------------------------------------
# _account_value caching
# ---------------------------------------------------------------------------


class TestAccountSummaryCache:
    def _build(self, summary_rows):
        """Build a client whose _run() returns ``summary_rows`` for accountSummaryAsync."""
        client = IBClient(
            {"environment": "paper",
             "ibkr": {"host": "127.0.0.1", "port": 4002, "client_id": 99}}
        )
        client._ib = MagicMock()
        client._connected = True

        # Stub accountSummaryAsync as a callable returning a no-op coroutine.
        # The actual return value is delivered via our _run override.
        async def _fake_summary():
            return summary_rows
        client._ib.accountSummaryAsync = _fake_summary

        # Override _run so we don't need a running event loop. It just runs
        # the coroutine to completion in an inline event loop.
        import asyncio
        def _sync_run(coro, timeout=10):
            return asyncio.new_event_loop().run_until_complete(coro)
        client._run = _sync_run
        return client

    def test_first_call_fetches_and_caches(self):
        """_get_account_summary_rows fetches once, then reuses the cache."""
        rows = [FakeAccountValue("NetLiquidation", "100000.00")]
        client = self._build(rows)

        # Spy on the async method to count calls.
        call_count = {"n": 0}
        original = client._ib.accountSummaryAsync
        async def _counted():
            call_count["n"] += 1
            return await original()
        client._ib.accountSummaryAsync = _counted

        # First call — fetches.
        v1 = client.get_portfolio_value()
        assert v1 == 100000.0
        assert call_count["n"] == 1, "first call should hit IB"

        # Second call — uses cache.
        v2 = client.get_portfolio_value()
        assert v2 == 100000.0
        assert call_count["n"] == 1, "second call should NOT re-fetch"

    def test_cache_expires_after_ttl(self):
        """After TTL, the next call refetches."""
        rows = [FakeAccountValue("NetLiquidation", "50000.00")]
        client = self._build(rows)
        call_count = {"n": 0}
        async def _counted():
            call_count["n"] += 1
            return rows
        client._ib.accountSummaryAsync = _counted

        client.get_portfolio_value()
        assert call_count["n"] == 1

        # Force the cache to look stale.
        client._account_summary_fetched_at -= client._ACCOUNT_SUMMARY_TTL + 1

        client.get_portfolio_value()
        assert call_count["n"] == 2, "stale cache should refetch"

    def test_multiple_tags_share_one_fetch(self):
        """get_portfolio_value + get_cash within TTL should issue ONE fetch.
        This is the regression guard for the original Error 322 leak — risk.can_enter
        looks up several tags during a single execute pass."""
        rows = [
            FakeAccountValue("NetLiquidation", "100000.00"),
            FakeAccountValue("AvailableFunds", "95000.00"),
        ]
        client = self._build(rows)
        call_count = {"n": 0}
        async def _counted():
            call_count["n"] += 1
            return rows
        client._ib.accountSummaryAsync = _counted

        nl = client.get_portfolio_value()
        cash = client.get_cash()

        assert nl == 100000.0
        assert cash == 95000.0
        assert call_count["n"] == 1, (
            f"only one accountSummaryAsync call expected, got {call_count['n']}"
        )

    def test_disconnected_raises_not_zero(self):
        """Per CLAUDE.md trade-path rule: errors must propagate. Returning 0
        silently was the precise pattern that caused ZeroDivisionError in
        risk.manager.check_daily_loss."""
        client = self._build([])
        client._connected = False

        with pytest.raises(RuntimeError, match="while disconnected"):
            client.get_portfolio_value()

    def test_missing_tag_raises(self):
        """Missing tag is a real bug, not a 0.0 fallback."""
        rows = [FakeAccountValue("Cushion", "0.92")]
        client = self._build(rows)
        with pytest.raises(RuntimeError, match="not in AccountSummary"):
            client.get_portfolio_value()  # NetLiquidation absent

    def test_non_numeric_value_raises(self):
        rows = [FakeAccountValue("NetLiquidation", "not-a-number")]
        client = self._build(rows)
        with pytest.raises(RuntimeError, match="non-numeric"):
            client.get_portfolio_value()

    def test_reconnect_invalidates_cache(self):
        """connect() must clear the cached summary so a stale value from the
        previous IB session doesn't leak through."""
        rows = [FakeAccountValue("NetLiquidation", "100000.00")]
        client = self._build(rows)
        client.get_portfolio_value()
        assert client._account_summary_rows  # populated

        # Patch IB() and _start_loop so connect() doesn't actually try to
        # open a socket. We only care about the cache-clearing side effect.
        with patch("executor.ib_client.IB", return_value=MagicMock()), \
             patch.object(client, "_start_loop"), \
             patch.object(client, "_run", side_effect=lambda *a, **kw: None):
            try:
                client.connect()
            except Exception:
                # connect() will fail at the post-connect step (no real Gateway),
                # but the cache invalidation runs at the top of the method.
                pass

        assert client._account_summary_rows == [], (
            "connect() must invalidate the cache from the previous session"
        )


# ---------------------------------------------------------------------------
# get_realtime_quote
# ---------------------------------------------------------------------------


class TestGetRealtimeQuote:
    def _build_client(self):
        client = IBClient(
            {"environment": "paper",
             "ibkr": {"host": "127.0.0.1", "port": 4002, "client_id": 99}}
        )
        client._ib = MagicMock()
        client._connected = True
        # Stub _qualify (async) to return a fake contract immediately.
        async def _fake_qualify(symbol):
            return SimpleNamespace(symbol=symbol)
        client._qualify = _fake_qualify

        import asyncio
        def _sync_run(coro, timeout=10):
            return asyncio.new_event_loop().run_until_complete(coro)
        client._run = _sync_run
        return client

    def test_returns_bid_ask_last_shape(self):
        client = self._build_client()
        # IB ticker: bid/ask/last appear immediately so the polling loop exits.
        fake_ticker = SimpleNamespace(
            bid=21.90, ask=22.00, last=21.95,
            close=21.95, marketPrice=lambda: 21.95,
        )
        client._ib.reqMktData = MagicMock(return_value=fake_ticker)

        out = client.get_realtime_quote("AAPL")
        assert out["ticker"] == "AAPL"
        assert out["bid"] == 21.90
        assert out["ask"] == 22.00
        assert out["last_price"] == 21.95

    def test_nan_inputs_become_zero(self):
        """IB ticker fields are float-or-NaN. NaN must become 0 (not propagate
        as NaN), so resolve_execution_price's `bid <= 0` check trips correctly
        and we skip-and-retry rather than placing a $NaN order."""
        client = self._build_client()
        # NaN sentinel
        nan = float("nan")
        fake_ticker = SimpleNamespace(
            bid=nan, ask=nan, last=nan, close=21.0,
            marketPrice=lambda: 21.0,
        )
        client._ib.reqMktData = MagicMock(return_value=fake_ticker)

        out = client.get_realtime_quote("AAPL")
        assert out["bid"] == 0.0
        assert out["ask"] == 0.0
        # last_price falls back to close when last is NaN
        assert out["last_price"] == 21.0

    def test_disconnected_raises(self):
        """Trade-path: don't silently return 0.0 — propagate so _track_job
        fires JOB FAILED."""
        client = self._build_client()
        client._connected = False
        with pytest.raises(RuntimeError, match="while disconnected"):
            client.get_realtime_quote("AAPL")
