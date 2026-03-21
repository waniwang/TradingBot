# Qullamaggie Trading Bot

Automated momentum trading bot inspired by Kristjan Kullamagi ("qullamaggie") — Swedish momentum trader who reportedly made $100M+ trading momentum stocks.

**Philosophy:** Trade the top 1-2% strongest stocks in the market. Small defined risk, asymmetric upside. Small losses, big winners — cut fast when wrong, let winners run. Position sizing and stop discipline matter more than win rate. Equities only (shares) — no options, no futures, no leverage.

**Source:** [qullamaggie.com — My 3 Timeless Setups](https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/)

---

## Trading Strategy

### Setup 1: Episodic Pivot (EP) — Gap-Up Catalyst Play

A previously neglected stock gets a surprise positive catalyst (earnings beat, FDA approval, major contract) and gaps up 10%+ with huge volume. This creates a new uptrend from scratch.

**Premarket Scan (6:00 AM ET):**

| Filter | Threshold | What it means |
|--------|-----------|---------------|
| Overnight gap | >= 10% | Must gap up at least 10% from prior close |
| Price | > $5 | No penny stocks |
| Premarket volume | >= 100,000 shares | Must have liquidity |
| Prior 6-month rally | < 50% | Rejects stocks already up 50%+ (less surprise factor) |
| Ticker format | <= 5 chars, letters only | Filters out warrants, units, etc. |

**Intraday Signal (9:35 AM+ ET) — all must be true:**

| Condition | Threshold | Detail |
|-----------|-----------|--------|
| Price > ORH | — | Price breaks above high of first 5 minutes (Opening Range High) |
| Extension guard | <= 5% above ORH | If price already ran 5%+ past ORH, skip (anti-chase) |
| RVOL | >= 2.0x | Time-of-day-normalized volume must be 2x the 20-day average |
| ATR stop cap | Stop width <= 1.5x ATR(14) | If low-of-day is too far below entry, stop capped at 1.5x ATR |

**Stop:** Low of day at time of entry, capped at 1.5x ATR(14). Typically 3-10% below entry.

**Risk/reward target:** 5-30x R.

---

### Setup 2: Breakout (BO) — Consolidation Breakout

A stock that had a big move (30%+), consolidated in a tight range near its moving averages, and is now breaking out again.

**Nightly Scan (5:00 PM ET) — consolidation detection:**

The bot ranks the top ~1,500 stocks by momentum (RS composite: 50% 1-month, 30% 3-month, 20% 6-month), takes the top 20, then checks each for consolidation quality.

| Filter | Threshold | What it means |
|--------|-----------|---------------|
| Prior move | >= 30% advance (low to high) | Must have had a 30%+ move in ~2 months before consolidation |
| Consolidation duration | 10-40 trading days | Must be consolidating 2-8 weeks |
| ATR contraction | Recent ATR / Older ATR < 0.95 | Range is getting tighter |
| Higher lows | Positive slope on lows | Lows during consolidation trending upward |
| Near 10-day MA | Within 3% of 10d SMA | Price hugging the 10-day moving average |
| Near 20-day MA | Within 3% of 20d SMA | Price also near the 20-day moving average |

**Intraday Signal (9:35 AM+ ET):**

| Condition | Threshold | Detail |
|-----------|-----------|--------|
| Price > ORH | — | Price breaks above high of first 5 minutes |
| Extension guard | <= 3% above ORH | Tighter than EP — skip if 3%+ past ORH (anti-chase) |
| Price > 20d MA | — | Must be above 20-day SMA |
| RVOL | >= 1.5x | Time-of-day-normalized volume must be 1.5x the 20-day average |
| ATR stop cap | Stop width <= 1x ATR(14) | Tighter stop cap than EP |

**Stop:** Low of day at time of entry, capped at 1x ATR(14). Typically 3-8% below entry.

**Risk/reward target:** 10-50x R on the best setups.

---

### Setup 3: Parabolic Short (DISABLED)

A stock that has gone parabolic (up 50-200%+ in days) showing signs of exhaustion. Short for mean-reversion back to moving averages.

**Status: Disabled** — negative expectancy in 6-year backtest (Sharpe -0.39 OOS). Not currently scanned or traded.

---

### Opening Range Definitions

| Timeframe | ORH / ORB definition |
|---|---|
| 1m ORH | High of first 1-minute candle (9:30-9:31) |
| 5m ORH | High of first 5-minute candle (9:30-9:35) — **default** |
| 60m ORH | High of first 60-minute candle (9:30-10:30) |

