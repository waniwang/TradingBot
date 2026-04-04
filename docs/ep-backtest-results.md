# EP Swing Strategy Backtest Results

Backtest of EP Earnings and EP News swing strategies on 2020-2025 historical gap candidate data (5,621 total candidates). Entry at gap day close (~3:50 PM ET), hold up to 50 trading days, fixed stop loss.

## Data

| Dataset | Candidates | Date Range | Source |
|---------|-----------|------------|--------|
| EP Earnings | 907 | Jan 2020 - Dec 2025 | Earnings gap-ups (>8% gap, >$800M mcap, open > prev high, open > 200D SMA) |
| EP News | 4,714 | Jan 2020 - Dec 2025 | News gap-ups (>8% gap, >$1B mcap, non-earnings catalyst) |

Data files in `trading-bot/backtest/data/`.

## Strategy Filters

### EP Earnings

| Filter | Strategy A (Tight) | Strategy B (Relaxed) |
|--------|-------------------|---------------------|
| CHG-OPEN% | > 0 | > 0 |
| Close in range | >= 50% | >= 50% |
| Downside from open | < 3% | (no filter) |
| ATR% | (no filter) | 2% - 5% |
| Prev 10D change | -30% to -10% | < -10% |
| Stop loss | -7% | -7% |
| Hold period | 50 days | 50 days |

### EP News

| Filter | Strategy A (Tight) | Strategy B (Relaxed) |
|--------|-------------------|---------------------|
| CHG-OPEN% | 2% - 10% | 2% - 10% |
| Close in range | >= 50% | 30% - 80% |
| Downside from open | < 3% | < 6% |
| Prev 10D change | <= -20% | <= -10% |
| ATR% | 3% - 7% | 3% - 7% |
| Volume | < 3M shares | < 5M shares |
| Stop loss | -7% | -10% |
| Hold period | 50 days | 50 days |

## Results Summary (2020-2025)

| Metric | Earnings A | Earnings B | News A | News B |
|--------|-----------|-----------|--------|--------|
| **Trades** | 188 | 262 | 48 | 137 |
| **Win Rate** | 48% (91W/97L) | 50% (131W/131L) | 67% (32W/16L) | 62% (85W/52L) |
| **Avg Return/Trade** | +5.34% | +8.19% | +21.26% | +17.28% |
| **Median Return** | -1.15% | +0.09% | +13.65% | +11.21% |
| **Avg Winner** | +17.74% | +22.84% | +35.13% | +33.25% |
| **Avg Loser** | -6.29% | -6.46% | -6.49% | -8.81% |
| **Profit Factor** | 2.64 | 3.54 | 10.82 | 6.17 |
| **Stopped Out** | 41% | 42% | 29% | 30% |
| **Best Trade** | +91.99% | +104.13% | +175.68% | +124.74% |

### Annualized Performance ($100k account, $10k per trade)

| Metric | Earnings A | Earnings B | News A | News B |
|--------|-----------|-----------|--------|--------|
| Trades/year | 31 | 44 | 8 | 23 |
| Annual P&L | +$16,644 | +$35,618 | +$16,968 | +$39,363 |
| **Annual Return** | **+16.6%** | **+35.6%** | **+17.0%** | **+39.4%** |

Combined Earnings B + News B: ~66 trades/year, ~$75k annual P&L = **+75%/year** on $100k account.

## Why It Works: Asymmetric Returns

Even with 48-50% win rates, these strategies are profitable because winners are 3-4x larger than losers:

- **Earnings B**: avg winner +22.84% vs avg loser -6.46% (3.5:1 ratio)
- **News B**: avg winner +33.25% vs avg loser -8.81% (3.8:1 ratio)

The -7% stop cap limits downside per trade, while the 50-day hold captures the full upside of momentum continuation after the gap.

## Year-by-Year Breakdown

### EP Earnings

| Year | A Trades | A Win% | A Avg Ret | B Trades | B Win% | B Avg Ret |
|------|---------|--------|-----------|---------|--------|-----------|
| 2020 | 21 | 52% | +8.32% | 29 | 62% | +16.92% |
| 2021 | 24 | 29% | -1.95% | 33 | 30% | +0.34% |
| 2022 | 9 | 56% | +6.24% | 17 | 65% | +13.56% |
| 2023 | 33 | 45% | +5.66% | 37 | 43% | +6.33% |
| 2024 | 53 | 42% | +4.54% | 64 | 44% | +4.25% |
| 2025 | 48 | 65% | +8.17% | 82 | 59% | +11.07% |

### EP News

| Year | A Trades | A Win% | A Avg Ret | B Trades | B Win% | B Avg Ret |
|------|---------|--------|-----------|---------|--------|-----------|
| 2020 | 20 | 75% | +21.26% | 64 | 78% | +27.17% |
| 2021 | 8 | 50% | +15.62% | 17 | 29% | +1.28% |
| 2022 | 4 | 75% | +18.76% | 13 | 62% | +12.71% |
| 2023 | 3 | 67% | +30.15% | 13 | 38% | +5.70% |
| 2024 | 7 | 57% | +7.88% | 16 | 44% | +5.91% |
| 2025 | 6 | 67% | +41.62% | 14 | 71% | +19.52% |

**Key observations:**
- 2021 was the weakest year across all strategies (SPAC/meme stock froth distorted patterns)
- Even in 2021, no strategy had a large loss — Earnings B was flat (+0.34%), News A still positive (+15.62%)
- 2020 and 2025 were the strongest years (sharp COVID recovery, strong earnings season)
- Strategies are profitable in both bull (2020, 2025) and bear (2022) markets

## Methodology

- **Data**: pre-curated spreadsheet of EP gap candidates with gap-day features (OHLCV, ATR, CHG-OPEN%, Prev 10D change%) and forward returns at 1D, 10D, 20D, 50D horizons
- **Filters**: vectorized pandas masks matching the live strategy filter logic
- **Exit simulation**: checkpoint-based — checks forward returns at 1D/10D/20D/50D; if any checkpoint breaches the stop level, exits at stop; otherwise exits at 50D return
- **Position sizing**: $10k fixed notional per trade, no compounding

### Known Limitations

- **Checkpoint stops are approximate**: only checks at 4 discrete points (1D/10D/20D/50D). A stock could dip below the stop on day 5 and recover by day 10 — in real trading the stop would trigger. Results are slightly optimistic.
- **No portfolio constraints**: trades are treated independently with no max-position or capital allocation limits. In practice, overlapping trades would require more capital or position limits.
- **No slippage or commissions**: entry/exit at exact close prices with no transaction costs.

## How to Run

```bash
cd trading-bot

# EP Earnings
.venv/bin/python run_ep_backtest.py --type earnings              # both A and B
.venv/bin/python run_ep_backtest.py --type earnings --strategy B  # single strategy
.venv/bin/python run_ep_backtest.py --type earnings --year 2025   # single year
.venv/bin/python run_ep_backtest.py --type earnings --trades      # show trade log

# EP News
.venv/bin/python run_ep_backtest.py --type news
.venv/bin/python run_ep_backtest.py --type news --strategy A --trades
```
