# EP News Strategy

EOD long swing on news-driven (non-earnings) gap-ups. Same timing as EP Earnings but excludes earnings events.

## Flow

1. **3:05 PM** — `scanner.py` finds news gappers (excludes earnings), `strategy.py` evaluates A/B/C filters. A/B → `Watchlist(stage="ready")`, C → `Watchlist(stage="watching", meta.day2_confirm=true)`.
2. **3:45 PM** — `job_day2_confirm` snapshots prices for yesterday's `watching` C rows; confirmed → flips to `stage="ready"` with execution payload in `meta`; rejected → `stage="expired"`.
3. **3:50–3:59 PM** — `job_execute` queries `Watchlist.stage="ready"` and places orders. Fires every minute (10 attempts) so a briefly-down bot or a transient Alpaca error still gets a trade in before close. **DB-driven + idempotent** — open-Position guard and <10-minute non-terminal Order guard block double-entry across retries.
4. **Ongoing** — Stop per strategy tier, max hold (50d for A/B, 20d for C).

**Note:** EP News scans at 3:05 PM (offset from EP Earnings at 3:00 PM) to avoid yfinance rate limiting from simultaneous per-ticker API calls.

### Watchlist stage semantics

Same as EP Earnings — `watching` (C pending) → `ready` (staged for execution) → `triggered` (order placed) → `expired`. Strategy variant lives in `meta.ep_strategy`. Plugins pass `watchlist_setup_type="ep_news"` to `_execute_entry` so the C-flavored signal `setup_type="ep_news_c"` still flips the right Watchlist row.

## Scanner Filters (`scanner.py`)

Same three-phase filter as EP Earnings, with differences:

| Difference | EP Earnings | EP News |
|------------|------------|---------|
| Market cap | >= $800M | >= $1B |
| Earnings | Required | Excluded |

**Earnings exclusion:** `_confirm_no_earnings()` returns `True` when the yfinance earnings calendar confirms no earnings today/yesterday. API failures are **not** swallowed — they propagate up, the scan fails, and a Telegram alert fires (per project error-handling policy). That way we never enter earnings-driven gaps as "news" due to a stale fallback.

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

## Strategy C (Bear Market / Day-2 Confirm) -- stop -7%, hold 20D

Designed for bear market regimes where "strong gap day" filters (A/B) select stocks that get sold off hardest.

| Filter | Value |
|--------|-------|
| Prev 10D change | <= -10% (beaten down pre-news) |
| ATR% | between 2% and 5% |
| Day-2 confirm | 1D return > 0 (stock holds up next day) |
| Stop | -7% |
| Hold | 20 days |

**Entry timing:** Scanned on gap day (3:05 PM), but NOT entered until day 2 (3:50 PM) after confirming positive 1D return.

## Backtesting

Uses spreadsheet-based backtest with pre-computed gap-day features and forward return checkpoints.

**Data source:** `backtest/data/2020-2025 EP Selection NEWS V2.xlsx` — 4,714 news gap candidates (2020-2025).

**How to run:**

```bash
cd trading-bot
.venv/bin/python run_ep_backtest.py --type news              # both A and B
.venv/bin/python run_ep_backtest.py --type news --strategy A  # single strategy
.venv/bin/python run_ep_backtest.py --type news --year 2025   # single year
.venv/bin/python run_ep_backtest.py --type news --trades      # show trade log
```

**Methodology:**
- Same as EP earnings backtest (see `strategies/ep_earnings/README.md`)
- Strategy A uses -7% stop; Strategy B uses -10% stop (per `config.yaml`)
- Known limitation: checkpoint stops miss intra-period dips (slightly optimistic)

**Results (2020-2025, 4,714 candidates):**

| Metric | Strategy A | Strategy B |
|--------|-----------|-----------|
| Trades | 48 | 137 |
| Win Rate | 67% | 62% |
| Avg Return | +21.26% | +17.28% |
| Profit Factor | 10.82 | 6.17 |
| Best Year | 2020 (75% WR) | 2020 (78% WR) |
| Worst Year | 2021 (50% WR) | 2021 (29% WR) |

Strategy A has very tight filters (48 trades over 6 years) but exceptional quality. Strategy B offers more trades with strong returns.

## Key Config (`config.yaml`)

```yaml
min_gap_pct: 8.0
min_price: 3.0
min_market_cap: 1_000_000_000
exclude_earnings: true
max_hold_days: 50
```

## Dashboard Parameter Display

The Strategies detail page (`/strategies/ep_news`) renders each parameter with its description, variation (A/B/C), and the phase/job where it's applied (scan vs execute vs day-2 confirm). Descriptions and phase tags live in [`trading-bot/api/param_meta.py`](../../api/param_meta.py). Update that file whenever you add a new `ep_news_*` key to `config.yaml`.

The A/B/C badges on trade rows and pipeline job details are derived at read time via [`api/variation.py`](../../api/variation.py), which joins `Signal` back to the originating `Watchlist.meta["ep_strategy"]`.
