# Trading Bot Flow & Setup Thresholds

## Bot Pipeline Overview

The bot runs 4 phases each day, all in Eastern Time:

```
6:00 AM    SCAN (premarket)     → Build watchlist
9:25 AM    FINALIZE             → Lock in watchlist
9:35 AM+   SIGNAL (intraday)    → Check for entries every minute
3:55 PM    EOD                  → Trailing stop updates, MA-close exits, P&L
```

---

## Setup 1: Episodic Pivot (EP) — Gap-Up Catalyst Play

**What it is:** A stock gaps up big on a catalyst (earnings, news) and we buy the breakout above the opening range.

### Premarket Scan (6:00 AM) — "Does this stock qualify?"

| Filter | Threshold | What it means |
|--------|-----------|---------------|
| Overnight gap | **>= 10%** | Must gap up at least 10% from prior close |
| Price | **> $5** | No penny stocks |
| Premarket volume | **>= 100,000 shares** | Must have liquidity |
| Prior 6-month rally | **< 50%** | Rejects stocks already up 50%+ in 6 months (extended names) |
| Ticker length | **<= 5 chars, letters only** | Filters out warrants, units, etc. |

**Potential issue:** The 10% gap minimum is fairly high. Many tradable EPs gap 5-8%. The 50% prior-rally filter also removes stocks that have been strong leaders.

### Intraday Signal (9:35 AM+) — "Do we enter?"

All 4 conditions must be true simultaneously:

| Condition | Threshold | Detail |
|-----------|-----------|--------|
| Price > ORH | — | Price must break above the high of the **first 5 minutes** (Opening Range High) |
| Extension guard | **<= 5% above ORH** | If price has already run more than 5% past the ORH, skip it (anti-chase) |
| RVOL | **>= 2.0x** | Time-of-day-normalized volume must be 2x the 20-day average. At 9:35, this means 2x the volume normally expected in the first 5 min |
| ATR stop cap | Stop width **<= 1.5x ATR(14)** | If the low-of-day is too far below entry, the stop is capped at 1.5x the 14-day ATR |

**Stop:** Low of day at time of entry (capped at 1.5x ATR)

**Potential issues:**
- RVOL >= 2.0x is strict — many stocks break out with 1.3-1.5x volume early in the day
- The 5% extension guard is generous for EP (gap stocks can run), but the ORH is based on only the first 5 minutes, which can be a very tight range on gap days

---

## Setup 2: Breakout (BO) — Consolidation Breakout

**What it is:** A stock had a big prior move, consolidated in a tight range near its moving averages, and now breaks out with volume.

### Premarket Scan (6:00 AM) — "Is this stock in a valid consolidation?"

This is a multi-step filter. The bot first gets the top ~1,500 stocks by momentum ranking (RS composite score weighted 50% 1-month, 30% 3-month, 20% 6-month), takes the top 20, then checks each one for consolidation quality.

**Momentum universe filter:**

| Filter | Threshold |
|--------|-----------|
| Price | **>= $5** |
| 20-day avg volume | **>= 100,000** |

**Consolidation requirements (ALL must pass):**

| Filter | Threshold | What it means |
|--------|-----------|---------------|
| Prior move | **>= 30%** advance (low→high) | Must have had a 30%+ directional move in the ~2 months before consolidation |
| Consolidation duration | **10–40 trading days** | Must be consolidating 2–8 weeks |
| ATR contraction | Recent ATR / Older ATR **< 0.95** | The range is getting tighter — recent half of the consolidation must have ATR below 95% of the first half |
| Higher lows | Positive slope on lows | The lows during consolidation must be trending upward (linear regression) |
| Near 10-day MA | Within **3%** of 10d SMA | Price must be hugging the 10-day moving average |
| Near 20-day MA | Within **3%** of 20d SMA | Price must also be near the 20-day moving average |

**Potential issues:**
- Requiring ALL 6 conditions simultaneously is very strict. A stock that meets 5 of 6 gets rejected entirely.
- The 30% prior move requirement filters out lower-beta stocks that make solid 15-20% moves then tighten up.
- ATR ratio < 0.95 means recent ATR must be strictly less than 95% of the earlier ATR — borderline contractions (ratio = 0.96) get rejected.
- Requiring price within 3% of BOTH the 10d and 20d MA is tight. If the stock just bounced off the 20d MA, it may be 4% above it.

