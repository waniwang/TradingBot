"""Tests for core/earnings.py — yfinance → Finnhub fallback for earnings dates.

No network calls — patches at the yfinance and requests layers.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from core.earnings import (
    EarningsLookupError,
    _fetch_finnhub,
    _fetch_yfinance,
    fetch_recent_earnings_dates,
)


# ---------------------------------------------------------------------------
# _fetch_yfinance
# ---------------------------------------------------------------------------


class TestFetchYfinance:
    def test_returns_dates(self):
        d1, d2 = date(2026, 5, 1), date(2026, 2, 1)
        idx = pd.DatetimeIndex([pd.Timestamp(d1), pd.Timestamp(d2)])
        df = pd.DataFrame({"EPS": [1.0, 0.9]}, index=idx)
        with patch("core.earnings.__import__", create=True) as _imp:
            # easier: patch yfinance module at the source
            pass

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker_cls.return_value = mock_ticker
            mock_ticker.get_earnings_dates.return_value = df

            out = _fetch_yfinance("NVDA", limit=4)

        assert set(out) == {d1, d2}

    def test_empty_dataframe(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker_cls.return_value = mock_ticker
            mock_ticker.get_earnings_dates.return_value = pd.DataFrame()

            assert _fetch_yfinance("NVDA", limit=4) == []

    def test_none_response(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker_cls.return_value = mock_ticker
            mock_ticker.get_earnings_dates.return_value = None

            assert _fetch_yfinance("NVDA", limit=4) == []

    def test_raises_on_keyerror(self):
        """Yahoo HTML schema change → KeyError(['Earnings Date']) — must propagate."""
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker_cls.return_value = mock_ticker
            mock_ticker.get_earnings_dates.side_effect = KeyError(["Earnings Date"])

            with pytest.raises(KeyError):
                _fetch_yfinance("NVDA", limit=4)


# ---------------------------------------------------------------------------
# _fetch_finnhub
# ---------------------------------------------------------------------------


class TestFetchFinnhub:
    def test_returns_dates_sorted_desc(self):
        payload = {
            "earningsCalendar": [
                {"date": "2026-02-01", "symbol": "NVDA"},
                {"date": "2026-05-01", "symbol": "NVDA"},
                {"date": "2025-11-01", "symbol": "NVDA"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None
        with patch("core.earnings.requests.get", return_value=mock_resp):
            out = _fetch_finnhub("NVDA", "fake_key", window_days=400)

        assert out == [date(2026, 5, 1), date(2026, 2, 1), date(2025, 11, 1)]

    def test_empty_calendar(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"earningsCalendar": []}
        mock_resp.raise_for_status.return_value = None
        with patch("core.earnings.requests.get", return_value=mock_resp):
            assert _fetch_finnhub("NVDA", "fake_key", window_days=400) == []

    def test_skips_unparseable_dates(self):
        payload = {"earningsCalendar": [{"date": "not-a-date", "symbol": "NVDA"}, {"date": "2026-05-01"}]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None
        with patch("core.earnings.requests.get", return_value=mock_resp):
            out = _fetch_finnhub("NVDA", "fake_key", window_days=400)
        assert out == [date(2026, 5, 1)]

    def test_raises_on_http_error(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401")
        with patch("core.earnings.requests.get", return_value=mock_resp):
            with pytest.raises(requests.HTTPError):
                _fetch_finnhub("NVDA", "fake_key", window_days=400)


# ---------------------------------------------------------------------------
# fetch_recent_earnings_dates — fallback orchestration
# ---------------------------------------------------------------------------


class TestFetchRecentEarningsDates:
    def test_yfinance_success_skips_finnhub(self):
        d = date(2026, 5, 1)
        with patch("core.earnings._fetch_yfinance", return_value=[d]) as mock_yf, \
             patch("core.earnings._fetch_finnhub") as mock_fh:
            out = fetch_recent_earnings_dates("NVDA", finnhub_key="key")

        assert out == [d]
        mock_yf.assert_called_once()
        mock_fh.assert_not_called()

    def test_yfinance_fail_no_key_raises(self):
        with patch("core.earnings._fetch_yfinance", side_effect=KeyError(["Earnings Date"])):
            with pytest.raises(EarningsLookupError, match="no Finnhub key"):
                fetch_recent_earnings_dates("NVDA", finnhub_key=None)

    def test_yfinance_fail_finnhub_succeeds(self):
        """The 2026-05-13 incident scenario: yfinance KeyError, Finnhub picks up."""
        d = date(2026, 5, 1)
        with patch("core.earnings._fetch_yfinance", side_effect=KeyError(["Earnings Date"])), \
             patch("core.earnings._fetch_finnhub", return_value=[d]):
            out = fetch_recent_earnings_dates("NVDA", finnhub_key="key")

        assert out == [d]

    def test_both_fail_raises(self):
        with patch("core.earnings._fetch_yfinance", side_effect=KeyError(["Earnings Date"])), \
             patch("core.earnings._fetch_finnhub", side_effect=requests.HTTPError("500")):
            with pytest.raises(EarningsLookupError, match="both yfinance and Finnhub"):
                fetch_recent_earnings_dates("NVDA", finnhub_key="key")

    def test_empty_list_is_success_not_fallback(self):
        """yfinance returning [] (ticker has no upcoming/recent earnings) is a
        successful response — we must NOT fall through to Finnhub. Otherwise a
        ticker that legitimately has no earnings would always cost us a Finnhub
        call."""
        with patch("core.earnings._fetch_yfinance", return_value=[]) as mock_yf, \
             patch("core.earnings._fetch_finnhub") as mock_fh:
            out = fetch_recent_earnings_dates("NVDA", finnhub_key="key")

        assert out == []
        mock_yf.assert_called_once()
        mock_fh.assert_not_called()
