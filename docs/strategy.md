# Trading Strategy

## Source & Inspiration

- **Trader**: Kristjan Kullamagi ("qullamaggie") — Swedish momentum trader, reportedly made $100M+ trading momentum stocks
- **Primary reference**: https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/
- **Additional reading**:
  - https://qullamaggie.com (full blog)
  - Twitter/X: @Qullamaggie
  - *How to Trade in Stocks* by Jesse Livermore (philosophical foundation)

---

## Core Philosophy

- Trade the **top 1-2% strongest stocks** in the market at any given time
- Buy setups with defined risk and large asymmetric upside (target 30-50x R on best setups)
- **Small losses, big winners** — cut fast when wrong, let winners run
- Position sizing and stop discipline matter more than win rate
- Trade **equities only** (shares) — no options, no futures, no leverage

---

## Setup 1: Breakout

### What it is
A stock that had a big move (weeks to months), consolidated in a tight range, and is now breaking out again.

### Screening criteria
- Top 1-2% of stocks by 1-month, 3-month, and 6-month relative performance
- Stock had a **large initial move (at least 30%+ from the base)** in the ~2 months before consolidation
- Currently in a **2-8 week tight consolidation** (10-40 trading days, enforced)

### Setup conditions
- Price is "surfing" **both the 10-day AND 20-day moving averages** (within 3% tolerance)
- Higher lows during the consolidation
- ATR (Average True Range) contracting — range getting tighter (ATR ratio < 0.95)
- Volume drying up during consolidation (bullish)

### Entry
- Pre-market: set alert at the high of the most recent 1m, 5m, or 60m opening range
- Enter as price breaks above the opening range high (ORH) on volume (> 1.5x 20d avg)
- Use limit orders, not market orders

### Stop
- Initial: **Low of Day (LOD)** at time of entry
- **Capped at 1x ATR(14)** — if LOD is wider than 1 ATR, stop is tightened to `entry - ATR`
- Typically 3-8% below entry depending on setup tightness

### Exit rules
1. After 3-5 days in trade AND price up 15-20%: sell 40% of position
2. Move stop to break-even after partial exit
3. Trail remaining shares: exit on **first daily close below the 10-day MA** (not an intraday touch — stock can dip below MA and recover without triggering exit)
4. Hard cut if thesis breaks (gap down, heavy volume selling)

### Risk/reward target
10-50x R on the best setups

---

## Setup 2: Episodic Pivot (EP)

### What it is
A previously neglected stock gets a surprise positive catalyst (earnings beat, FDA approval, major contract, regulatory change) and gaps up 10%+ with huge volume. This creates a new uptrend from scratch.

### Screening criteria
- Stock gaps up 10%+ after close / premarket
- Catalyst is *unexpected* (analysts missed it, stock was overlooked)
- Heavy premarket volume (ideally top 10 most active premarket)
- For earnings: triple-digit EPS + revenue growth beats
- **Stock should NOT have already rallied 50%+ in prior 3-6 months** (less surprise factor) — enforced via prior-rally filter in gapper scanner

### Setup conditions
- Big volume in premarket or within first 15-30 minutes of open (> 2x 20d avg)
- Stock holding premarket highs / not fading sharply

### Entry
- Identify candidates in after-hours or premarket
- Enter on opening range high (ORH) using 1m or 5m candle after 9:30 AM
- Alternatively: enter 15-30 minutes into open if still showing strength

### Stop
- **Low of day (LOD)** at time of entry
- **Capped at 1.5x ATR(14)** — if LOD is wider than 1.5 ATR, stop is tightened to `entry - 1.5 * ATR`
- Typically 3-10% below entry (EPs can be volatile)

### Exit rules
- Same as Breakout: partial exit after 3+ days if gain >= 15%, then trail with 10d MA daily close
- Often faster movers — be ready to take partial profits earlier (day 2-3)

### Risk/reward target
5-30x R

---

## Setup 3: Parabolic Short

### What it is
A stock that has gone parabolic (extreme move in very short time) and is now showing signs of exhaustion. Short it for a mean-reversion back to the moving averages.

### Screening criteria
- **Large-cap (price > $50)**: up 50%+ in days to a few weeks
- **Small-cap (price < $20)**: up 200%+ (interpolated for mid-range prices)
- Stock up 3-5+ consecutive days with accelerating candles

### Entry (short)
- Wait for first sign of exhaustion: opening range *low* (ORB low) forms on 1m/5m
- Short on VWAP failure (price bounces to VWAP then fails to reclaim)
- Define stop at day's high or VWAP reclaim level

### Stop
- Above day's high OR above VWAP reclaim level
- Tight stops — if VWAP is reclaimed cleanly, cut immediately

### Exit (cover) — profit targets
- **10-day MA**: cover 50% of position when price reaches the 10d MA from above
- **20-day MA**: cover remaining position when price reaches the 20d MA
- Risk/reward target: 5-10x R (lower than longs but higher win rate)

### Implementation note
This is a *short* setup — requires margin account and short-locate access.
**Shorts are implemented via Alpaca. Ensure margin account is enabled.**

---

## Opening Range Definitions

| Timeframe | ORH / ORB definition |
|---|---|
| 1m ORH | High of first 1-minute candle (9:30-9:31) |
| 5m ORH | High of first 5-minute candle (9:30-9:35) — **default** |
| 60m ORH | High of first 60-minute candle (9:30-10:30) |

- **ORH** = Opening Range High (used for long entries)
- **ORB low** = Opening Range Low (used for short entries)

## No-trade window
- No entries in the first 5 minutes (9:30-9:35 ET) — let ORH/ORB form first
