"""
Quick IB Gateway connectivity test.

Usage: .venv/bin/python scripts/test_ib_connection.py

Requires IB Gateway to be running and logged in to paper account,
with API enabled on port 4002 and 127.0.0.1 in trusted IPs.
"""
from ib_async import IB, util

HOST = "127.0.0.1"
PORT = 4002   # paper gateway default
CLIENT_ID = 1


def main():
    ib = IB()
    print(f"Connecting to IB Gateway at {HOST}:{PORT} (clientId={CLIENT_ID})...")
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=10)
    except Exception as e:
        print(f"FAILED to connect: {e}")
        print("Troubleshooting:")
        print("  - Is IB Gateway running and logged in to a PAPER account?")
        print("  - Configure -> Settings -> API -> Settings:")
        print("      * Enable ActiveX and Socket Clients: checked")
        print("      * Read-Only API: unchecked")
        print("      * Socket port: 4002")
        print("      * Trusted IPs includes 127.0.0.1")
        return

    print("Connected.")
    print(f"Server version: {ib.client.serverVersion()}")

    # Account summary
    print("\nAccount Summary:")
    summary = ib.accountSummary()
    keys_of_interest = {"NetLiquidation", "AvailableFunds", "BuyingPower", "CashBalance", "AccountType"}
    for item in summary:
        if item.tag in keys_of_interest:
            print(f"  {item.tag:<20} {item.value} {item.currency}")

    # Managed accounts (paper account IDs start with DU)
    print(f"\nManaged accounts: {ib.managedAccounts()}")

    # Quick market data sanity check (delayed ok)
    from ib_async import Stock
    ib.reqMarketDataType(3)  # 3 = delayed, works without live subscription
    contract = Stock("AAPL", "SMART", "USD")
    ib.qualifyContracts(contract)
    ticker = ib.reqMktData(contract, snapshot=True)
    ib.sleep(3)
    print(f"\nAAPL delayed snapshot: last={ticker.last or ticker.close} bid={ticker.bid} ask={ticker.ask}")

    ib.disconnect()
    print("\nOK — IB Gateway is reachable and API works.")


if __name__ == "__main__":
    main()
