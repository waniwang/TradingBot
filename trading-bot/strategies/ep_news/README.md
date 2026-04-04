# EP News Strategy

EOD long swing on news-driven (non-earnings) gap-ups. Same timing as EP Earnings but excludes earnings events.

## Flow

1. **3:00 PM** — `scanner.py` finds news gappers (excludes earnings), `strategy.py` evaluates A/B filters
2. **3:50 PM** — Plugin executes entries near close
3. **Ongoing** — Stop per strategy tier, max 50-day hold

## Scanner Filters (`scanner.py`)

Same three-phase filter as EP Earnings, with differences:

| Difference | EP Earnings | EP News |
|------------|------------|---------|
| Market cap | >= $800M | >= $1B |
| Earnings | Required | Excluded |

## Strategy A (NEWS-Tight) — stop -7%

| Filter | Value |
|--------|-------|
| CHG-OPEN% | between 2% and 10% |
| Close in range | >= 50% |
| Downside from open | < 3% |
| Prev 10D change | <= -20% |
| ATR% | between 3% and 7% |
| Volume | < 3M shares |

## Strategy B (NEWS-Relaxed) — stop -10%

| Filter | Value |
|--------|-------|
| CHG-OPEN% | between 2% and 10% |
| Close in range | between 30% and 80% |
| Downside from open | < 6% |
| Prev 10D change | <= -10% |
| ATR% | between 3% and 7% |
| Volume | < 5M shares |

If both pass, Strategy A is used (tighter stop).

## Key Config (`config.yaml`)

```yaml
min_gap_pct: 8.0
min_price: 3.0
min_market_cap: 1_000_000_000
exclude_earnings: true
max_hold_days: 50
```
