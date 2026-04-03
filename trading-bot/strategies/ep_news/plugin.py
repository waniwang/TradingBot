"""EP News swing strategy plugin."""

from __future__ import annotations

import json as _json
import logging
from collections import defaultdict
from datetime import datetime

import pytz

from core.loader import ExitAction, ScheduleEntry

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


def _scan_job(config, client, db_engine, notify):
    """3:00 PM ET — scan + evaluate EP news candidates."""
    PLUGIN.job_scan(config, client, db_engine, notify)


def _execute_job(config, client, db_engine, notify):
    """3:50 PM ET — execute staged EP news entries."""
    PLUGIN.job_execute(config, client, db_engine, notify)


class EPNewsPlugin:
    """
    EP News Swing — long setup on news-driven gap-ups (non-earnings).

    Scans at 3:00 PM ET for stocks that gapped up on news catalysts.
    Excludes earnings-driven gaps (handled by ep_earnings).
    Strategy A uses -7% stop, Strategy B uses -10% stop.
    Executes at 3:50 PM ET near market close.
    Uses shared exit logic + max hold period (50 days).
    """

    name = "ep_news"
    display_name = "EP News Swing"
    watchlist_persist_days = 1  # single-day: expire at EOD

    schedule = [
        ScheduleEntry(
            job_id="ep_news_scan",
            cron={"hour": 15, "minute": 0, "day_of_week": "mon-fri"},
            handler=_scan_job,
        ),
        ScheduleEntry(
            job_id="ep_news_execute",
            cron={"hour": 15, "minute": 50, "day_of_week": "mon-fri"},
            handler=_execute_job,
        ),
    ]

    def __init__(self):
        self._staged_entries: list[dict] = []

    def premarket_scan(self, config, client, db_engine, notify) -> list[dict]:
        return []  # EP news scans at 3 PM, not premarket

    def evaluate_signal(self, ticker, watchlist_entry, **ctx):
        return None  # EP news executes at 3:50 PM, not via intraday signals

    def on_position_update(self, pos, current_price, daily_closes) -> ExitAction | None:
        return None  # use shared exit logic + max_hold_period in position_tracker

    def backtest_entry(self, ticker, date, row, history, bt_config):
        return None

    def backtest_exit(self, pos, date, row, history, bt_config):
        return None

    # ------------------------------------------------------------------
    # Scheduled job handlers
    # ------------------------------------------------------------------

    def job_scan(self, config, client, db_engine, notify):
        """3:00 PM ET — EOD scan for EP news gap-up candidates."""
        from main import is_trading_day
        from strategies.ep_news.scanner import scan_ep_news
        from strategies.ep_news.strategy import evaluate_ep_news_strategies
        from scanner.watchlist_manager import persist_candidates

        if not is_trading_day(client):
            logger.info("EP news scan skipped — not a trading day")
            return

        logger.info("=== EOD EP NEWS SCAN START ===")
        if notify:
            notify("EOD EP NEWS SCAN STARTED")

        today = datetime.now(ET).date()

        try:
            # Phase 1: Scanner (exclude earnings)
            candidates = scan_ep_news(config, client)
            logger.info("EOD EP news scanner: %d candidates", len(candidates))

            if not candidates:
                if notify:
                    notify("EP NEWS SCAN: 0 candidates found")
                return

            # Phase 2: Fetch daily bars
            tickers = [c["ticker"] for c in candidates]
            daily_bars = client.get_daily_bars_batch(tickers, days=300)
            logger.info("Fetched daily bars for %d/%d tickers", len(daily_bars), len(tickers))

            # Phase 3: Evaluate Strategy A + B
            entries = evaluate_ep_news_strategies(candidates, daily_bars, config)
            logger.info("News strategy evaluation: %d entries from %d candidates", len(entries), len(candidates))

            if entries:
                persist_candidates(candidates, "ep_news", "active", today, db_engine)
                self._staged_entries = entries

                for entry in entries:
                    self._persist_entry(entry, today, db_engine)

                if notify:
                    lines = [f"EP NEWS: {len(entries)} strategy entries from {len(candidates)} candidates"]
                    a_count = sum(1 for e in entries if e["ep_strategy"] == "A")
                    b_count = sum(1 for e in entries if e["ep_strategy"] == "B")
                    lines.append(f"  Strategy A: {a_count} | Strategy B: {b_count}")
                    for e in entries:
                        lines.append(
                            f"  {e['ticker']} ({e['ep_strategy']}): gap {e['gap_pct']:.1f}%, "
                            f"entry ${e['entry_price']:.2f}, stop ${e['stop_price']:.2f}"
                        )
                    notify("\n".join(lines))
            else:
                if notify:
                    lines = [f"EP NEWS: {len(candidates)} candidates, 0 passed strategy filters"]
                    for c in candidates:
                        lines.append(f"  {c['ticker']}: gap {c['gap_pct']:.1f}% (filtered out)")
                    notify("\n".join(lines))

        except Exception as e:
            logger.error("EOD EP news scan failed: %s", e)
            if notify:
                notify(f"EOD EP NEWS SCAN FAILED: {e}")

    def job_execute(self, config, client, db_engine, notify):
        """3:50 PM ET — execute entries staged by the 3:00 PM scan."""
        from main import is_trading_day, _execute_entry
        from signals.base import SignalResult
        from risk.manager import RiskManager
        from db.models import Position, get_session

        if not is_trading_day(client):
            return

        entries = self._staged_entries
        if not entries:
            logger.info("EP news execute: no entries to execute")
            return

        logger.info("=== EP NEWS EXECUTE: %d entries ===", len(entries))
        risk = RiskManager(config)

        # Group by ticker: if same stock passes both A and B, enter once
        by_ticker = defaultdict(list)
        for entry in entries:
            by_ticker[entry["ticker"]].append(entry)

        executed = 0
        for ticker, ticker_entries in by_ticker.items():
            strategies = [e["ep_strategy"] for e in ticker_entries]
            strategy_label = "+".join(sorted(set(strategies)))

            # If both A+B, use Strategy A entry (tighter stop)
            entry = next((e for e in ticker_entries if e["ep_strategy"] == "A"), ticker_entries[0])

            # Check if we already have an open position
            with get_session(db_engine) as session:
                existing = session.query(Position).filter_by(
                    ticker=ticker, is_open=True
                ).first()
                if existing:
                    logger.info("EP news: %s already has open position, skipping", ticker)
                    continue

            # Risk manager checks
            with get_session(db_engine) as session:
                open_count = session.query(Position).filter_by(is_open=True).count()

            try:
                portfolio_value = client.get_account_equity()
            except Exception:
                portfolio_value = 100_000

            daily_pnl = 0.0
            weekly_pnl = 0.0
            can_enter, reason = risk.can_enter(open_count, daily_pnl, weekly_pnl, portfolio_value)
            if not can_enter:
                logger.info("EP news: %s blocked by risk manager: %s", ticker, reason)
                if notify:
                    notify(f"EP NEWS BLOCKED: {ticker} ({strategy_label}) - {reason}")
                continue

            shares = risk.calculate_position_size(
                portfolio_value, entry["entry_price"], entry["stop_price"]
            )
            if shares <= 0:
                logger.info("EP news: %s position size = 0, skipping", ticker)
                continue

            signal = SignalResult(
                ticker=ticker,
                setup_type="ep_news",
                side="long",
                entry_price=entry["entry_price"],
                stop_price=entry["stop_price"],
                gap_pct=entry["gap_pct"],
                volume_ratio=entry.get("rvol"),
                notes=f"EP News Strategy {strategy_label} | "
                      f"CHG-OPEN={entry['chg_open_pct']:.1f}% CIR={entry['close_in_range']:.0f} "
                      f"DS={entry['downside_from_open']:.1f}% P10D={entry['prev_10d_change_pct']:.1f}% "
                      f"ATR={entry['atr_pct']:.1f}% Vol={entry.get('today_volume', 0)/1e6:.1f}M",
            )

            logger.info(
                "EP news entry: %s (%s) %d shares @ $%.2f stop $%.2f",
                ticker, strategy_label, shares, signal.entry_price, signal.stop_price,
            )
            _execute_entry(ticker, signal, shares, client, db_engine, notify)
            executed += 1

        self._staged_entries = []

        if notify:
            notify(f"EP NEWS EXECUTE: {executed}/{len(by_ticker)} tickers entered")

    def _persist_entry(self, entry: dict, scan_date, db_engine):
        """Persist an EP news strategy entry to the watchlist with metadata."""
        from db.models import Watchlist, get_session

        meta = {
            "ep_strategy": entry["ep_strategy"],
            "gap_pct": entry["gap_pct"],
            "entry_price": entry["entry_price"],
            "stop_price": entry["stop_price"],
            "stop_loss_pct": entry["stop_loss_pct"],
            "max_hold_days": entry["max_hold_days"],
            "chg_open_pct": entry["chg_open_pct"],
            "close_in_range": entry["close_in_range"],
            "downside_from_open": entry["downside_from_open"],
            "prev_10d_change_pct": entry["prev_10d_change_pct"],
            "atr_pct": entry["atr_pct"],
            "open_price": entry["open_price"],
            "prev_close": entry["prev_close"],
            "market_cap": entry.get("market_cap", 0),
            "rvol": entry.get("rvol", 0),
        }

        with get_session(db_engine) as session:
            wl = Watchlist(
                ticker=entry["ticker"],
                setup_type="ep_news",
                stage="ready",
                scan_date=scan_date,
                metadata_json=_json.dumps(meta),
                notes=f"EP News Strategy {entry['ep_strategy']}",
            )
            session.add(wl)
            session.commit()


PLUGIN = EPNewsPlugin()
