"""DB-vs-broker reconciliation helpers.

These run inside the every-5-min reconcile_positions jobs in main.py and
main_ib.py to surface discrepancies between what the bot's DB believes is
open and what the broker actually shows. Before 2026-05-11 the reconcile
flow only watched for stop-order fills; it never compared share counts.
That gap let phantom DB rows (AMD/FTAI/KFRC/TXN/URI on IB) accrue paper
P&L of ~$6.8K for weeks while the broker had zero shares. See
memory/incident_2026_05_11_ib_phantom_positions_and_shorts.md.

Output is a list of structured discrepancies; the caller decides whether
to log, notify, or auto-act. The helper is pure (no DB writes, no broker
calls) and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PositionDiscrepancy:
    """One DB-vs-broker mismatch."""

    ticker: str
    db_open_shares: int           # sum across all DB-open variants for this ticker
    broker_qty: float             # signed: positive long, negative short, 0 flat
    kind: str                      # see categories below

    def summary(self) -> str:
        return (
            f"{self.ticker}: db_open={self.db_open_shares} "
            f"broker={self.broker_qty:.0f} [{self.kind}]"
        )


def detect_discrepancies(
    open_positions: Iterable,         # iterable of DB Position rows (any object with .ticker, .shares, .partial_exit_shares)
    broker_positions: Iterable[dict], # output of *Client.get_open_positions() — qty is SIGNED
) -> list[PositionDiscrepancy]:
    """Compare DB open positions to broker positions.

    Categories emitted:
      - "phantom_db":   DB says open with shares > 0, broker shows 0
                        for this ticker. Position never existed at broker
                        OR was sold/stopped externally and DB missed it.
      - "broker_short": Broker shows NEGATIVE qty (we're short) but DB
                        thinks we're long. Almost certainly a bug.
      - "broker_under": Broker has FEWER long shares than DB sum. Could
                        be a partial broker close, a phantom variant, or
                        a stop fired without the bot noticing.
      - "broker_over":  Broker has MORE long shares than DB knows. Could
                        be a duplicate fill (e.g. retried entry that
                        succeeded twice — the ARRY/EZPW/DGII 2x pattern
                        from 2026-05-11).

    Tickers in `broker_positions` with no matching DB-open rows are NOT
    flagged as "broker_over" here because they may belong to other
    strategies or be manually-held names. Only tickers we DO have
    DB-open rows for get compared.

    Args:
        open_positions: DB Position rows where is_open=True
        broker_positions: list of dicts from get_open_positions(), each
            with keys "symbol" and "qty" (signed)

    Returns:
        List of PositionDiscrepancy entries. Empty list means clean.
    """
    # Group DB-open rows by ticker, summing effective remaining shares.
    db_shares: dict[str, int] = {}
    for pos in open_positions:
        remaining = int(getattr(pos, "shares", 0)) - int(getattr(pos, "partial_exit_shares", 0))
        if remaining <= 0:
            continue
        db_shares[pos.ticker] = db_shares.get(pos.ticker, 0) + remaining

    broker_by_ticker: dict[str, float] = {
        bp["symbol"]: float(bp.get("qty", 0)) for bp in broker_positions
    }

    discrepancies: list[PositionDiscrepancy] = []

    for ticker, db_total in db_shares.items():
        broker_qty = broker_by_ticker.get(ticker, 0.0)

        if broker_qty == 0:
            discrepancies.append(PositionDiscrepancy(
                ticker=ticker, db_open_shares=db_total,
                broker_qty=broker_qty, kind="phantom_db",
            ))
            continue

        if broker_qty < 0:
            discrepancies.append(PositionDiscrepancy(
                ticker=ticker, db_open_shares=db_total,
                broker_qty=broker_qty, kind="broker_short",
            ))
            continue

        # Both non-zero positive — compare sizes
        if broker_qty < db_total:
            discrepancies.append(PositionDiscrepancy(
                ticker=ticker, db_open_shares=db_total,
                broker_qty=broker_qty, kind="broker_under",
            ))
        elif broker_qty > db_total:
            discrepancies.append(PositionDiscrepancy(
                ticker=ticker, db_open_shares=db_total,
                broker_qty=broker_qty, kind="broker_over",
            ))
        # broker_qty == db_total → match, no discrepancy

    return discrepancies


def format_telegram_alert(discrepancies: list[PositionDiscrepancy], bot_label: str) -> str:
    """Format discrepancies for a Telegram notify(). Caller is responsible
    for deduplication / alert frequency."""
    if not discrepancies:
        return ""
    lines = [f"RECONCILE DRIFT ({bot_label}): {len(discrepancies)} discrepancy(s)"]
    for d in discrepancies:
        if d.kind == "phantom_db":
            lines.append(
                f"  ⚠ {d.ticker}: DB says open {d.db_open_shares}sh, "
                f"broker shows 0 — phantom row"
            )
        elif d.kind == "broker_short":
            lines.append(
                f"  ⚠ {d.ticker}: DB long {d.db_open_shares}sh, "
                f"broker is SHORT {abs(d.broker_qty):.0f}sh — investigate"
            )
        elif d.kind == "broker_under":
            lines.append(
                f"  ⚠ {d.ticker}: DB {d.db_open_shares}sh, "
                f"broker only {d.broker_qty:.0f}sh — partial/missing"
            )
        elif d.kind == "broker_over":
            lines.append(
                f"  ⚠ {d.ticker}: DB {d.db_open_shares}sh, "
                f"broker has {d.broker_qty:.0f}sh — possible duplicate fill"
            )
    return "\n".join(lines)