### Intraday Signal (9:35 AM+) — "Do we enter?"

| Condition | Threshold | Detail |
|-----------|-----------|--------|
| Price > ORH | — | Price must break above the high of the **first 5 minutes** |
| Extension guard | **<= 3% above ORH** | Tighter than EP — if it's already run 3% past the ORH, skip (anti-chase) |
| Price > 20d MA | — | Current price must be above the 20-day SMA |
| RVOL | **>= 1.5x** | Time-of-day-normalized volume must be 1.5x the 20-day average |
| ATR stop cap | Stop width **<= 1x ATR(14)** | Stop is capped at 1x ATR (tighter than EP) |

**Stop:** Low of day at time of entry (capped at 1x ATR)

**Potential issues:**
- 3% extension guard is tight for breakouts — a strong breakout can clear the ORH by 3% within minutes
- RVOL 1.5x is reasonable but combined with the 3% extension guard means you have a very narrow window to catch the move

---

## Risk Management — "Can we take this trade?"

Even if a signal fires, the risk manager gates every entry:

| Check | Threshold | Effect |
|-------|-----------|--------|
| Trading window | **After 9:35 AM ET** | No entries in the first 5 minutes |
| Max open positions | **4** | Can't enter a 5th trade until one closes |
| Max single position size | **15% of portfolio** | No position can exceed 15% notional |
| Risk per trade | **1% of portfolio** | Position sized so that if stopped out, you lose ~1% of portfolio |
| Daily loss limit | **-3%** | If down 3% on the day, halt all new entries |
| Weekly loss limit | **-5%** | If down 5% for the week, halt all new entries |

**Position sizing formula:**
```
Shares = floor(Portfolio × 1% / (Entry - Stop))
Then capped at: floor(Portfolio × 15% / Entry)
```

---

## Exit Rules — "When do we get out?"

| Exit Type | Condition | Action |
|-----------|-----------|--------|
| **Stop hit** | Price touches stop (checked every minute) | Close full position |
| **Partial exit** | Held **>= 3 days** AND gain **>= 15%** | Sell **40%** of position, move stop to **break-even** |
| **Trailing MA close** | After partial exit, daily close below **10-day SMA** | Close remaining position at EOD |
| **Trailing stop ratchet** | EOD each day | Stop is raised to the 10d MA level (never lowered) |

---

## Where Trades Are Likely Getting Blocked

Given that most trades are not executing, here's where to look, ranked by likelihood:

1. **Consolidation scanner too strict (BO)** — Requiring all 6 conditions (prior move + ATR contraction + higher lows + near 10d MA + near 20d MA + duration) simultaneously leaves very few candidates. Most real consolidations fail 1-2 of these.

2. **RVOL thresholds (both setups)** — 2.0x for EP and 1.5x for BO, normalized to time-of-day. Early in the day, this can be noisy. A stock might have 1.4x RVOL at 9:36 and 2.1x by 9:40 but by then it's past the extension guard.

3. **Extension guards (both setups)** — 3% for BO and 5% for EP above ORH. If the ORH from the first 5 minutes is tight (low-range open), a strong move can blow past the extension guard before the bot checks.

4. **10% gap minimum (EP)** — Many quality EP setups gap 5-8%. A stock that gaps 7% on a great earnings beat with huge volume gets filtered out.

5. **Max 4 positions** — If 4 positions are open and none have exited yet, all new signals are blocked.

6. **Prior-rally filter (EP)** — Removes stocks already up 50% in 6 months. This filters out the strongest leaders that Qullamaggie often trades (stocks that keep making new highs).

### Suggested parameters to consider loosening

| Parameter | Current | More permissive option |
|-----------|---------|----------------------|
| `ep_min_gap_pct` | 10% | 7% |
| `ep_volume_multiplier` | 2.0x | 1.5x |
| `breakout_volume_multiplier` | 1.5x | 1.2x |
| `breakout_max_extension_pct` | 3% | 5% |
| `consolidation_prior_move_pct` | 30% | 20% |
| `consolidation_atr_ratio` | 0.95 | 1.0 (any contraction at all) |
| `consolidation_ma_tolerance_pct` | 3% | 5% |
| Prior 6-month rally filter (EP) | 50% | 80% or remove |

These are all configurable in `config.yaml` under the `signals:` section — no code changes needed.
