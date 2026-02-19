"""
Unit tests for scanner modules (gapper, momentum_rank, consolidation).

Uses mocked AlpacaClient — no network calls.
"""

import pytest
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from scanner.gapper import get_premarket_gappers
from scanner.momentum_rank import rank_by_momentum, compute_rs_score
from scanner.consolidation import (
    analyze_consolidation,
    scan_breakout_candidates,
    compute_atr,
    detect_higher_lows,
    detect_atr_contraction,
    check_near_ma,
)
from scanner.parabolic import scan_parabolic_candidates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config():
    return {
        "signals": {
            "ep_min_gap_pct": 10.0,
            "breakout_consolidation_days_min": 10,
            "breakout_consolidation_days_max": 40,
        },
    }


def _make_daily_df(n: int = 130, start_price: float = 50.0, drift: float = 0.1) -> pd.DataFrame:
    """Create a synthetic daily OHLCV DataFrame."""
    rows = []
    price = start_price
    for i in range(n):
        price += drift
        rows.append({
            "date": pd.Timestamp("2025-06-01") + pd.Timedelta(days=i),
            "open": round(price - 0.5, 2),
            "high": round(price + 1.0, 2),
            "low": round(price - 1.0, 2),
            "close": round(price, 2),
            "volume": 1_000_000 + i * 1000,
        })
    return pd.DataFrame(rows)


def _make_consolidating_df(n: int = 60) -> pd.DataFrame:
    """
    Create a DataFrame that simulates consolidation:
    - Higher lows
    - ATR contracting (range gets tighter over time)
    - Price near 20d MA
    """
    rows = []
    price = 100.0
    for i in range(n):
        # Slow upward drift (higher lows)
        price += 0.05
        # ATR contracts: range narrows over time
        range_factor = max(0.3, 2.0 - i * 0.03)
        rows.append({
            "date": pd.Timestamp("2025-06-01") + pd.Timedelta(days=i),
            "open": round(price - 0.1, 2),
            "high": round(price + range_factor, 2),
            "low": round(price - range_factor, 2),
            "close": round(price, 2),
            "volume": max(100_000, 1_000_000 - i * 15_000),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Gapper tests
# ---------------------------------------------------------------------------

class TestGetPremarketGappers:
    def test_basic_gapper(self):
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "NVDA", "percent_change": 15.0, "price": 120.0},
            {"symbol": "AAPL", "percent_change": 12.0, "price": 180.0},
            {"symbol": "PENNY", "percent_change": 50.0, "price": 2.0},  # below $5
        ]
        client.get_snapshots.return_value = {
            "NVDA": {"prev_close": 100.0, "latest_price": 115.0, "daily_volume": 500_000},
            "AAPL": {"prev_close": 160.0, "latest_price": 179.2, "daily_volume": 300_000},
        }

        config = _make_config()
        result = get_premarket_gappers(config, client)

        assert len(result) == 2
        assert result[0]["ticker"] == "NVDA"
        assert result[0]["gap_pct"] == 15.0
        assert result[0]["setup_type"] == "episodic_pivot"
        assert result[1]["ticker"] == "AAPL"

    def test_filters_low_volume(self):
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "LOW", "percent_change": 20.0, "price": 50.0},
        ]
        client.get_snapshots.return_value = {
            "LOW": {"prev_close": 40.0, "latest_price": 50.0, "daily_volume": 5_000},
        }

        result = get_premarket_gappers(_make_config(), client)
        assert len(result) == 0

    def test_filters_below_min_gap(self):
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "SMALL", "percent_change": 5.0, "price": 50.0},
        ]
        client.get_snapshots.return_value = {
            "SMALL": {"prev_close": 48.0, "latest_price": 50.0, "daily_volume": 500_000},
        }

        result = get_premarket_gappers(_make_config(), client)
        assert len(result) == 0  # 4.17% gap < 10% min

    def test_empty_movers(self):
        client = MagicMock()
        client.get_market_movers_gainers.return_value = []

        result = get_premarket_gappers(_make_config(), client)
        assert result == []

    def test_filters_invalid_symbols(self):
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "GOOD", "percent_change": 20.0, "price": 50.0},
            {"symbol": "BAD123", "percent_change": 20.0, "price": 50.0},  # non-alpha
            {"symbol": "TOOLONG", "percent_change": 20.0, "price": 50.0},  # >5 chars
        ]
        client.get_snapshots.return_value = {
            "GOOD": {"prev_close": 40.0, "latest_price": 50.0, "daily_volume": 500_000},
        }

        result = get_premarket_gappers(_make_config(), client)
        assert len(result) == 1
        assert result[0]["ticker"] == "GOOD"

    def test_sorted_by_gap_descending(self):
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "AAA", "percent_change": 10.0, "price": 50.0},
            {"symbol": "BBB", "percent_change": 30.0, "price": 70.0},
            {"symbol": "CCC", "percent_change": 20.0, "price": 60.0},
        ]
        client.get_snapshots.return_value = {
            "AAA": {"prev_close": 40.0, "latest_price": 50.0, "daily_volume": 200_000},
            "BBB": {"prev_close": 50.0, "latest_price": 70.0, "daily_volume": 200_000},
            "CCC": {"prev_close": 45.0, "latest_price": 60.0, "daily_volume": 200_000},
        }

        result = get_premarket_gappers(_make_config(), client)
        gaps = [r["gap_pct"] for r in result]
        assert gaps == sorted(gaps, reverse=True)