- **ORH** = Opening Range High (used for long entries)
- **ORB low** = Opening Range Low (used for short entries)
- **No-trade window:** 9:30-9:35 AM ET — no entries in the first 5 minutes; let ORH/ORB form first

---

## Risk Management

### Position Sizing

```
shares = floor(portfolio_value * 1% / |entry_price - stop_price|)
```

Then capped at 15% of portfolio notional: `max_shares = floor(portfolio * 15% / entry_price)`

**Worked example:**

| Parameter | Value |
|---|---|
| Portfolio | $100,000 |
| Entry price | $52.00 |
| Stop price | $48.50 |
| Risk per share | $3.50 |
| Max risk (1%) | $1,000 |
| Raw shares | floor($1,000 / $3.50) = **285 shares** |
| Notional | 285 x $52 = $14,820 (14.8% of portfolio) |
| After 15% cap | floor($15,000 / $52) = **288 shares** |
| **Final size** | **285 shares** (R-based size is binding here) |

### Hard Rules

- **Max 4 concurrent positions** at all times
- **Never move a stop further from entry** — only tighten or move to break-even
- **Daily loss limit:** if portfolio down 3% for the day, halt all new entries
- **Weekly loss limit:** if portfolio down 5% for the week, halt for the rest of the week
- **No market orders** except for emergency exits
- **No entries before 9:35 AM ET** — let the opening range form

---

## Exit Rules

| Exit Type | Condition | Action |
|-----------|-----------|--------|
| **Stop hit** | Price touches GTC stop order | Close full position |
| **Partial exit** | Held >= 3 days AND gain >= 15% | Sell 40% of position, move stop to break-even |
| **Trailing MA close** | Daily close below 10d SMA (after partial exit) | Close remaining position at EOD |
| **Trailing stop** | EOD each day | Stop raised to 10d MA level (never lowered) |
| **Parabolic targets** | Short reaches 10d MA / 20d MA | Cover 50% at 10d MA, rest at 20d MA |
| **Daily loss limit** | Portfolio down 3% for the day | Halt all trading, close no existing positions |

**Important:** Trailing MA close is a **daily close** check, not an intraday touch. A stock can dip below the MA during the day and recover without triggering an exit.

**Exit reason codes in DB:** `stop_hit`, `trailing_stop`, `trailing_ma_close`, `parabolic_target`, `manual`, `daily_loss_limit`

---

## Daily Bot Flow

```
Scanners (premarket)          Signals (market open)         Monitor (intraday + EOD)
├── gapper.py (EP)            ├── breakout.py               ├── stop checks
├── momentum_rank.py (BO)     ├── episodic_pivot.py         ├── partial exits (40% @ +15%)
├── consolidation.py (BO)     └── parabolic_short.py        ├── trailing MA close (10d)
└── parabolic.py (short)                                    └── parabolic targets (10d/20d MA)
         ↓                           ↓                              ↓
    Watchlist ──────────────→ Risk Manager ──────────────→ Alpaca Executor
                              (1% risk/trade,               (limit entries,
                               max 4 positions,              GTC stop orders)
                               15% max position)
```

**Daily timeline (all times Eastern):**

| Time | Job | What happens |
|------|-----|--------------|
| 5:00 PM (prior day) | Nightly watchlist scan | Heavy breakout scan: momentum rank 1,500 stocks via yfinance, consolidation analysis, persist to DB |
| 6:00 AM | Premarket scan | Alpaca screener for EP gappers, promote breakout candidates to active, refresh watchlist |
| 9:25 AM | Finalize watchlist | Pre-fetch 130 days of daily bars for all candidates, subscribe to Alpaca real-time data stream |
| 9:35 AM onwards | Signal evaluation | Every 1-min candle: check entry conditions, evaluate against risk rules, place limit orders on signal |
| Every 5 min | Reconcile positions | Poll broker for GTC stop fills, detect unprotected positions, sync DB with Alpaca state |
| 3:55 PM | EOD tasks | Trailing stop updates, MA-close exit checks, compute daily P&L, send Telegram summary |
| Every 30s | Heartbeat | Write bot_status.json for dashboard to read current phase and next job |

---

## Backtest Results

**6-year analysis (20 tickers, 2019-2024) — In-Sample vs Out-of-Sample:**

