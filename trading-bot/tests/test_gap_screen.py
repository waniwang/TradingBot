"""
Throttling and rate-limit-retry tests for scanner/gap_screen.py.

The broad-universe gap pre-screen calls yfinance against ~5K tickers each EOD
scan. Without throttling, Yahoo rate-limits us and the scanner returns 0
candidates — the silent failure mode that motivated this test file. These tests
lock in: (1) batches are paced with sleeps, (2) YFRateLimitError triggers
exponential backoff retry, (3) bad batches don't poison the rest of the scan.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from scanner import gap_screen


def _make_df(symbols: list[str], gaps: dict[str, float]) -> pd.DataFrame:
    """Build a yfinance-style multi-index DataFrame with prev_close=100 and
    today's open driven by the per-symbol gap percentage."""
    idx = pd.DatetimeIndex(["2026-04-08", "2026-04-09"])
    cols = pd.MultiIndex.from_product([symbols, ["Open", "High", "Low", "Close", "Volume"]])
    rows = []
    for d in idx:
        row = []
        for sym in symbols:
            if d == idx[-1]:
                gap_pct = gaps.get(sym, 0.0)
                open_p = 100 * (1 + gap_pct / 100)
                row.extend([open_p, open_p * 1.02, open_p * 0.98, open_p * 1.01, 1_000_000])
            else:
                row.extend([99.0, 101.0, 98.0, 100.0, 1_000_000])
        rows.append(row)
    return pd.DataFrame(rows, index=idx, columns=cols)


class TestThrottling:
    def test_inter_batch_sleep_is_called(self):
        """Batches are paced with sleep_fn between them."""
        sleep_fn = MagicMock()
        df = _make_df(["A", "B"], {"A": 12.0})
        with patch("scanner.gap_screen.yf.download", return_value=df):
            gap_screen.scan_broad_gaps(
                universe=["A", "B", "C", "D"],
                batch_size=2,
                inter_batch_delay_s=0.5,
                sleep_fn=sleep_fn,
            )
        # 4 tickers / 2 per batch = 2 batches → exactly 1 inter-batch sleep
        sleep_fn.assert_called_once_with(0.5)

    def test_no_sleep_after_last_batch(self):
        """We don't waste time sleeping after the final batch."""
        sleep_fn = MagicMock()
        df = _make_df(["A", "B"], {})
        with patch("scanner.gap_screen.yf.download", return_value=df):
            gap_screen.scan_broad_gaps(
                universe=["A", "B"],
                batch_size=2,
                inter_batch_delay_s=0.5,
                sleep_fn=sleep_fn,
            )
        sleep_fn.assert_not_called()


class TestRateLimitRetry:
    def test_yfratelimiterror_triggers_backoff(self):
        """A rate-limit error retries with exponential backoff, then succeeds."""
        # Build a fake YFRateLimitError class (yfinance has its own; we duck-type by name)
        class YFRateLimitError(Exception):
            pass

        df = _make_df(["A"], {"A": 10.0})
        # First two calls raise rate-limit, third succeeds
        download = MagicMock(side_effect=[YFRateLimitError("rate"), YFRateLimitError("rate"), df])
        sleep_fn = MagicMock()

        with patch("scanner.gap_screen.yf.download", download):
            results = gap_screen.scan_broad_gaps(
                universe=["A"],
                batch_size=1,
                inter_batch_delay_s=0.0,
                max_retries=3,
                retry_backoff_base_s=4.0,
                sleep_fn=sleep_fn,
            )

        assert download.call_count == 3
        # Backoff: 4s, 16s (then success on 3rd attempt = no third sleep)
        backoff_sleeps = [c.args[0] for c in sleep_fn.call_args_list if c.args[0] in (4.0, 16.0)]
        assert backoff_sleeps == [4.0, 16.0]
        assert len(results) == 1 and results[0]["symbol"] == "A"

    def test_exhausted_retries_returns_empty_for_batch(self):
        """If all retries fail, the batch is dropped but the scan continues."""
        class YFRateLimitError(Exception):
            pass

        df = _make_df(["B"], {"B": 12.0})
        # First batch (A) always rate-limited; second batch (B) succeeds
        download = MagicMock(side_effect=[YFRateLimitError("rate")] * 3 + [df])
        sleep_fn = MagicMock()

        with patch("scanner.gap_screen.yf.download", download):
            results = gap_screen.scan_broad_gaps(
                universe=["A", "B"],
                batch_size=1,
                inter_batch_delay_s=0.0,
                max_retries=3,
                retry_backoff_base_s=1.0,
                sleep_fn=sleep_fn,
            )

        # 3 attempts on A + 1 on B = 4 calls; results contain only B
        assert download.call_count == 4
        assert {r["symbol"] for r in results} == {"B"}

    def test_non_rate_limit_error_does_not_retry(self):
        """A generic exception (not rate-limit) fails the batch immediately — no retry."""
        download = MagicMock(side_effect=ValueError("bad request"))
        sleep_fn = MagicMock()

        with patch("scanner.gap_screen.yf.download", download):
            results = gap_screen.scan_broad_gaps(
                universe=["A"],
                batch_size=1,
                inter_batch_delay_s=0.0,
                max_retries=3,
                sleep_fn=sleep_fn,
            )

        assert download.call_count == 1
        assert results == []


class TestFiltering:
    def test_gap_threshold(self):
        df = _make_df(["A", "B", "C"], {"A": 12.0, "B": 5.0, "C": 9.0})
        with patch("scanner.gap_screen.yf.download", return_value=df):
            results = gap_screen.scan_broad_gaps(
                universe=["A", "B", "C"], min_gap_pct=8.0,
                batch_size=10, sleep_fn=MagicMock(),
            )
        symbols = {r["symbol"] for r in results}
        assert symbols == {"A", "C"}

    def test_min_price_filter(self):
        df = _make_df(["A"], {"A": 20.0})
        with patch("scanner.gap_screen.yf.download", return_value=df):
            # prev_close in fixture is 100 → above $3 → passes
            results = gap_screen.scan_broad_gaps(
                universe=["A"], min_price=3.0,
                batch_size=10, sleep_fn=MagicMock(),
            )
        assert len(results) == 1

        with patch("scanner.gap_screen.yf.download", return_value=df):
            # min_price=200 → prev_close=100 → filtered out
            results = gap_screen.scan_broad_gaps(
                universe=["A"], min_price=200.0,
                batch_size=10, sleep_fn=MagicMock(),
            )
        assert results == []

    def test_results_sorted_by_gap_descending(self):
        df = _make_df(["A", "B", "C"], {"A": 10.0, "B": 30.0, "C": 20.0})
        with patch("scanner.gap_screen.yf.download", return_value=df):
            results = gap_screen.scan_broad_gaps(
                universe=["A", "B", "C"], min_gap_pct=8.0,
                batch_size=10, sleep_fn=MagicMock(),
            )
        assert [r["symbol"] for r in results] == ["B", "C", "A"]
