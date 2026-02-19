"""
Generate synthetic OHLCV fixture CSVs for unit tests.

Run once:  python tests/fixtures/generate_fixtures.py
"""

import csv
import os
import random

random.seed(42)
OUT_DIR = os.path.dirname(__file__)


def write_csv(filename: str, rows: list[dict]):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written: {path}")


def make_daily_bars(
    n: int,
    start_price: float,
    trend: float = 0.002,  # daily drift
    vol: float = 0.015,    # daily volatility
    base_volume: int = 1_000_000,
) -> list[dict]:
    rows = []
    price = start_price
    for i in range(n):
        change = price * (trend + random.gauss(0, vol))
        open_ = round(price, 2)
        close = round(price + change, 2)
        high = round(max(open_, close) * (1 + random.uniform(0, 0.01)), 2)
        low = round(min(open_, close) * (1 - random.uniform(0, 0.01)), 2)
        volume = int(base_volume * random.uniform(0.5, 1.5))
        rows.append({
            "date": f"2024-01-{i+1:02d}" if i < 31 else f"2024-02-{i-30:02d}",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        price = close
    return rows


def make_1m_candles(
    n: int,
    start_price: float,
    trend: float = 0.001,
    vol: float = 0.003,
    base_volume: int = 50_000,
) -> list[dict]:
    rows = []
    price = start_price
    for i in range(n):
        change = price * (trend + random.gauss(0, vol))
        open_ = round(price, 2)
        close = round(price + change, 2)
        high = round(max(open_, close) * (1 + random.uniform(0, 0.005)), 2)
        low = round(min(open_, close) * (1 - random.uniform(0, 0.005)), 2)
        volume = int(base_volume * random.uniform(0.5, 2.5))
        rows.append({
            "date": f"09:{30+i//60:02d}:{i%60:02d}" if i < 390 else f"10:{i//60-7:02d}:{i%60:02d}",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        price = close
    return rows


if __name__ == "__main__":
    # Breakout fixture: consolidating stock surfing 20d MA, volume drying up
    breakout_daily = make_daily_bars(60, start_price=50.0, trend=0.001, vol=0.008)
    # Make last 15 days tighter (consolidation)
    for i, row in enumerate(breakout_daily[-15:]):
        row["high"] = round(row["close"] * 1.005, 2)
        row["low"] = round(row["close"] * 0.995, 2)
        row["volume"] = int(row["volume"] * 0.6)
    write_csv("breakout_daily.csv", breakout_daily)

    # Breakout 1m candles: stock breaking ORH with volume
    breakout_1m = make_1m_candles(30, start_price=55.0, trend=0.002, vol=0.002)
    # Make first 5 candles establish ORH around 55.50
    for i in range(5):
        breakout_1m[i]["high"] = round(55.0 + i * 0.1, 2)
        breakout_1m[i]["close"] = round(55.0 + i * 0.08, 2)
    # Make candles 5+ break ORH with volume
    for i in range(5, 30):
        breakout_1m[i]["close"] = round(55.6 + (i - 5) * 0.05, 2)
        breakout_1m[i]["high"] = round(breakout_1m[i]["close"] + 0.05, 2)
        breakout_1m[i]["volume"] = int(breakout_1m[i]["volume"] * 2.0)  # elevated volume
    write_csv("breakout_1m.csv", breakout_1m)

    # EP fixture: stock that gapped up 15%, now breaking ORH
    ep_1m = make_1m_candles(30, start_price=115.0, trend=0.003, vol=0.002)
    for i in range(5):
        ep_1m[i]["high"] = round(115.0 + i * 0.2, 2)
        ep_1m[i]["close"] = round(115.0 + i * 0.15, 2)
    for i in range(5, 30):
        ep_1m[i]["close"] = round(115.8 + (i - 5) * 0.1, 2)
        ep_1m[i]["high"] = round(ep_1m[i]["close"] + 0.1, 2)
        ep_1m[i]["volume"] = int(ep_1m[i]["volume"] * 3.0)  # very high volume (EP)
    write_csv("ep_1m.csv", ep_1m)
    write_csv("ep_daily_volumes.csv", [{"volume": v} for v in
                                        [800_000 + random.randint(-100_000, 100_000) for _ in range(30)]])

    # Parabolic fixture: stock up 60% in 5 days
    parabolic_daily = make_daily_bars(10, start_price=10.0, trend=0.12, vol=0.03)
    write_csv("parabolic_daily.csv", parabolic_daily)

    # Parabolic 1m candles: stock opening weak, breaking below ORB low
    para_1m = make_1m_candles(30, start_price=16.0, trend=-0.003, vol=0.003)
    # First 5 candles: form ORB low around 15.80
    for i in range(5):
        para_1m[i]["low"] = round(16.0 - i * 0.04, 2)
        para_1m[i]["close"] = round(16.0 - i * 0.03, 2)
    # Candles 5+: break below ORB low
    for i in range(5, 30):
        para_1m[i]["close"] = round(15.75 - (i - 5) * 0.03, 2)
        para_1m[i]["low"] = round(para_1m[i]["close"] - 0.05, 2)
    write_csv("parabolic_1m.csv", para_1m)

    print("All fixtures generated.")