| Strategy | Period | Trades | Win% | Sharpe | CAGR% | MaxDD% | PF | Calmar |
|---|---|---|---|---|---|---|---|---|
| EP | IS | 13 | 30.8 | 0.41 | 2.4 | 6.1 | 1.78 | 0.39 |
| EP | OOS | 36 | 36.1 | 1.08 | 10.7 | 8.7 | 2.55 | 1.23 |
| Breakout | IS | 7 | 14.3 | 0.20 | 0.6 | 3.4 | 1.75 | 0.19 |
| Breakout | OOS | 8 | 12.5 | -0.03 | -0.2 | 3.8 | 0.88 | 0.05 |
| Parabolic Short | OOS | 6 | 33.3 | -0.39 | -1.0 | 5.8 | 0.24 | 0.18 |
| **Tuned Combined** | **OOS** | **42** | **30.9** | **1.29** | **18.1** | **9.3** | **3.29** | — |
| SPY buy&hold | OOS | — | — | 0.57 | 8.8 | 24.5 | — | 0.36 |

**Key findings:**
- EP is the strongest strategy (Sharpe 1.08 OOS, Calmar 1.23)
- Parabolic short is unprofitable — disabled
- Tuned combined beats SPY on Sharpe (1.29 vs 0.57) and max drawdown (9.3% vs 24.5%)

**Known limitations:** Survivorship bias (current S&P 500 list), daily bars only (entries approximated), no slippage or borrow costs assumed.

---

## Telegram Notifications

The bot sends Telegram alerts for all key events:

- **Bot started** — environment confirmation on startup
- **Premarket scan started/finished** — with watchlist summary
- **Nightly scan started/finished** — momentum rank + consolidation results
- **Watchlist ready** — candidate count and tickers by setup type
- **Entry order placed** — ticker, setup, side, shares, price, stop level
- **Entry filled** — actual fill price, risk per share
- **Stop filled** (via reconciliation) — exit price, P&L
- **Trading halted** — daily or weekly loss limit hit
- **Unprotected position** — critical alert if stop order fails
- **EOD summary** — date, P&L, realized, trades, portfolio value
- **Errors** — scan failures, stream subscription failures

---

## Getting Started

### Prerequisites

- Python 3.14+
- Alpaca account (paper trading is free: https://alpaca.markets)
- Telegram bot (optional — create via @BotFather)

### Setup

```bash
cd trading-bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

Edit `trading-bot/config.yaml` with your Alpaca API keys, or set environment variables:

```bash
export ALPACA_API_KEY=your_key
export ALPACA_SECRET_KEY=your_secret
export TELEGRAM_BOT_TOKEN=your_token    # optional
export TELEGRAM_CHAT_ID=your_chat_id    # optional
```

### Run locally

```bash
./bot.sh local start     # start bot + Streamlit dashboard
./bot.sh local status    # check health
./bot.sh local logs      # tail logs
./bot.sh local stop      # stop both
```

Dashboard: http://localhost:8501

### Run tests

```bash
cd trading-bot && .venv/bin/pytest tests/ -v
```

---

## Key Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `risk.risk_per_trade_pct` | 1.0 | % of portfolio risked per trade |
| `risk.max_positions` | 4 | Max concurrent open positions |
| `risk.max_position_pct` | 15.0 | Max single position as % of portfolio notional |
| `risk.daily_loss_limit_pct` | 3.0 | Halt trading if daily loss exceeds this % |
| `signals.ep_min_gap_pct` | 10.0 | Min overnight gap % for EP candidates |
| `signals.ep_volume_multiplier` | 2.0 | Min RVOL for EP entry |
| `signals.breakout_volume_multiplier` | 1.5 | Min RVOL for breakout entry |
| `signals.ep_max_extension_pct` | 5.0 | Max % above ORH before skipping (EP) |
| `signals.breakout_max_extension_pct` | 3.0 | Max % above ORH before skipping (breakout) |
| `strategies.enabled` | EP + breakout | Which setups to scan and trade |

See [docs/config-reference.md](docs/config-reference.md) for the full schema.

---

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/architecture.md](docs/architecture.md) | Tech stack, data flow, project structure, module reference, design decisions |
| [docs/config-reference.md](docs/config-reference.md) | Full config.yaml schema with all parameters |
| [docs/operations.md](docs/operations.md) | Bot operations: start/stop/deploy/verify/scan commands |
| [docs/backtesting.md](docs/backtesting.md) | Test plan, backtest procedures, results, paper trading checklist |
| [docs/daily-verification.md](docs/daily-verification.md) | Daily verification playbook for AI-assisted review |
| [docs/risks-and-mitigations.md](docs/risks-and-mitigations.md) | Known risks and how they are handled |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Phase-by-phase build plan with checklists |