# ---------------------------------------------------------------------------
# Momentum rank tests
# ---------------------------------------------------------------------------

class TestComputeRsScore:
    def test_basic_uptrend(self):
        df = _make_daily_df(130, start_price=50.0, drift=0.5)
        scores = compute_rs_score(df)
        assert "rs_1m" in scores
        assert "rs_composite" in scores
        assert scores["rs_1m"] > 0
        assert scores["rs_composite"] > 0

    def test_too_short(self):
        df = _make_daily_df(10)
        scores = compute_rs_score(df)
        assert scores == {}

    def test_empty(self):
        df = pd.DataFrame()
        scores = compute_rs_score(df)
        assert scores == {}


class TestRankByMomentum:
    def test_ranks_correctly(self):
        df_strong = _make_daily_df(130, start_price=50.0, drift=1.0)
        df_weak = _make_daily_df(130, start_price=50.0, drift=0.1)
        df_mid = _make_daily_df(130, start_price=50.0, drift=0.5)

        client = MagicMock()
        client.get_daily_bars_batch.return_value = {
            "STRONG": df_strong,
            "WEAK": df_weak,
            "MID": df_mid,
        }

        result = rank_by_momentum(
            ["STRONG", "WEAK", "MID"], _make_config(), client, top_n=3
        )

        assert len(result) == 3
        assert result[0]["ticker"] == "STRONG"
        assert result[-1]["ticker"] == "WEAK"
        assert all(r["setup_type"] == "breakout" for r in result)

    def test_top_n_limit(self):
        bars = {}
        for i in range(10):
            sym = f"T{i}"
            bars[sym] = _make_daily_df(130, start_price=50.0, drift=0.1 * (i + 1))

        client = MagicMock()
        client.get_daily_bars_batch.return_value = bars

        result = rank_by_momentum(
            list(bars.keys()), _make_config(), client, top_n=3
        )
        assert len(result) == 3

    def test_skips_missing_symbols(self):
        client = MagicMock()
        client.get_daily_bars_batch.return_value = {
            "EXISTS": _make_daily_df(130),
            # "MISSING" not in results
        }

        result = rank_by_momentum(
            ["EXISTS", "MISSING"], _make_config(), client, top_n=10
        )
        assert len(result) == 1
        assert result[0]["ticker"] == "EXISTS"


# ---------------------------------------------------------------------------
# Consolidation tests
# ---------------------------------------------------------------------------

class TestAnalyzeConsolidation:
    def test_insufficient_data(self):
        df = _make_daily_df(10)
        result = analyze_consolidation("SHORT", _make_config(), df)
        assert result["qualifies"] is False
        assert result["reason"] == "insufficient_data"

    def test_consolidating_stock(self):
        df = _make_consolidating_df(60)
        result = analyze_consolidation("CONSOL", _make_config(), df)
        # Should at least produce a valid result with all fields
        assert "qualifies" in result
        assert "atr_contracting" in result
        assert "higher_lows" in result
        assert "near_20d_ma" in result
        assert result["setup_type"] == "breakout"

    def test_empty_df(self):
        df = pd.DataFrame()
        result = analyze_consolidation("EMPTY", _make_config(), df)
        assert result["qualifies"] is False
        assert result["reason"] == "insufficient_data"


class TestScanBreakoutCandidates:
    def test_passes_dataframes_to_analyze(self):
        df = _make_consolidating_df(60)
        client = MagicMock()
        client.get_daily_bars_batch.return_value = {
            "AAA": df,
            "BBB": _make_daily_df(10),  # too short
        }

        result = scan_breakout_candidates(["AAA", "BBB"], _make_config(), client)
        # BBB should be filtered out (insufficient data)
        for r in result:
            assert r["ticker"] != "BBB"
        client.get_daily_bars_batch.assert_called_once_with(["AAA", "BBB"], days=90)

    def test_empty_tickers(self):
        client = MagicMock()
        client.get_daily_bars_batch.return_value = {}

        result = scan_breakout_candidates([], _make_config(), client)
        assert result == []


class TestComputeAtr:
    def test_basic(self):
        df = _make_daily_df(30)
        atr = compute_atr(df, period=14)
        assert len(atr) == 30
        assert atr.iloc[-1] > 0


class TestDetectHigherLows:
    def test_uptrend(self):
        closes = pd.Series([10.0, 10.5, 11.0, 11.5, 12.0])
        assert detect_higher_lows(closes, 5) == True

    def test_downtrend(self):
        closes = pd.Series([12.0, 11.5, 11.0, 10.5, 10.0])
        assert detect_higher_lows(closes, 5) == False


