"""
Interactive Brokers broker client — implements the same public interface as
AlpacaClient so it can be swapped in for EP earnings + EP news strategies.

Runs alongside IB Gateway (paper account) on the local machine:
    Gateway host: 127.0.0.1
    Gateway port: 4002 (paper default)

Design notes:
- ib_async is async-native. We run a persistent asyncio event loop in a
  background thread and expose synchronous wrapper methods so callers don't
  have to know about asyncio.
- Only the subset of AlpacaClient methods used by the EP strategies is
  implemented. Methods used by intraday-stream strategies (subscribe_quotes,
  get_candles_1m, etc.) are not reimplemented here.
- get_daily_bars_batch is broker-agnostic (yfinance). It is duplicated here for
  now rather than extracted to a shared module; if/when a third broker is
  added, pull it out into core/market_data.py.
- Order IDs: IB uses integer orderIds; we stringify them so the DB Order model
  (String(64)) works unchanged.
- Calendar: IB has no calendar API like Alpaca's. We use exchange_calendars
  (XNYS) for holiday-aware trading-day checks.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

try:
    from ib_async import (
        IB,
        Stock,
        LimitOrder,
        StopOrder,
        MarketOrder,
        ScannerSubscription,
    )
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False
    logger.warning("ib_async not installed — IBClient running in stub mode")


class IBClient:
    """
    Thin wrapper around ib_async with the same public API as AlpacaClient
    (subset used by EP earnings + EP news strategies).

    Usage:
        client = IBClient(config)
        client.connect()
        value = client.get_portfolio_value()
        oid = client.place_limit_order("AAPL", "buy", 100, 175.50)
        client.disconnect()
    """

    def __init__(self, config: dict, notify=None):
        self.env: str = config.get("environment", "paper")
        self._notify = notify or (lambda msg: None)
        ib_cfg = config.get("ibkr", {})

        self._host: str = ib_cfg.get("host", "127.0.0.1")
        self._port: int = int(ib_cfg.get("port", 4002))
        self._client_id: int = int(ib_cfg.get("client_id", 1))
        # Paper account ids start with "DU" (e.g. "DU3353764"). We don't use
        # this for routing — IB Gateway already points to the paper account —
        # but it's useful for logging.
        self._account: str = ib_cfg.get("account", "")

        self._ib: "IB | None" = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._connected: bool = False

        # Contract qualification cache: symbol -> qualified Contract
        self._contracts: dict[str, object] = {}
        self._contracts_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Event-loop bridge
    # ------------------------------------------------------------------

    def _start_loop(self):
        """Start a persistent asyncio loop in a background thread."""
        if self._loop is not None and self._loop.is_running():
            return
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="ib-asyncio-loop"
        )
        self._loop_thread.start()

    def _run(self, coro, timeout: float = 30.0):
        """Run an async coroutine on the background loop, blocking until done."""
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("IBClient event loop is not running — call connect() first")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self):
        if not IB_AVAILABLE:
            logger.info("[stub] IBClient.connect()")
            return

        self._start_loop()
        self._ib = IB()

        async def _connect():
            await self._ib.connectAsync(
                self._host, self._port, clientId=self._client_id, timeout=15
            )
            # Default to delayed data if we don't have a live subscription
            # (paper accounts typically don't). Harmless for accounts that do.
            self._ib.reqMarketDataType(3)

        try:
            self._run(_connect(), timeout=20)
            self._connected = True
            accounts = self._ib.managedAccounts()
            logger.info(
                "Connected to IB Gateway at %s:%d (clientId=%d) accounts=%s",
                self._host, self._port, self._client_id, accounts,
            )
        except Exception as e:
            logger.error("Failed to connect to IB Gateway: %s", e)
            raise

    def disconnect(self):
        if not IB_AVAILABLE or not self._connected:
            return
        try:
            # ib_async.disconnect() is synchronous — schedule on loop to avoid
            # cross-thread access to the ib object.
            if self._loop and self._loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(
                    asyncio.to_thread(self._ib.disconnect), self._loop
                )
                fut.result(timeout=5)
            else:
                self._ib.disconnect()
        except Exception as e:
            logger.warning("Error during IB disconnect: %s", e)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._connected = False
        logger.info("Disconnected from IB Gateway")

    def is_connected(self) -> bool:
        if not IB_AVAILABLE:
            return True  # stub mode
        return bool(self._ib and self._ib.isConnected())

    # ------------------------------------------------------------------
    # Contract helpers
    # ------------------------------------------------------------------

    async def _qualify(self, symbol: str):
        """Qualify a Stock contract (fills in conId). Cached per symbol."""
        with self._contracts_lock:
            if symbol in self._contracts:
                return self._contracts[symbol]
        contract = Stock(symbol, "SMART", "USD")
        qualified = await self._ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract for {symbol}")
        c = qualified[0]
        with self._contracts_lock:
            self._contracts[symbol] = c
        return c

    # ------------------------------------------------------------------
    # Market calendar (exchange_calendars — IB has no native calendar API)
    # ------------------------------------------------------------------

    def _nyse(self):
        import exchange_calendars as ec
        return ec.get_calendar("XNYS")

    def get_market_clock(self) -> dict:
        """Return {is_open, next_open, next_close} using exchange_calendars."""
        try:
            cal = self._nyse()
            now = pd.Timestamp.utcnow()
            is_open = bool(cal.is_open_on_minute(now.floor("min")))
            # next_open / next_close: take next session bounds
            next_session = cal.next_session(now.normalize())
            next_open = cal.session_first_minute(next_session).to_pydatetime()
            next_close = cal.session_close(next_session).to_pydatetime()
            return {"is_open": is_open, "next_open": next_open, "next_close": next_close}
        except Exception as e:
            logger.warning("get_market_clock failed: %s", e)
            now = datetime.now(timezone.utc)
            return {"is_open": False, "next_open": now, "next_close": now}

    def is_market_open(self) -> bool:
        try:
            cal = self._nyse()
            now = pd.Timestamp.utcnow()
            return bool(cal.is_open_on_minute(now.floor("min")))
        except Exception as e:
            logger.warning("is_market_open failed: %s", e)
            return False

    def is_trading_day(self) -> bool:
        """True if today is a US equity trading day (NYSE calendar)."""
        try:
            from zoneinfo import ZoneInfo
            today = datetime.now(ZoneInfo("America/New_York")).date()
            cal = self._nyse()
            return bool(cal.is_session(pd.Timestamp(today)))
        except Exception as e:
            logger.warning("is_trading_day check failed, falling back to weekday: %s", e)
            return datetime.now().weekday() < 5

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def _account_value(self, tag: str) -> float:
        """Fetch a single AccountSummary tag as a float."""
        if not IB_AVAILABLE or not self._connected:
            return 0.0
        try:
            rows = self._run(self._ib.accountSummaryAsync(), timeout=10)
            for r in rows:
                if r.tag == tag:
                    try:
                        return float(r.value)
                    except (TypeError, ValueError):
                        return 0.0
            return 0.0
        except Exception as e:
            logger.warning("account_value(%s) failed: %s", tag, e)
            return 0.0

    def get_portfolio_value(self) -> float:
        if not IB_AVAILABLE:
            return 100_000.0
        return self._account_value("NetLiquidation")

    def get_cash(self) -> float:
        if not IB_AVAILABLE:
            return 100_000.0
        # AvailableFunds = cash minus margin used (closer to "buying headroom")
        return self._account_value("AvailableFunds")

    def get_open_positions(self) -> list[dict]:
        if not IB_AVAILABLE or not self._connected:
            return []
        try:
            positions = self._ib.positions()
        except Exception as e:
            logger.warning("get_open_positions failed: %s", e)
            return []

        out = []
        for p in positions:
            qty = float(p.position or 0)
            if qty == 0:
                continue
            side = "long" if qty > 0 else "short"
            out.append({
                "symbol": p.contract.symbol,
                "qty": abs(qty),
                "side": side,
                "avg_entry_price": float(p.avgCost or 0),
                # IB positions() doesn't include current price / unrealized pl —
                # leave as 0; the API route pulls fresh prices via get_latest_bar().
                "current_price": 0.0,
                "unrealized_pl": 0.0,
                "market_value": 0.0,
            })
        return out

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    @staticmethod
    def _action_for_side(side: str) -> str:
        """Map Alpaca-style sides to IB actions."""
        side = side.lower()
        if side in {"buy", "buy_to_cover"}:
            return "BUY"
        if side in {"sell", "sell_short"}:
            return "SELL"
        raise ValueError(f"Unknown side: {side}")

    def place_limit_order(
        self, ticker: str, side: str, shares: int, price: float
    ) -> str:
        if not IB_AVAILABLE:
            stub_id = f"STUB-{ticker}-{int(time.time())}"
            logger.info("[stub] place_limit_order %s %s %d @ %.2f", side, ticker, shares, price)
            return stub_id

        async def _submit():
            contract = await self._qualify(ticker)
            action = self._action_for_side(side)
            order = LimitOrder(action, shares, round(price, 2))
            order.tif = "DAY"
            trade = self._ib.placeOrder(contract, order)
            # Give IB a moment to assign an order ID and acknowledge
            await asyncio.sleep(0.3)
            return trade

        trade = self._run(_submit(), timeout=15)
        order_id = str(trade.order.orderId)
        logger.info("IB limit order placed: %s %s %d @ %.2f → id=%s",
                    side, ticker, shares, price, order_id)
        return order_id

    def place_stop_order(
        self, ticker: str, side: str, shares: int, stop_price: float
    ) -> str:
        if not IB_AVAILABLE:
            stub_id = f"STUB-STOP-{ticker}-{int(time.time())}"
            return stub_id

        async def _submit():
            contract = await self._qualify(ticker)
            action = self._action_for_side(side)
            order = StopOrder(action, shares, round(stop_price, 2))
            order.tif = "GTC"
            trade = self._ib.placeOrder(contract, order)
            await asyncio.sleep(0.3)
            return trade

        trade = self._run(_submit(), timeout=15)
        order_id = str(trade.order.orderId)
        logger.info("IB stop order placed: %s %s %d @ %.2f → id=%s",
                    side, ticker, shares, stop_price, order_id)
        return order_id

    def modify_stop_order(self, order_id: str, new_stop_price: float):
        """Modify an existing stop order by re-submitting with the same orderId."""
        if not IB_AVAILABLE:
            logger.info("[stub] modify_stop_order %s → %.2f", order_id, new_stop_price)
            return

        oid = int(order_id)

        async def _modify():
            # Find the existing open trade/order
            trades = self._ib.openTrades()
            target = next((t for t in trades if t.order.orderId == oid), None)
            if target is None:
                raise ValueError(f"No open order with id {order_id} to modify")
            target.order.auxPrice = round(new_stop_price, 2)  # stop price lives in auxPrice
            # Re-submit with same orderId → IB treats as modification
            self._ib.placeOrder(target.contract, target.order)
            await asyncio.sleep(0.3)

        self._run(_modify(), timeout=15)
        logger.info("IB stop order %s updated → %.2f", order_id, new_stop_price)

    def cancel_order(self, order_id: str):
        if not IB_AVAILABLE:
            logger.info("[stub] cancel_order %s", order_id)
            return
        oid = int(order_id)

        async def _cancel():
            trades = self._ib.openTrades()
            target = next((t for t in trades if t.order.orderId == oid), None)
            if target is None:
                # Maybe it's already terminal. Try cancelOrder by Order object anyway.
                logger.warning("cancel_order: order %s not in openTrades (may already be done)", order_id)
                return
            self._ib.cancelOrder(target.order)
            await asyncio.sleep(0.3)

        self._run(_cancel(), timeout=10)
        logger.info("IB cancelled order %s", order_id)

    def close_position(self, ticker: str, shares: int, side: str) -> str:
        """Market order to close a position. `side` is the existing position side."""
        if not IB_AVAILABLE:
            return f"STUB-CLOSE-{ticker}-{int(time.time())}"

        action = "SELL" if side == "long" else "BUY"

        async def _submit():
            contract = await self._qualify(ticker)
            order = MarketOrder(action, shares)
            order.tif = "DAY"
            trade = self._ib.placeOrder(contract, order)
            await asyncio.sleep(0.3)
            return trade

        trade = self._run(_submit(), timeout=15)
        order_id = str(trade.order.orderId)
        logger.info("IB market close %s %d (%s) → id=%s", ticker, shares, side, order_id)
        return order_id

    def get_order_status(self, order_id: str) -> dict:
        """
        Query status of an order. Returns:
            {order_id, status, filled_qty, filled_avg_price}
        Status values normalised to Alpaca-like strings:
            submitted, partially_filled, filled, cancelled, rejected
        """
        if not IB_AVAILABLE:
            return {"order_id": order_id, "status": "filled", "filled_qty": 100, "filled_avg_price": 0.0}

        oid = int(order_id)

        async def _status():
            # Look across open + recently completed trades
            all_trades = list(self._ib.trades())
            target = next((t for t in all_trades if t.order.orderId == oid), None)
            return target

        trade = self._run(_status(), timeout=10)
        if trade is None:
            # Unknown — could be very new (not yet acknowledged). Treat as submitted.
            return {"order_id": order_id, "status": "submitted", "filled_qty": 0, "filled_avg_price": 0.0}

        ib_status = (trade.orderStatus.status or "").lower()
        # Map IB statuses -> Alpaca-like vocabulary used by core/execution.py
        mapping = {
            "pendingsubmit": "submitted",
            "pendingcancel": "submitted",
            "presubmitted": "submitted",
            "submitted": "submitted",
            "apicancelled": "cancelled",
            "cancelled": "cancelled",
            "filled": "filled",
            "inactive": "rejected",
        }
        status = mapping.get(ib_status, ib_status)
        filled_qty = int(trade.orderStatus.filled or 0)
        remaining = int(trade.orderStatus.remaining or 0)
        if filled_qty > 0 and remaining > 0 and status != "filled":
            status = "partially_filled"

        return {
            "order_id": order_id,
            "status": status,
            "filled_qty": filled_qty,
            "filled_avg_price": float(trade.orderStatus.avgFillPrice or 0),
        }

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_latest_bar(self, ticker: str) -> dict:
        """Latest snapshot for a ticker (delayed or live depending on subscription)."""
        if not IB_AVAILABLE or not self._connected:
            return {"ticker": ticker, "last_price": 0.0, "volume": 0}

        async def _snap():
            contract = await self._qualify(ticker)
            t = self._ib.reqMktData(contract, snapshot=True)
            # Wait a few seconds for snapshot to arrive
            for _ in range(30):
                await asyncio.sleep(0.1)
                price = t.last or t.close or t.marketPrice()
                if price and price == price:  # not NaN
                    break
            return t

        try:
            t = self._run(_snap(), timeout=10)
            last = t.last or t.close or 0.0
            return {
                "ticker": ticker,
                "last_price": float(last) if last == last else 0.0,
                "open": float(t.open) if t.open and t.open == t.open else 0.0,
                "high": float(t.high) if t.high and t.high == t.high else 0.0,
                "low": float(t.low) if t.low and t.low == t.low else 0.0,
                "volume": int(t.volume) if t.volume and t.volume == t.volume else 0,
            }
        except Exception as e:
            logger.warning("get_latest_bar(%s) failed: %s", ticker, e)
            return {"ticker": ticker, "last_price": 0.0, "volume": 0}

    # ------------------------------------------------------------------
    # Scanner helpers (used by strategies/ep_*/scanner.py)
    # ------------------------------------------------------------------

    def get_market_movers_gainers(self, top: int = 50) -> list[dict]:
        """Top US stock % gainers via IB Scanner (TOP_PERC_GAIN)."""
        if not IB_AVAILABLE or not self._connected:
            return []

        top = min(top, 50)  # IB Scanner max

        async def _scan():
            sub = ScannerSubscription(
                instrument="STK",
                locationCode="STK.US.MAJOR",
                scanCode="TOP_PERC_GAIN",
                numberOfRows=top,
            )
            data = await self._ib.reqScannerDataAsync(sub)
            return data

        try:
            data = self._run(_scan(), timeout=15)
        except Exception as e:
            logger.error("get_market_movers_gainers failed: %s", e)
            return []

        results = []
        # IB scanner returns ScanData entries with contractDetails.contract
        for item in data:
            try:
                sym = item.contractDetails.contract.symbol
                results.append({
                    "symbol": sym,
                    # IB scanner doesn't return percent_change/price directly on
                    # the scan result; downstream callers fetch snapshots anyway.
                    "percent_change": 0.0,
                    "price": 0.0,
                })
            except AttributeError:
                continue
        return results

    def get_snapshots(self, tickers: list[str]) -> dict[str, dict]:
        """
        Snapshot data for multiple tickers. Mirrors AlpacaClient.get_snapshots():
        returns {symbol: {prev_close, prev_high, latest_price, daily_volume,
                          open, today_high, today_low}}
        """
        if not IB_AVAILABLE or not self._connected or not tickers:
            return {}

        # IB rate limit: ~50 msg/sec. Snapshot reqs are light; we throttle gently.
        async def _fetch_all():
            out: dict[str, dict] = {}
            # Fire requests in small batches to stay within limits
            BATCH = 20
            for i in range(0, len(tickers), BATCH):
                batch = tickers[i:i + BATCH]
                tickers_and_contracts = []
                for sym in batch:
                    try:
                        c = await self._qualify(sym)
                        tickers_and_contracts.append((sym, c))
                    except Exception as e:
                        logger.debug("qualify %s failed: %s", sym, e)
                        continue

                # Request snapshots in parallel
                ib_tickers = []
                for sym, contract in tickers_and_contracts:
                    t = self._ib.reqMktData(contract, snapshot=True)
                    ib_tickers.append((sym, t))

                # Wait for snapshot results (max ~5s per batch)
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    # Continue once every ticker has at least one of last/close
                    done = all(
                        (t.last and t.last == t.last) or (t.close and t.close == t.close)
                        for _, t in ib_tickers
                    )
                    if done:
                        break

                for sym, t in ib_tickers:
                    def _f(x):
                        if x is None or x != x:  # NaN check
                            return 0.0
                        return float(x)
                    last = _f(t.last) or _f(t.close) or _f(t.marketPrice())
                    prev_close = _f(t.close)  # prev-day close comes as `close` on snapshot
                    out[sym] = {
                        "prev_close": prev_close,
                        "prev_high": _f(t.high),   # not strictly prev-day; IB snapshot doesn't distinguish
                        "latest_price": last,
                        "daily_volume": int(_f(t.volume)),
                        "open": _f(t.open),
                        "today_high": _f(t.high),
                        "today_low": _f(t.low),
                    }
                # Avoid hammering IB
                await asyncio.sleep(0.2)
            return out

        try:
            return self._run(_fetch_all(), timeout=120)
        except Exception as e:
            logger.error("get_snapshots failed: %s", e)
            return {}

    # ------------------------------------------------------------------
    # Daily bars (broker-agnostic — uses yfinance)
    # ------------------------------------------------------------------

    def get_daily_bars_batch(
        self, tickers: list[str], days: int = 130, batch_size: int = 500,
        progress_cb=None,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch daily OHLCV bars for multiple symbols via yfinance.
        (Identical logic to AlpacaClient.get_daily_bars_batch — both brokers
         use yfinance for daily history since IB/Alpaca free tiers have poor
         coverage.)
        """
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
        batch_timeout = 600

        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            try:
                def _download():
                    return yf.download(
                        batch, period=period, group_by="ticker",
                        progress=False, threads=True,
                    )

                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_download)
                    try:
                        raw = future.result(timeout=batch_timeout)
                    except FuturesTimeout:
                        future.cancel()
                        raise TimeoutError(
                            f"yfinance download timed out after {batch_timeout}s"
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
                logger.error("get_daily_bars_batch failed for batch %d: %s", i, e, exc_info=True)

            if progress_cb:
                progress_cb(min(i + batch_size, len(tickers)), len(tickers))

        logger.info("Fetched daily bars for %d/%d tickers", len(result), len(tickers))
        return result
