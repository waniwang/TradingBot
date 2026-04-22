"""
Smoke-test IBClient end-to-end against the live IB Gateway paper account.

Runs:
  1. Connect + account summary
  2. is_trading_day / is_market_open
  3. get_open_positions
  4. get_latest_bar('AAPL')
  5. get_daily_bars_batch(['AAPL','MSFT'])
  6. get_market_movers_gainers(10)
  7. Place + cancel a limit order far from market (no fill risk)

Use clientId=2 so this test doesn't collide with the main bot (clientId=1).
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # trading-bot/

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

from executor.ib_client import IBClient

config = {
    "environment": "paper",
    "ibkr": {"host": "127.0.0.1", "port": 4002, "client_id": 2},
}

c = IBClient(config)

try:
    print("=== 1. connect ===")
    c.connect()
    print(f"connected: {c.is_connected()}")

    print("\n=== 2. account ===")
    print(f"portfolio_value: {c.get_portfolio_value()}")
    print(f"cash/available:  {c.get_cash()}")

    print("\n=== 3. calendar ===")
    print(f"is_trading_day: {c.is_trading_day()}")
    print(f"is_market_open: {c.is_market_open()}")

    print("\n=== 4. open positions ===")
    print(c.get_open_positions())

    print("\n=== 5. latest bar AAPL ===")
    print(c.get_latest_bar("AAPL"))

    print("\n=== 6. daily bars batch (AAPL, MSFT, 30d) ===")
    bars = c.get_daily_bars_batch(["AAPL", "MSFT"], days=30)
    for sym, df in bars.items():
        print(f"  {sym}: {len(df)} rows, last close={df['close'].iloc[-1]:.2f}")

    print("\n=== 7. market movers gainers (top 10) ===")
    mv = c.get_market_movers_gainers(10)
    print(f"  got {len(mv)} symbols: {[m['symbol'] for m in mv]}")

    print("\n=== 8. place + cancel limit order (no fill risk) ===")
    # AAPL far below market — won't fill
    oid = c.place_limit_order("AAPL", "buy", 1, 50.00)
    print(f"  order id: {oid}")
    time.sleep(2)
    status = c.get_order_status(oid)
    print(f"  status: {status}")
    c.cancel_order(oid)
    time.sleep(2)
    status = c.get_order_status(oid)
    print(f"  after cancel: {status}")

    print("\n=== OK — all IBClient methods work ===")

finally:
    c.disconnect()
