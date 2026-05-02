"""
Alpaca broker client — replaces moomoo_client.py.

Uses alpaca-py SDK. No gateway process required — pure REST + WebSocket.

Paper trading:  environment: paper  → TradingClient(paper=True)
Live trading:   environment: live   → TradingClient(paper=False)

Alpaca dashboard: https://alpaca.markets
API docs:         https://docs.alpaca.markets
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

logger = logging.getLogger(__name__)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        LimitOrderRequest,
        MarketOrderRequest,
        StopOrderRequest,
        StopLossRequest,
        ReplaceOrderRequest,
        GetOrdersRequest,
        GetCalendarRequest,
        GetAssetsRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus, AssetStatus, AssetClass, OrderClass
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.historical.screener import ScreenerClient
    from alpaca.data.requests import (
        StockBarsRequest,
        StockLatestQuoteRequest,
        StockLatestBarRequest,
        StockSnapshotRequest,
        MarketMoversRequest,
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.live import StockDataStream
    from alpaca.data.enums import DataFeed
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    logger.warning("alpaca-py not installed — AlpacaClient running in stub mode")


class AlpacaClient:
    """
    Thin wrapper around alpaca-py.

    Exposes the same interface as the old MoomooClient so the rest of the
    codebase (risk manager, position tracker, main.py) needs no changes.

    Usage:
        client = AlpacaClient(config)
        client.connect()
        value  = client.get_portfolio_value()
        oid    = client.place_limit_order("AAPL", "buy", 100, 175.50)
        client.cancel_order(oid)
        client.disconnect()
    """

    def __init__(self, config: dict, notify=None, *, stub_ok: bool = False):
        # Guard against the "silently running in stub mode" failure mode — a bot
        # that looks alive, logs orders, but never sends anything to a real broker.
        # Tests/backtests that genuinely need the stub pass stub_ok=True.
        if not ALPACA_AVAILABLE and not stub_ok:
            raise RuntimeError(
                "alpaca-py is not installed — AlpacaClient cannot run. "
                "Install it (`pip install alpaca-py>=0.43.0`) or pass stub_ok=True "
                "if you explicitly want stub responses for testing."
            )

        self.env: str = config.get("environment", "paper")
        self._notify = notify or (lambda msg: None)
        alpaca_cfg = config.get("alpaca", {})

        self._api_key: str = (
            os.environ.get("ALPACA_API_KEY") or alpaca_cfg.get("api_key", "")
        )
        self._secret_key: str = (
            os.environ.get("ALPACA_SECRET_KEY") or alpaca_cfg.get("secret_key", "")
        )
        self._paper: bool = self.env != "live"

        self._trade: TradingClient | None = None
        self._data: StockHistoricalDataClient | None = None
        self._screener: "ScreenerClient | None" = None
        self._stream: StockDataStream | None = None
        self._stream_thread: "threading.Thread | None" = None
        self._subscribed_tickers: list[str] = []
        self._watchdog_stop: "threading.Event | None" = None

        self._stream_callbacks: list[Callable] = []
        self._callback_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bar-cb")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self):
        if not ALPACA_AVAILABLE:
            logger.info("[stub] AlpacaClient.connect()")
            return

        self._trade = TradingClient(
            api_key=self._api_key,
            secret_key=self._secret_key,
            paper=self._paper,
        )
        self._data = StockHistoricalDataClient(
            api_key=self._api_key,
            secret_key=self._secret_key,
        )
        self._screener = ScreenerClient(
            api_key=self._api_key,
            secret_key=self._secret_key,
        )
        mode = "PAPER" if self._paper else "LIVE"
        logger.info("Connected to Alpaca (%s)", mode)

    def disconnect(self):
        if self._watchdog_stop:
            self._watchdog_stop.set()
        if self._stream:
            self._stream.stop()
        logger.info("Disconnected from Alpaca")

    def is_connected(self) -> bool:
        return self._trade is not None or not ALPACA_AVAILABLE

    # ------------------------------------------------------------------
    # Market calendar
    # ------------------------------------------------------------------

    def get_market_clock(self) -> dict:
        """
        Return current market status from Alpaca's clock endpoint.

        Returns a dict with:
            is_open       (bool)   — True if market is currently open
            next_open     (datetime) — next market open time (UTC-aware)
            next_close    (datetime) — next market close time (UTC-aware)

        Alpaca's clock already accounts for US market holidays and early closes.
        """
        if not ALPACA_AVAILABLE:
            now = datetime.now(timezone.utc)
            return {"is_open": False, "next_open": now, "next_close": now}

        clock = self._trade.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": clock.next_open,
            "next_close": clock.next_close,
        }

    def is_market_open(self) -> bool:
        """True if the US equity market is currently open (holiday-aware)."""
        if not ALPACA_AVAILABLE:
            return False
        try:
            return self._trade.get_clock().is_open
        except Exception as e:
            logger.warning("Could not fetch market clock: %s", e)
            return False

    def is_trading_day(self) -> bool:
        """
        True if today is a trading day (not a weekend or US holiday).

        Uses Alpaca's calendar API — accurate for early closes, ad-hoc holidays, etc.
        Falls back to weekday check if the API call fails.
        """
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")

        if not ALPACA_AVAILABLE:
            return datetime.now(_ET).weekday() < 5

        try:
            today = datetime.now(_ET).date()
            req = GetCalendarRequest(start=today, end=today)
            cal = self._trade.get_calendar(req)
            return len(cal) > 0  # empty list → not a trading day
        except Exception as e:
            logger.warning("Market calendar check failed, falling back to weekday: %s", e)
            return datetime.now(_ET).weekday() < 5

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_portfolio_value(self) -> float:
        if not ALPACA_AVAILABLE:
            logger.debug("[stub] get_portfolio_value() → 100000.0")
            return 100_000.0

        account = self._trade.get_account()
        return float(account.portfolio_value)

    def get_cash(self) -> float:
        if not ALPACA_AVAILABLE:
            return 100_000.0
        return float(self._trade.get_account().cash)

    def get_open_positions(self) -> list[dict]:
        """Return list of current open positions from Alpaca."""
        if not ALPACA_AVAILABLE:
            return []

        positions = self._trade.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": p.side.value,
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price or 0),
                "unrealized_pl": float(p.unrealized_pl or 0),
                "market_value": float(p.market_value or 0),
            }
            for p in positions
        ]

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_limit_order(
        self, ticker: str, side: str, shares: int, price: float
    ) -> str:
        """
        Place a day limit order.

        Args:
            ticker: e.g. "AAPL"
            side: "buy" | "sell" | "sell_short" | "buy_to_cover"
            shares: number of shares (positive integer)
            price: limit price

        Returns:
            Alpaca order ID string
        """
        if not ALPACA_AVAILABLE:
            stub_id = f"STUB-{ticker}-{int(time.time())}"
            logger.info("[stub] place_limit_order %s %s %d @ %.2f → %s",
                        side, ticker, shares, price, stub_id)
            return stub_id

        order_side = self._resolve_side(side)
        req = LimitOrderRequest(
            symbol=ticker,
            qty=shares,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            limit_price=round(price, 2),
        )
        order = self._trade.submit_order(req)
        order_id = str(order.id)
        logger.info("Limit order placed: %s %s %d @ %.2f → id=%s",
                    side, ticker, shares, price, order_id)
        return order_id

    def place_oto_order(
        self,
        ticker: str,
        side: str,
        shares: int,
        entry_price: float,
        stop_price: float,
    ) -> str:
        """
        Submit a single atomic OTO (One-Triggers-Other) order:
        - Parent: limit entry at ``entry_price``, DAY TIF
        - Child:  stop-loss at ``stop_price`` (sent automatically if/when
                  the parent fills, becomes GTC)

        Why OTO instead of separate limit + stop calls:
        Alpaca's wash-trade detector rejects a naked buy/sell submitted
        against a ticker that already has an opposite-side stop/market
        order live (error 40310000, "potential wash trade detected. use
        complex orders"). Bundling entry + stop in a single OTO signals
        intent and bypasses the rule. See incident 2026-05-01 for the
        first batch of wash-trade rejections that drove this change.

        Returns the parent order id. The child stop's id is not known
        until the parent fills — discover it later via
        ``get_child_stop_order_id(parent_id)``.

        ``side`` is the entry side ("buy" / "sell_short"); the stop
        child's side is inferred (sell-stop for longs, buy-to-cover for
        shorts) automatically by Alpaca.
        """
        if not ALPACA_AVAILABLE:
            stub_id = f"STUB-OTO-{ticker}-{int(time.time())}"
            logger.info("[stub] place_oto_order %s %s %d @ %.2f stop %.2f → %s",
                        side, ticker, shares, entry_price, stop_price, stub_id)
            return stub_id

        order_side = self._resolve_side(side)
        req = LimitOrderRequest(
            symbol=ticker,
            qty=shares,
            side=order_side,
            # Bracket/OTO support DAY or GTC; DAY matches the strategy's
            # "fill at close or skip" semantics — child stop becomes GTC
            # automatically once the parent fills.
            time_in_force=TimeInForce.DAY,
            limit_price=round(entry_price, 2),
            order_class=OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        order = self._trade.submit_order(req)
        order_id = str(order.id)
        logger.info(
            "OTO order placed: %s %s %d entry=%.2f stop=%.2f → parent_id=%s",
            side, ticker, shares, entry_price, stop_price, order_id,
        )
        return order_id

    def get_child_stop_order_id(self, parent_id: str) -> str | None:
        """
        Look up the child stop order belonging to an OTO parent. Returns
        the stop-leg's broker order id, or None if the parent hasn't yet
        triggered the child (e.g. parent still working/cancelled before
        fill).

        Implementation: query the parent with the SDK's nested helper —
        ``GetOrderByIdRequest(nested=True)`` — and pick the leg whose
        order_type is ``stop``. There is normally exactly one stop leg
        in an OTO; if there are zero, the parent never triggered the
        child (return None); if there are more than one (defensive), we
        return the first stop-typed leg.
        """
        if not ALPACA_AVAILABLE:
            return f"STUB-STOP-CHILD-{parent_id}"

        from alpaca.trading.requests import GetOrderByIdRequest
        try:
            order = self._trade.get_order_by_id(
                parent_id, GetOrderByIdRequest(nested=True),
            )
        except Exception as e:
            logger.warning("get_child_stop_order_id(%s): query failed: %s", parent_id, e)
            return None

        legs = getattr(order, "legs", None) or []
        for leg in legs:
            leg_type = getattr(leg, "order_type", None) or getattr(leg, "type", None)
            leg_type_str = leg_type.value if hasattr(leg_type, "value") else str(leg_type or "")
            if "stop" in leg_type_str.lower():
                return str(leg.id)
        return None

    def place_stop_order(
        self, ticker: str, side: str, shares: int, stop_price: float
    ) -> str:
        """
        Place a GTC stop-market order (converts to market order when stop_price is hit).

        side should be "sell" for a long stop, "buy_to_cover" for a short stop.
        """
        if not ALPACA_AVAILABLE:
            stub_id = f"STUB-STOP-{ticker}-{int(time.time())}"
            logger.info("[stub] place_stop_order %s %s %d @ %.2f → %s",
                        side, ticker, shares, stop_price, stub_id)
            return stub_id

        order_side = self._resolve_side(side)
        req = StopOrderRequest(
            symbol=ticker,
            qty=shares,
            side=order_side,
            time_in_force=TimeInForce.GTC,
            stop_price=round(stop_price, 2),
        )
        order = self._trade.submit_order(req)
        order_id = str(order.id)
        logger.info("Stop order placed: %s %s %d @ %.2f → id=%s",
                    side, ticker, shares, stop_price, order_id)
        return order_id

    def modify_stop_order(self, order_id: str, new_stop_price: float):
        """Update an existing stop order's price via replace."""
        if not ALPACA_AVAILABLE:
            logger.info("[stub] modify_stop_order %s → %.2f", order_id, new_stop_price)
            return

        req = ReplaceOrderRequest(stop_price=round(new_stop_price, 2))
        self._trade.replace_order_by_id(order_id, req)
        logger.info("Stop order %s updated → %.2f", order_id, new_stop_price)

    def cancel_order(self, order_id: str):
        """Cancel an open order."""
        if not ALPACA_AVAILABLE:
            logger.info("[stub] cancel_order %s", order_id)
            return

        self._trade.cancel_order_by_id(order_id)
        logger.info("Cancelled order %s", order_id)

    def close_position(self, ticker: str, shares: int, side: str):
        """
        Close a position with a market order.

        Args:
            ticker: stock symbol
            shares: number of shares to close
            side: "long" or "short" (determines order direction)
        """
        if not ALPACA_AVAILABLE:
            stub_id = f"STUB-CLOSE-{ticker}-{int(time.time())}"
            logger.info("[stub] close_position %s %d shares (%s)", ticker, shares, side)
            return stub_id

        order_side = OrderSide.SELL if side == "long" else OrderSide.BUY
        req = MarketOrderRequest(
            symbol=ticker,
            qty=shares,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        order = self._trade.submit_order(req)
        order_id = str(order.id)
        logger.info("Market close %s %d shares → id=%s", ticker, shares, order_id)
        return order_id

    def get_order_status(self, order_id: str) -> dict:
        """Query status of a single order."""
        if not ALPACA_AVAILABLE:
            return {
                "order_id": order_id,
                "status": "filled",
                "filled_qty": 100,
                "filled_avg_price": 0.0,
            }

        order = self._trade.get_order_by_id(order_id)
        return {
            "order_id": str(order.id),
            "status": order.status.value,
            "filled_qty": int(order.filled_qty or 0),
            "filled_avg_price": float(order.filled_avg_price or 0),
        }

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def subscribe_quotes(self, tickers: list[str], callback: Callable | None = None):
        """
        Subscribe to real-time 1m bars via Alpaca WebSocket stream.

        The stream runs in a background thread. A watchdog thread monitors the
        connection and automatically reconnects if the stream thread dies.

        Callback signature: callback(data: dict)
        """
        if not ALPACA_AVAILABLE:
            logger.info("[stub] subscribe_quotes %s", tickers)
            return

        import threading

        if callback:
            self._stream_callbacks.append(callback)

        self._subscribed_tickers = tickers
        self._start_stream(tickers)

        # Start watchdog (stop previous one if any)
        if self._watchdog_stop:
            self._watchdog_stop.set()
        self._watchdog_stop = threading.Event()
        t = threading.Thread(target=self._stream_watchdog, daemon=True)
        t.start()

    def _start_stream(self, tickers: list[str]):
        """Create and launch the WebSocket stream in a daemon thread."""
        import threading

        # Stop old stream to prevent thread/memory leak on reconnect
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass

        self._stream = StockDataStream(
            api_key=self._api_key,
            secret_key=self._secret_key,
            feed=DataFeed.IEX,
        )

        async def _bar_handler(bar):
            data = {
                "ticker": bar.symbol,
                "time": bar.timestamp,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume),
            }
            loop = asyncio.get_event_loop()
            for cb in self._stream_callbacks:
                loop.run_in_executor(self._callback_executor, cb, data)

        self._stream.subscribe_bars(_bar_handler, *tickers)
        self._stream_thread = threading.Thread(target=self._stream.run, daemon=True)
        self._stream_thread.start()
        logger.info("Stream started for %s (IEX feed)", tickers)

    def _stream_watchdog(self):
        """Monitor stream thread every 30s; reconnect if it has died."""
        import time

        while not self._watchdog_stop.is_set():
            self._watchdog_stop.wait(timeout=30)
            if self._watchdog_stop.is_set():
                break

            if self._stream_thread and not self._stream_thread.is_alive():
                logger.warning("Stream thread died — attempting reconnect...")
                self._notify(
                    "🔴 WebSocket stream disconnected.\nAttempting to reconnect — signals paused."
                )
                try:
                    self._start_stream(self._subscribed_tickers)
                    logger.info("Stream reconnected for %s", self._subscribed_tickers)
                    self._notify("✅ WebSocket stream reconnected. Signals resumed.")
                except Exception as e:
                    logger.error("Stream reconnect failed: %s", e)
                    self._notify(f"⚠️ Stream reconnect failed: {e}\nWill retry in 30s.")

    def unsubscribe_quotes(self, tickers: list[str]):
        """Unsubscribe from real-time data for a list of tickers."""
        if not ALPACA_AVAILABLE:
            logger.info("[stub] unsubscribe_quotes %s", tickers)
            return

        if self._stream:
            self._stream.unsubscribe_bars(*tickers)
            logger.info("Unsubscribed bars for %s", tickers)

    def get_realtime_quote(self, ticker: str) -> dict:
        """Get the latest quote for a single ticker (REST, not streaming)."""
        if not ALPACA_AVAILABLE:
            return {"ticker": ticker, "last_price": 0.0, "volume": 0}

        req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = self._data.get_stock_latest_quote(req)
        q = quotes[ticker]
        # Use mid-price as proxy for last price
        mid = (float(q.ask_price) + float(q.bid_price)) / 2 if q.ask_price and q.bid_price else 0.0
        return {
            "ticker": ticker,
            "last_price": mid,
            "ask": float(q.ask_price or 0),
            "bid": float(q.bid_price or 0),
            "volume": 0,  # quote doesn't include volume; use get_candles_1m for that
        }

    def get_latest_bar(self, ticker: str) -> dict:
        """Get the most recent 1-minute bar for a ticker."""
        if not ALPACA_AVAILABLE:
            return {"ticker": ticker, "last_price": 0.0, "volume": 0}

        req = StockLatestBarRequest(symbol_or_symbols=ticker, feed=DataFeed.IEX)
        bars = self._data.get_stock_latest_bar(req)
        b = bars[ticker]
        return {
            "ticker": ticker,
            "last_price": float(b.close),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "volume": int(b.volume),
        }

    def get_candles_1m(self, ticker: str, count: int = 30) -> list[dict]:
        """
        Get the last `count` 1-minute bars for a ticker via Alpaca REST.

        Uses IEX feed (free). For SIP feed upgrade your Alpaca plan.
        """
        if not ALPACA_AVAILABLE:
            return []

        end = datetime.now(timezone.utc)
        # Request extra bars to account for market hours gaps
        start = end - timedelta(minutes=count * 3)

        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=DataFeed.IEX,
            limit=count,
        )
        bars = self._data.get_stock_bars(req)
        bars_data = bars.data if hasattr(bars, 'data') else bars
        ticker_bars = bars_data.get(ticker, [])

        result = []
        for b in ticker_bars[-count:]:
            result.append({
                "time": b.timestamp,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": int(b.volume),
            })
        return result

    def get_candles_1m_range(
        self, ticker: str, start: datetime, end: datetime
    ) -> list[dict]:
        """Fetch 1-minute bars for an explicit time window (UTC datetimes).

        Used by `verify_day.py`'s unfilled-limit postmortem to check whether
        the stock ever touched a limit price during the 60-second fill-wait
        window — the historical counterpart of `get_candles_1m`, which only
        looks back from now.
        """
        if not ALPACA_AVAILABLE:
            return []

        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        bars = self._data.get_stock_bars(req)
        bars_data = bars.data if hasattr(bars, 'data') else bars
        ticker_bars = bars_data.get(ticker, [])

        return [
            {
                "time": b.timestamp,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": int(b.volume),
            }
            for b in ticker_bars
        ]

    def get_daily_bars(self, ticker: str, days: int = 130) -> list[dict]:
        """Get daily OHLCV bars for a ticker (used for MA / volume avg calculations)."""
        if not ALPACA_AVAILABLE:
            return []

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 10)

        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
            limit=days,
        )
        bars = self._data.get_stock_bars(req)
        bars_data = bars.data if hasattr(bars, 'data') else bars
        ticker_bars = bars_data.get(ticker, [])

        return [
            {
                "time": b.timestamp,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": int(b.volume),
            }
            for b in ticker_bars[-days:]
        ]

    # ------------------------------------------------------------------
    # Scanner helpers (used by scanner/ modules)
    # ------------------------------------------------------------------

    def get_market_movers_gainers(self, top: int = 50) -> list[dict]:
        """Return top gaining stocks from Alpaca's screener."""
        if not ALPACA_AVAILABLE:
            return []

        try:
            req = MarketMoversRequest(top=top)
            movers = self._screener.get_market_movers(req)
            results = []
            for m in movers.gainers:
                results.append({
                    "symbol": m.symbol,
                    "percent_change": float(m.percent_change),
                    "price": float(m.price),
                })
            return results
        except Exception as e:
            logger.error("get_market_movers_gainers failed: %s", e)
            return []

    def get_snapshots(self, tickers: list[str], batch_size: int = 1000) -> dict[str, dict]:
        """
        Snapshot data for multiple tickers, batched to avoid overly large requests.

        Returns {symbol: {prev_close, prev_high, latest_price, daily_volume, open, today_high, today_low}}.
        Raises on API failure (per no-silent-swallow policy).
        """
        if not ALPACA_AVAILABLE or not tickers:
            return {}

        result: dict[str, dict] = {}
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            req = StockSnapshotRequest(symbol_or_symbols=batch, feed=DataFeed.IEX)
            snapshots = self._data.get_stock_snapshot(req)
            for sym, snap in snapshots.items():
                if snap is None:
                    continue
                prev_bar = snap.previous_daily_bar
                day_bar = snap.daily_bar
                last_trade = snap.latest_trade
                result[sym] = {
                    "prev_close": float(prev_bar.close) if prev_bar else 0,
                    "prev_high": float(prev_bar.high) if prev_bar else 0,
                    "latest_price": float(last_trade.price) if last_trade else 0,
                    "daily_volume": int(day_bar.volume) if day_bar else 0,
                    "open": float(day_bar.open) if day_bar else 0,
                    "today_high": float(day_bar.high) if day_bar else 0,
                    "today_low": float(day_bar.low) if day_bar else 0,
                }
        return result

    def get_tradable_universe(self) -> list[str]:
        """
        Get ALL tradable US equity symbols from Alpaca.

        Filters: active, tradable, NYSE/NASDAQ, alpha-only symbols <= 5 chars.
        Returns ~8K+ symbols (no limit).
        """
        if not ALPACA_AVAILABLE:
            return []

        try:
            req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
            assets = self._trade.get_all_assets(req)
            valid_exchanges = {"NYSE", "NASDAQ"}
            tickers = []
            for a in assets:
                if (
                    a.tradable
                    and a.exchange in valid_exchanges
                    and len(a.symbol) <= 5
                    and a.symbol.isalpha()
                ):
                    tickers.append(a.symbol)
            logger.info("Tradable universe: %d tickers", len(tickers))
            return tickers
        except Exception as e:
            logger.error("get_tradable_universe failed: %s", e)
            return []

    def filter_universe_by_liquidity(
        self,
        tickers: list[str],
        min_price: float = 5.0,
        min_volume: int = 100_000,
        batch_size: int = 200,
        progress_cb=None,
    ) -> list[str]:
        """
        Filter tickers by price and volume using Alpaca snapshots.

        Args:
            tickers: full ticker list from get_tradable_universe()
            min_price: minimum latest price
            min_volume: minimum daily volume
            batch_size: tickers per snapshot API call
            progress_cb: optional callback(processed, total) for progress reporting

        Returns:
            Qualifying tickers sorted by volume descending.
            Falls back to unfiltered input if all snapshot calls fail.
        """
        if not tickers:
            return []

        qualified = []  # list of (symbol, volume) tuples
        total = len(tickers)
        processed = 0
        any_success = False

        for i in range(0, total, batch_size):
            batch = tickers[i : i + batch_size]
            try:
                snapshots = self.get_snapshots(batch)
                any_success = True
                for sym, snap in snapshots.items():
                    price = snap.get("latest_price", 0)
                    volume = snap.get("daily_volume", 0)
                    if price >= min_price and volume >= min_volume:
                        qualified.append((sym, volume))
            except Exception as e:
                logger.warning("filter_universe_by_liquidity batch %d failed: %s", i, e)

            processed += len(batch)
            if progress_cb:
                progress_cb(processed, total)

        if not any_success:
            logger.warning(
                "All snapshot batches failed — returning unfiltered universe (%d tickers)", total
            )
            return tickers

        # Sort by volume descending
        qualified.sort(key=lambda x: x[1], reverse=True)
        result = [sym for sym, _ in qualified]
        logger.info(
            "Liquidity filter: %d → %d tickers (min_price=%.1f, min_volume=%d)",
            total, len(result), min_price, min_volume,
        )
        return result

    def get_daily_bars_batch(
        self, tickers: list[str], days: int = 130, batch_size: int = 500,
        progress_cb=None, min_bars: int = 20,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch daily OHLCV bars for multiple symbols.

        Alpaca IEX daily bars (primary): ~99.7% coverage — the "IEX covers ~2%"
        caveat only applies to realtime intraday quote streams, not to daily
        aggregates. See docs/alpaca-api.md.

        yfinance (fallback): used for any symbol that Alpaca returned empty or
        with fewer than `min_bars` rows. Protects against flaky single-ticker
        yfinance fetches (2026-04-23 incident) without giving up yfinance's
        broader long-tail coverage.

        Returns {symbol: DataFrame[date, open, high, low, close, volume]}.
        """
        if not tickers:
            return {}

        result: dict[str, pd.DataFrame] = self._fetch_daily_bars_alpaca(
            tickers, days, batch_size=min(batch_size, 200), progress_cb=progress_cb,
        )
        missing = [t for t in tickers if len(result.get(t, [])) < min_bars]
        if missing:
            logger.info(
                "Alpaca returned %d/%d tickers with >=%d bars; falling back to yfinance for %d",
                len(tickers) - len(missing), len(tickers), min_bars, len(missing),
            )
            yf_bars = self._fetch_daily_bars_yfinance(missing, days, batch_size)
            result.update(yf_bars)

        logger.info("Fetched daily bars for %d/%d tickers", len(result), len(tickers))
        return result

    def _fetch_daily_bars_alpaca(
        self, tickers: list[str], days: int, batch_size: int = 200,
        progress_cb=None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch daily bars via Alpaca IEX feed. Best-effort per batch — on a
        batch-level error, log and continue so the yfinance fallback can fill in."""
        if not ALPACA_AVAILABLE or not tickers:
            return {}

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(days * 1.6) + 10)  # calendar→trading padding

        result: dict[str, pd.DataFrame] = {}
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            try:
                req = StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Day,
                    start=start,
                    end=end,
                    feed=DataFeed.IEX,
                )
                bars = self._data.get_stock_bars(req)
                bars_data = bars.data if hasattr(bars, "data") else bars
                for sym, sym_bars in bars_data.items():
                    if not sym_bars:
                        continue
                    df = pd.DataFrame([
                        {
                            "date": b.timestamp,
                            "open": float(b.open),
                            "high": float(b.high),
                            "low": float(b.low),
                            "close": float(b.close),
                            "volume": int(b.volume),
                        }
                        for b in sym_bars
                    ])
                    if not df.empty:
                        result[sym] = df.tail(days).reset_index(drop=True)
            except Exception as e:
                logger.warning(
                    "Alpaca daily-bars batch %d failed (%d tickers): %s — will try yfinance fallback",
                    i // batch_size + 1, len(batch), e,
                )

            if progress_cb:
                progress_cb(min(i + batch_size, len(tickers)), len(tickers))

        return result

    def _fetch_daily_bars_yfinance(
        self, tickers: list[str], days: int, batch_size: int = 500,
    ) -> dict[str, pd.DataFrame]:
        """Fetch daily bars via yfinance. Used as fallback when Alpaca coverage is thin."""
        if not tickers:
            return {}

        import yfinance as yf

        if days <= 30:
            period = "1mo"
        elif days <= 90:
            period = "3mo"
        elif days <= 180:
            period = "6mo"
        else:
            period = "1y"

        result: dict[str, pd.DataFrame] = {}
        batch_timeout = 600  # 10 minutes per batch — normal is ~5 min for 500 tickers

        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            try:
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

                def _download():
                    return yf.download(
                        batch,
                        period=period,
                        group_by="ticker",
                        progress=False,
                        threads=True,
                    )

                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_download)
                    try:
                        raw = future.result(timeout=batch_timeout)
                    except FuturesTimeout:
                        future.cancel()
                        raise TimeoutError(
                            f"yfinance download timed out after {batch_timeout}s for batch "
                            f"{i // batch_size + 1} ({len(batch)} tickers). "
                            f"This usually means yfinance threads are stalled — "
                            f"check network connectivity and yfinance status."
                        )

                if raw.empty:
                    continue

                if len(batch) == 1:
                    sym = batch[0]
                    df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()
                    if not df.empty:
                        df = df.rename(columns={
                            "Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Volume": "volume",
                        })
                        df = df.reset_index().rename(columns={"Date": "date"})
                        result[sym] = df.tail(days)
                else:
                    for sym in batch:
                        try:
                            if sym not in raw.columns.get_level_values(0):
                                continue
                            df = raw[sym][["Open", "High", "Low", "Close", "Volume"]].dropna()
                            if df.empty:
                                continue
                            df = df.rename(columns={
                                "Open": "open", "High": "high", "Low": "low",
                                "Close": "close", "Volume": "volume",
                            })
                            df = df.reset_index().rename(columns={"Date": "date"})
                            result[sym] = df.tail(days)
                        except (KeyError, TypeError):
                            continue
            except Exception as e:
                logger.error("yfinance daily-bars batch %d failed: %s", i, e, exc_info=True)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_side(side: str) -> "OrderSide":
        if not ALPACA_AVAILABLE:
            return side
        mapping = {
            "buy": OrderSide.BUY,
            "sell": OrderSide.SELL,
            "sell_short": OrderSide.SELL,   # Alpaca handles short via position context
            "buy_to_cover": OrderSide.BUY,
        }
        if side not in mapping:
            raise ValueError(f"Unknown order side: {side}")
        return mapping[side]
