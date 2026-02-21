"""
Tests for AlpacaClient.filter_universe_by_liquidity().
"""

from unittest.mock import MagicMock, patch
from executor.alpaca_client import AlpacaClient


def _make_client():
    """Create an AlpacaClient with stub config (ALPACA_AVAILABLE=False)."""
    return AlpacaClient({"environment": "paper", "alpaca": {"api_key": "", "secret_key": ""}})


class TestFilterUniverseByLiquidity:
    def test_filters_by_price(self):
        client = _make_client()
        client.get_snapshots = MagicMock(return_value={
            "AAPL": {"latest_price": 150.0, "daily_volume": 1_000_000},
            "PENNY": {"latest_price": 2.0, "daily_volume": 5_000_000},
            "MSFT": {"latest_price": 300.0, "daily_volume": 800_000},
        })

        result = client.filter_universe_by_liquidity(
            ["AAPL", "PENNY", "MSFT"], min_price=5.0, min_volume=500_000, batch_size=10
        )

        assert "PENNY" not in result
        assert "AAPL" in result
        assert "MSFT" in result

    def test_filters_by_volume(self):
        client = _make_client()
        client.get_snapshots = MagicMock(return_value={
            "AAPL": {"latest_price": 150.0, "daily_volume": 1_000_000},
            "THIN": {"latest_price": 50.0, "daily_volume": 100_000},
        })

        result = client.filter_universe_by_liquidity(
            ["AAPL", "THIN"], min_price=5.0, min_volume=500_000, batch_size=10
        )

        assert "AAPL" in result
        assert "THIN" not in result

    def test_sorted_by_volume_descending(self):
        client = _make_client()
        client.get_snapshots = MagicMock(return_value={
            "LOW": {"latest_price": 10.0, "daily_volume": 500_000},
            "MID": {"latest_price": 20.0, "daily_volume": 2_000_000},
            "HIGH": {"latest_price": 30.0, "daily_volume": 10_000_000},
        })

        result = client.filter_universe_by_liquidity(
            ["LOW", "MID", "HIGH"], min_price=5.0, min_volume=500_000, batch_size=10
        )

        assert result == ["HIGH", "MID", "LOW"]

    def test_fallback_on_all_snapshots_fail(self):
        client = _make_client()
        client.get_snapshots = MagicMock(side_effect=Exception("API down"))

        tickers = ["AAPL", "MSFT", "GOOG"]
        result = client.filter_universe_by_liquidity(
            tickers, min_price=5.0, min_volume=500_000, batch_size=10
        )

        # Should return unfiltered input
        assert result == tickers

    def test_empty_input(self):
        client = _make_client()
        result = client.filter_universe_by_liquidity(
            [], min_price=5.0, min_volume=500_000, batch_size=10
        )
        assert result == []

    def test_batching(self):
        client = _make_client()

        call_count = 0
        def mock_snapshots(batch):
            nonlocal call_count
            call_count += 1
            return {sym: {"latest_price": 50.0, "daily_volume": 1_000_000} for sym in batch}

        client.get_snapshots = mock_snapshots

        tickers = [f"T{i}" for i in range(10)]
        result = client.filter_universe_by_liquidity(
            tickers, min_price=5.0, min_volume=500_000, batch_size=3
        )

        # 10 tickers / batch_size 3 = 4 batches (3+3+3+1)
        assert call_count == 4
        assert len(result) == 10

    def test_progress_callback(self):
        client = _make_client()
        client.get_snapshots = MagicMock(return_value={
            "A": {"latest_price": 50.0, "daily_volume": 1_000_000},
        })

        progress_calls = []
        def on_progress(processed, total):
            progress_calls.append((processed, total))

        client.filter_universe_by_liquidity(
            ["A", "B", "C"], min_price=5.0, min_volume=500_000,
            batch_size=2, progress_cb=on_progress,
        )

        assert len(progress_calls) == 2  # 2 batches
        assert progress_calls[0] == (2, 3)
        assert progress_calls[1] == (3, 3)

    def test_partial_snapshot_failure(self):
        """Some batches fail but others succeed — should use successful results."""
        client = _make_client()

        calls = [0]
        def mock_snapshots(batch):
            calls[0] += 1
            if calls[0] == 1:
                raise Exception("batch 1 failed")
            return {sym: {"latest_price": 50.0, "daily_volume": 1_000_000} for sym in batch}

        client.get_snapshots = mock_snapshots

        tickers = ["A", "B", "C", "D"]
        result = client.filter_universe_by_liquidity(
            tickers, min_price=5.0, min_volume=500_000, batch_size=2
        )

        # First batch (A, B) fails, second batch (C, D) succeeds
        assert set(result) == {"C", "D"}
