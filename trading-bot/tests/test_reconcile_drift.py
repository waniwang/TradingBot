"""Tests for core/reconcile.py — DB-vs-broker drift detection.

Added 2026-05-11 after the IB phantom-positions incident. See
memory/incident_2026_05_11_ib_phantom_positions_and_shorts.md.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.reconcile import (
    PositionDiscrepancy,
    detect_discrepancies,
    format_telegram_alert,
)


def _pos(ticker: str, shares: int, partial: int = 0):
    """Make a minimal stand-in for a DB Position row."""
    return SimpleNamespace(
        ticker=ticker, shares=shares, partial_exit_shares=partial,
    )


def _broker(symbol: str, qty: float) -> dict:
    """Match get_open_positions() output shape (qty is SIGNED)."""
    return {"symbol": symbol, "qty": qty, "side": "long" if qty > 0 else "short"}


class TestDetectDiscrepancies:
    # ----- clean case -----
    def test_no_discrepancies_when_in_sync(self):
        db = [_pos("AMD", 40), _pos("WAT", 16)]
        broker = [_broker("AMD", 40), _broker("WAT", 16)]
        result = detect_discrepancies(db, broker)
        assert result == []

    def test_no_discrepancies_when_db_empty(self):
        assert detect_discrepancies([], [_broker("ARRY", 100)]) == []

    def test_broker_extras_ignored(self):
        """Broker has tickers DB doesn't track — that's fine, not a discrepancy.
        Could be other strategies, manual holdings, etc."""
        db = [_pos("AMD", 40)]
        broker = [_broker("AMD", 40), _broker("MYSTERY", 999)]
        assert detect_discrepancies(db, broker) == []

    # ----- phantom_db: the 5/11 AMD/FTAI/KFRC/TXN/URI pattern -----
    def test_phantom_db_when_broker_has_zero(self):
        db = [_pos("AMD", 40)]
        broker = []  # broker has nothing
        result = detect_discrepancies(db, broker)
        assert len(result) == 1
        assert result[0].ticker == "AMD"
        assert result[0].kind == "phantom_db"
        assert result[0].db_open_shares == 40
        assert result[0].broker_qty == 0

    def test_multiple_phantoms_detected(self):
        db = [_pos("AMD", 40), _pos("FTAI", 59), _pos("URI", 14)]
        broker = []
        result = detect_discrepancies(db, broker)
        assert {d.ticker for d in result} == {"AMD", "FTAI", "URI"}
        assert all(d.kind == "phantom_db" for d in result)

    # ----- broker_short: the 5/11 TTMI/WCC pattern -----
    def test_broker_short_when_we_expected_long(self):
        """DB says we're long 91 TTMI, broker says we're SHORT 182.
        This is the smoking-gun signal the bot needs to flag loudly."""
        db = [_pos("TTMI", 91)]
        broker = [_broker("TTMI", -182)]
        result = detect_discrepancies(db, broker)
        assert len(result) == 1
        assert result[0].kind == "broker_short"
        assert result[0].broker_qty == -182

    # ----- broker_over: the 5/11 ARRY/EZPW/DGII duplicate pattern -----
    def test_broker_over_when_broker_has_more(self):
        """Broker shows 2x what DB knows — likely duplicate entry."""
        db = [_pos("ARRY", 549)]
        broker = [_broker("ARRY", 1098)]
        result = detect_discrepancies(db, broker)
        assert len(result) == 1
        assert result[0].kind == "broker_over"
        assert result[0].broker_qty == 1098

    # ----- broker_under: partial fill or quietly-stopped position -----
    def test_broker_under_when_broker_has_less(self):
        db = [_pos("XYZ", 100)]
        broker = [_broker("XYZ", 75)]
        result = detect_discrepancies(db, broker)
        assert len(result) == 1
        assert result[0].kind == "broker_under"

    # ----- partial exits properly accounted -----
    def test_partial_exits_reduce_db_total(self):
        """A 100-share position with 40 already partial-exited is 60 remaining."""
        db = [_pos("XYZ", 100, partial=40)]
        broker = [_broker("XYZ", 60)]
        result = detect_discrepancies(db, broker)
        assert result == []

    def test_multi_variant_ticker_sums(self):
        """Same ticker held in multiple variants (e.g. PWR-a + PWR-c) sums DB."""
        db = [_pos("PWR", 19), _pos("PWR", 19)]   # 19 + 19 = 38
        broker = [_broker("PWR", 38)]
        assert detect_discrepancies(db, broker) == []

    def test_multi_variant_with_phantom_broker(self):
        db = [_pos("PWR", 19), _pos("PWR", 19)]
        broker = []
        result = detect_discrepancies(db, broker)
        assert len(result) == 1
        assert result[0].db_open_shares == 38

    # ----- excluded inputs -----
    def test_zero_remaining_shares_ignored(self):
        """Position with shares == partial_exit_shares is effectively closed
        from an exposure standpoint — don't flag."""
        db = [_pos("XYZ", 100, partial=100)]
        broker = []
        assert detect_discrepancies(db, broker) == []


class TestFormatTelegramAlert:
    def test_empty(self):
        assert format_telegram_alert([], "Alpaca") == ""

    def test_phantom(self):
        d = PositionDiscrepancy("AMD", 40, 0, "phantom_db")
        msg = format_telegram_alert([d], "IB")
        assert "RECONCILE DRIFT (IB)" in msg
        assert "AMD" in msg
        assert "phantom row" in msg

    def test_broker_short(self):
        d = PositionDiscrepancy("TTMI", 91, -182, "broker_short")
        msg = format_telegram_alert([d], "IB")
        assert "SHORT" in msg
        assert "182" in msg

    def test_broker_over(self):
        d = PositionDiscrepancy("ARRY", 549, 1098, "broker_over")
        msg = format_telegram_alert([d], "IB")
        assert "duplicate fill" in msg
        assert "1098" in msg

    def test_multiple(self):
        ds = [
            PositionDiscrepancy("AMD", 40, 0, "phantom_db"),
            PositionDiscrepancy("TTMI", 91, -182, "broker_short"),
        ]
        msg = format_telegram_alert(ds, "IB")
        assert "2 discrepancy" in msg
        assert "AMD" in msg
        assert "TTMI" in msg