class TestCheckNearMa:
    def test_near_ma(self):
        df = _make_daily_df(30, start_price=100.0, drift=0.0)
        # All closes ≈ 100, so price is at MA
        assert check_near_ma(df, ma_period=20) == True

    def test_far_from_ma(self):
        # Create DF where last close is far from MA
        rows = []
        for i in range(30):
            price = 100.0 if i < 29 else 120.0  # big jump on last day
            rows.append({
                "date": pd.Timestamp("2025-06-01") + pd.Timedelta(days=i),
                "open": price, "high": price + 1, "low": price - 1,
                "close": price, "volume": 1_000_000,
            })
        df = pd.DataFrame(rows)
        assert check_near_ma(df, ma_period=20, tolerance_pct=3.0) == False


# ---------------------------------------------------------------------------
# Parabolic scanner tests
# ---------------------------------------------------------------------------

def _make_parabolic_config():
    return {
        "signals": {
            "parabolic_min_gain_pct": 50.0,
            "parabolic_min_days": 3,
        },
    }


def _make_parabolic_df(gain_pct: float = 60.0, days: int = 8) -> pd.DataFrame:
    """Create a DataFrame with a parabolic move over the last 3 days."""
    rows = []
    base_price = 20.0
    for i in range(days):
        if i >= days - 3:
            # Parabolic move: scale up linearly to reach gain_pct
            progress = (i - (days - 3) + 1) / 3.0
            price = base_price * (1 + gain_pct / 100 * progress)
        else:
            price = base_price + i * 0.1
        rows.append({
            "date": pd.Timestamp("2025-06-01") + pd.Timedelta(days=i),
            "open": round(price - 0.2, 2),
            "high": round(price + 0.5, 2),
            "low": round(price - 0.3, 2),
            "close": round(price, 2),
            "volume": 1_000_000,
        })
    return pd.DataFrame(rows)


class TestScanParabolicCandidates:
    def test_qualifies_parabolic(self):
        df = _make_parabolic_df(gain_pct=60.0)
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "MEME", "percent_change": 30.0, "price": 30.0},
        ]
        client.get_daily_bars_batch.return_value = {"MEME": df}

        result = scan_parabolic_candidates(_make_parabolic_config(), client)
        assert len(result) == 1
        assert result[0]["ticker"] == "MEME"
        assert result[0]["setup_type"] == "parabolic_short"
        assert result[0]["gain_pct"] >= 50.0

    def test_filters_insufficient_gain(self):
        df = _make_parabolic_df(gain_pct=20.0)  # below 50% threshold
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "SLOW", "percent_change": 10.0, "price": 25.0},
        ]
        client.get_daily_bars_batch.return_value = {"SLOW": df}

        result = scan_parabolic_candidates(_make_parabolic_config(), client)
        assert len(result) == 0

    def test_empty_movers(self):
        client = MagicMock()
        client.get_market_movers_gainers.return_value = []

        result = scan_parabolic_candidates(_make_parabolic_config(), client)
        assert result == []

    def test_filters_penny_stocks(self):
        df = _make_parabolic_df(gain_pct=80.0)
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "CHEAP", "percent_change": 80.0, "price": 2.0},  # below $5
        ]
        client.get_daily_bars_batch.return_value = {"CHEAP": df}

        result = scan_parabolic_candidates(_make_parabolic_config(), client)
        assert len(result) == 0

    def test_sorted_by_gain_descending(self):
        df1 = _make_parabolic_df(gain_pct=60.0)
        df2 = _make_parabolic_df(gain_pct=80.0)
        client = MagicMock()
        client.get_market_movers_gainers.return_value = [
            {"symbol": "AAA", "percent_change": 30.0, "price": 30.0},
            {"symbol": "BBB", "percent_change": 50.0, "price": 40.0},
        ]
        client.get_daily_bars_batch.return_value = {"AAA": df1, "BBB": df2}

        result = scan_parabolic_candidates(_make_parabolic_config(), client)
        if len(result) >= 2:
            assert result[0]["gain_pct"] >= result[1]["gain_pct"]


# ---------------------------------------------------------------------------
# RS score with partial data
# ---------------------------------------------------------------------------

class TestComputeRsScorePartialData:
    def test_only_30_rows(self):
        """With only 30 rows, 1m is available but 3m and 6m should be 0.0."""
        df = _make_daily_df(30, start_price=50.0, drift=0.5)
        scores = compute_rs_score(df)
        assert scores != {}
        assert scores["rs_1m"] > 0
        # 3m and 6m should fall back to 0.0 (insufficient data)
        assert scores["rs_3m"] == 0.0
        assert scores["rs_6m"] == 0.0
        # Composite should equal rs_1m (only available period)
        assert scores["rs_composite"] == scores["rs_1m"]


# ---------------------------------------------------------------------------
# Consolidation with missing config key
# ---------------------------------------------------------------------------

class TestConsolidationMissingConfig:
    def test_missing_consolidation_days_max(self):
        """Config without breakout_consolidation_days_max should use default 40."""
        config = {"signals": {}}
        df = _make_consolidating_df(60)
        result = analyze_consolidation("TEST", config, df)
        assert result["consolidation_days"] == 40
