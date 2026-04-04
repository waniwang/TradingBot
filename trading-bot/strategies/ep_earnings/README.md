# EP Earnings Strategy

EOD long swing on earnings gap-ups. Scans at 3 PM, enters near close at 3:50 PM. Holds up to 50 days.

## Flow

1. **3:00 PM** — `scanner.py` finds earnings gappers, `strategy.py` evaluates A/B filters
2. **3:50 PM** — Plugin executes entries (limit orders near close)
3. **Ongoing** — -7% stop, max 50-day hold, shared exit logic (partial exits, trailing MA)

## Scanner Filters (`scanner.py`)

Three-phase filter:

| Phase | Filter | Threshold |
|-------|--------|-----------|
| A (Alpaca) | Price | > $3 (prev close) |
| A | Gap % | >= 8% |
| A | Open | > prev day's high |
| B (yfinance) | Open | > 200-day SMA |
| B | RVOL | >= 1.0x (14d avg) |
| B | Prior 6mo gain | < 50% |
| C (per-ticker) | Market cap | >= $800M |
| C | Earnings | Must have earnings on gap day |

## Strategy A (Tight) — 69% WR, PF 5.68

| Filter | Value |
|--------|-------|
| CHG-OPEN% | > 0 (positive intraday) |
| Close in range | >= 50% (top half) |
| Downside from open | < 3% |
| Prev 10D change | between -30% and -10% |
| Stop | -7% |

## Strategy B (Relaxed) — 61% WR, PF 5.62

| Filter | Value |
|--------|-------|
| CHG-OPEN% | > 0 |
| Close in range | >= 50% |
| ATR% | between 2% and 5% |
| Prev 10D change | < -10% |
| Stop | -7% |

## Kill Zones (Avoid)

- Prev 10D > 0% (ran up into earnings): 31% WR, -7.4% mean
- CHG-OPEN% < 0 AND close_in_range < 50: 40% WR

## Key Config (`config.yaml`)

```yaml
min_gap_pct: 8.0
min_price: 3.0
min_market_cap: 800_000_000
stop_loss_pct: 7.0
max_hold_days: 50
```
