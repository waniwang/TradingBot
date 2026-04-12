#!/usr/bin/env python3
"""Reset the trading bot database — drops all tables and recreates them empty."""

import sys
from db.models import Base, get_engine, init_db


def main():
    engine = get_engine()
    url = str(engine.url)

    print(f"Database: {url}")
    print("This will DELETE all data (signals, orders, positions, watchlist, jobs, P&L).")

    if "--yes" not in sys.argv:
        confirm = input("Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return

    print("Dropping all tables...")
    Base.metadata.drop_all(engine)

    print("Recreating tables...")
    Base.metadata.create_all(engine)

    print("Done — database reset to empty state.")


if __name__ == "__main__":
    main()
