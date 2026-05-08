"""
Tests for `scripts/collect_missed_trades.py` — focused on the
RiskSkip → CSV row mapping. The collector unifies two data sources
(Watchlist[bot-failure] + RiskSkip) into a single tracker CSV; the
`block_reason → (label, category)` dispatch is the part most likely
to drift when new risk gates land, so it gets explicit coverage here.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


# Load the collector as a module — it's a script, not under a package,
# so a normal `from scripts.collect_missed_trades import ...` doesn't work.
_COLLECTOR_PATH = Path(__file__).resolve().parent.parent / "scripts" / "collect_missed_trades.py"


@pytest.fixture(scope="module")
def collector():
    spec = importlib.util.spec_from_file_location("collect_missed_trades", _COLLECTOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["collect_missed_trades"] = mod
    spec.loader.exec_module(mod)
    return mod


def _risk_skip(
    block_reason: str,
    notes: str = "",
    ticker: str = "FOO",
    setup_type: str = "ep_earnings",
    ep_strategy: str = "C",
    intended_entry: float = 100.0,
    intended_stop: float = 93.0,
):
    """Build a SimpleNamespace shaped like a RiskSkip ORM row. The
    collector reads attributes by name, so any duck-typed object works
    — keeps these tests independent of the SQLAlchemy session machinery.
    """
    return SimpleNamespace(
        ticker=ticker,
        setup_type=setup_type,
        ep_strategy=ep_strategy,
        block_reason=block_reason,
        intended_entry=intended_entry,
        intended_stop=intended_stop,
        notes=notes,
        occurred_date=date(2026, 5, 7),
        occurred_at=datetime(2026, 5, 7, 19, 37, 0),
    )


def test_max_positions_reason_and_category(collector):
    row = _risk_skip(block_reason="max_positions", notes="open=20/cap=20")
    out = collector._risk_skip_to_csv(row)

    assert out["category"] == "max-positions"
    assert out["reason"] == "Position cap full at execute time (open=20/cap=20)"
    assert out["would_confirm"] == "yes"
    assert out["intended_entry"] == "100.00"
    assert out["intended_stop"] == "93.00"
    assert out["incident_commit"] == ""


def test_insufficient_bp_reason_and_category(collector):
    """New 2026-05-07 path — Alpaca buying-power exhaustion. Pre-flight
    check writes a RiskSkip row; collector translates it to a friendly
    label so the Google Sheet reads naturally."""
    row = _risk_skip(
        block_reason="insufficient_bp",
        notes="cost=$4,172 > BP=$533",
        ticker="NBIX",
        intended_entry=149.00,
        intended_stop=138.57,
    )
    out = collector._risk_skip_to_csv(row)

    assert out["category"] == "insufficient-bp"
    assert out["reason"] == "Buying power exhausted at execute time (cost=$4,172 > BP=$533)"
    assert out["ticker"] == "NBIX"
    assert out["strategy"] == "ep_earnings_c"


def test_stage_filter_drop_is_categorized_as_bot_bug(collector):
    """The 2026-05-07 stage-filter bug (commit 39d6e70) silently dropped
    FLEX/SSRM/HL after their first cancel. Backfilled rows for that
    incident use block_reason='stage_filter_drop' and must appear under
    category='bot-bug' — they were missed because of a bug, not an
    operator-tunable risk gate."""
    row = _risk_skip(
        block_reason="stage_filter_drop",
        notes="cancelled at 15:38, would have filled at 15:43 (1m bar low=$132.07) — backfilled",
        ticker="FLEX",
    )
    out = collector._risk_skip_to_csv(row)

    assert out["category"] == "bot-bug"
    assert "Order cancelled by timeout" in out["reason"]
    assert "would have filled at 15:43" in out["reason"]


def test_unknown_block_reason_falls_through_to_risk_skip(collector):
    """A future code path could write a new block_reason without updating
    _BLOCK_REASON_LABELS. The row must still surface — better to ship a
    generic label than drop the data entirely."""
    row = _risk_skip(block_reason="margin_limit", notes="ratio=2.1x")
    out = collector._risk_skip_to_csv(row)

    assert out["category"] == "risk-skip"
    assert "margin_limit" in out["reason"]
    assert "ratio=2.1x" in out["reason"]


def test_empty_notes_omits_parenthetical(collector):
    """If a RiskSkip has no notes, the reason should be the bare label
    without an empty `()` suffix."""
    row = _risk_skip(block_reason="max_positions", notes="")
    out = collector._risk_skip_to_csv(row)

    assert out["reason"] == "Position cap full at execute time"
    assert "(" not in out["reason"]


def test_strategy_label_includes_variant(collector):
    """The CSV `strategy` column is `setup_type_<variant.lower()>` so
    operators can filter on A/B/C in the sheet. The label maps come from
    STRATEGY_LABELS in the collector — exercised here as a smoke check."""
    out_a = collector._risk_skip_to_csv(_risk_skip(
        block_reason="max_positions", setup_type="ep_news", ep_strategy="A",
    ))
    out_c = collector._risk_skip_to_csv(_risk_skip(
        block_reason="max_positions", setup_type="ep_earnings", ep_strategy="C",
    ))

    assert out_a["strategy"] == "ep_news_a"
    assert out_c["strategy"] == "ep_earnings_c"
