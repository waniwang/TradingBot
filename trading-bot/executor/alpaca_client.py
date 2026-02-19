"""
Alpaca broker client — replaces moomoo_client.py.

Uses alpaca-py SDK. No gateway process required — pure REST + WebSocket.

Paper trading:  environment: paper  → TradingClient(paper=True)
Live trading:   environment: live   → TradingClient(paper=False)

Alpaca dashboard: https://alpaca.markets
API docs:         https://docs.alpaca.markets
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

logger = logging.getLogger(__name__)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        LimitOrderRequest,
        MarketOrderRequest,
        StopOrderRequest,
        ReplaceOrderRequest,
        GetOrdersRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import (
        StockBarsRequest,
        StockLatestQuoteRequest,
        StockLatestBarRequest,
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.live import StockDataStream
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

    def __init__(self, config: dict):
        self.env: str = config.get("environment", "paper")
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
        self._stream: StockDataStream | None = None
        self._stream_thread: "threading.Thread | None" = None
        self._subscribed_tickers: list[str] = []
        self._watchdog_stop: "threading.Event | None" = None

        self._stream_callbacks: list[Callable] = []

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

        self._stream = StockDataStream(
            api_key=self._api_key,
            secret_key=self._secret_key,
            feed="iex",
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
            for cb in self._stream_callbacks:
                cb(data)

        self._stream.subscribe_bars(_bar_handler, *tickers)
        self._stream_thread = threading.Thread(target=self._stream.run, daemon=True)
        self._stream_thread.start()
        logger.info("Stream started for %s (IEX feed)", tickers)

    def _stream_watchdog(self):
        """Monitor stream thread every 30s; reconnect if it has died."""
        import threading
        import time

        while not self._watchdog_stop.is_set():
            self._watchdog_stop.wait(timeout=30)
            if self._watchdog_stop.is_set():
                break

            if self._stream_thread and not self._stream_thread.is_alive():
                logger.warning("Stream thread died — attempting reconnect...")
                try:
                    self._start_stream(self._subscribed_tickers)
                    logger.info("Stream reconnected for %s", self._subscribed_tickers)
                except Exception as e:
                    logger.error("Stream reconnect failed: %s", e)

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

        req = StockLatestBarRequest(symbol_or_symbols=ticker, feed="iex")
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
            feed="iex",
            limit=count,
        )
        bars = self._data.get_stock_bars(req)
        ticker_bars = bars.get(ticker, [])

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
            feed="iex",
            limit=days,
        )
        bars = self._data.get_stock_bars(req)
        ticker_bars = bars.get(ticker, [])

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
