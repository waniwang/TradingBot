# Daily Verification Playbook

AI-assisted review process for the trading bot's daily execution. Use this playbook after running `verify_day.py` to perform judgment-based checks that go beyond what the automated script covers.

## Workflow

1. Run the script: `cd trading-bot && .venv/bin/python verify_day.py`
2. Paste the output to Claude and say: **"Review yesterday's bot results"**
3. Claude follows this playbook step by step
4. Claude provides analysis, flags issues, suggests actions

---

## Step 1: Triage Automated Results

- Review the **PASS/FAIL/WARN** summary from `verify_day.py`
- Any **FAIL** items are immediate action items — investigate root cause before continuing
- **WARN** items need judgment:
  - 0 candidates / 0 signals may be normal on quiet days
  - Fill slippage warnings are informational — flag only if pattern persists
  - ERROR log lines: transient (network timeout) vs systemic (code bug)
- **SKIP** items mean data wasn't available — verify why (no trades that day? missing logs?)

## Step 2: Review Scanner Quality

*Judgment call — the script provides the raw data, you interpret it.*

- Look at the **Watchlist Summary**: do the tickers make sense for their setup type?
- **Episodic Pivot candidates**: Was there actual news/earnings? Quick web search each gapper to confirm a real catalyst exists
- **Breakout candidates**: Are these stocks that have been consolidating after a big move? Or random low-volume names?
- **Spot-check for missed movers**: Were there obvious market movers that the scanner should have caught? Check:
  - Finviz premarket gainers for the date
  - Any high-profile earnings that day
  - Sector rotation plays (e.g., chip stocks rallying on NVDA earnings)
- If the scanner consistently misses obvious candidates, flag for parameter tuning

## Step 3: Review Signal Quality

*For each signal in the "Signals Fired" table:*

- **Entry level**: Was the entry at or near the Opening Range High (for longs)? Or did it fire too far above ORH (chasing)?
- **Timing**: Did the signal fire within the first 30-60 minutes? Or late in the day?
- **Volume**: Was there real volume on the breakout bar? (Check intraday chart if available)
- **Gap signals (EP)**: Was the gap filled and then recovered, or did it fire on the initial gap-up?

*For signals NOT acted on:*
- Why wasn't it acted on? (Max positions reached? Risk manager blocked? Check the log warnings)

*For watchlist items with no signal:*
- Did the stock actually break out but the signal engine missed it?
- Or did it correctly not trigger (stock didn't meet criteria)?

## Step 4: Review Trade Execution

- **Fill prices**: Compare limit prices to fill prices in the Orders table
  - Acceptable slippage: < 0.5% for liquid names, < 1% for small caps
  - Pattern of consistently bad fills suggests order type or timing issues
- **Order timing**: Was the entry order placed promptly after signal fired? (Compare signal fired_at vs order created_at)
- **Cancelled/rejected orders**: Check 20 (`Unfilled limits`) runs a 1-minute bar postmortem for every cancelled/rejected limit — it tells you whether the stock ever touched the limit price in the 90s fill-wait window. "NEVER touched limit" means the passive limit was unreachable (strategy bought the wrong level); "touched limit" but still unfilled means a broker/timing issue worth digging into
- **Stop orders**: Were GTC stop orders placed immediately after entry fills?

## Step 5: Review Exits

*For each position in "Positions Closed":*

- **Stop hits** (`stop_hit`):
  - Was the stop level correct per strategy rules (LOD or ATR-based)?
  - Was the stop too tight (normal volatility hit it) or too loose (took more loss than intended)?
  - Stop slippage: was fill close to stop price? (>2% slippage is concerning)

- **Trailing MA close** (`trailing_ma_close`):
  - Did the stock actually close below the 10d SMA? (automated check #15 verifies this)
  - Was this the right call? Did the stock recover the next day? (check the next day's price)
  - Is the MA period (10d) appropriate for the current market regime?

- **Partial exits**:
  - Was the +15% gain threshold met?
  - Was 40% of the position sold as intended?
  - Was stop moved to break-even after partial?

- **Positions that should NOT have been exited**: Any premature exits?
- **Positions that SHOULD have been exited but weren't**: Any open positions with clear breakdown signals that the monitor missed?

## Step 6: Review Risk Management

- **Portfolio heat**: Total exposure as % of portfolio
  - Sum of all open position notionals / portfolio value
  - Should generally be < 60% (4 positions * 15% max each)
- **Per-trade risk**: Each position's `shares * |entry - stop|` should be ~1% of portfolio
  - If any trade risked significantly more or less, investigate
- **Position sizing**: No single position > 15% of portfolio notional
- **Concentration**: Are all positions in the same sector/theme?
  - 3/4 positions in tech stocks = high concentration risk
  - Not a hard rule, but flag it for awareness

## Step 7: Market Context

*This context helps interpret whether the bot's behavior was appropriate for the day.*

- **Broad market**: What did SPY/QQQ do?
  - Strong trend day: expect more signals, higher win rate
  - Choppy/range day: expect false breakouts, lower win rate
  - Down day: long-biased bot should have fewer entries
- **Volatility**: Was VIX elevated? (Higher VIX = wider stops needed, fewer entries expected)
- **Unusual events**: Fed meeting, CPI data, earnings season peak, options expiration?
- **Bot P&L vs market**: On any single day, correlation to SPY should be low
  - If the bot lost money on a strong up day, that's worth investigating
  - If the bot made money on a choppy day, the strategy is working

## Step 8: Conclusions & Action Items

Summarize findings in three categories:

### Working as Expected
- List things that went right (good scanner picks, correct signal timing, proper risk management)

### Issues Found
- Specific problems with evidence (e.g., "Scanner missed TSLA earnings gap-up" or "Stop on NVDA was 5% from entry instead of expected 2%")
- Classify as: **bug** (code fix needed), **parameter** (config tuning), or **operational** (process issue)

### Action Items
- Code changes needed (with file paths and description)
- Parameter adjustments to consider (with rationale)
- Things to monitor going forward (e.g., "Watch if fill slippage continues above 1%")

---

## Diagnostic: Where Trades Get Blocked

If the bot is not generating trades, check these areas in order of likelihood:

1. **Consolidation scanner too strict (BO)** — Requiring all 6 conditions (prior move + ATR contraction + higher lows + near 10d MA + near 20d MA + duration) simultaneously leaves very few candidates. Most real consolidations fail 1-2 of these.

2. **RVOL thresholds (both setups)** — 2.0x for EP and 1.5x for BO, normalized to time-of-day. Early in the day, this can be noisy. A stock might have 1.4x RVOL at 9:36 and 2.1x by 9:40 but by then it's past the extension guard.

3. **Extension guards (both setups)** — 3% for BO and 5% for EP above ORH. If the ORH from the first 5 minutes is tight (low-range open), a strong move can blow past the extension guard before the bot checks.

4. **10% gap minimum (EP)** — Many quality EP setups gap 5-8%. A stock that gaps 7% on a great earnings beat with huge volume gets filtered out.

5. **Max 4 positions** — If 4 positions are open and none have exited yet, all new signals are blocked.

6. **Prior-rally filter (EP)** — Removes stocks already up 50% in 6 months. This filters out the strongest leaders that Qullamaggie often trades.

---

## Parameter Tuning Reference

All parameters below are configurable in `config.yaml` under the `signals:` section — no code changes needed.

| Parameter | Current | More permissive option | Effect |
|-----------|---------|----------------------|--------|
| `ep_min_gap_pct` | 10% | 7% | Admits more EP candidates |
| `ep_volume_multiplier` | 2.0x | 1.5x | Easier to trigger EP entries |
| `breakout_volume_multiplier` | 1.5x | 1.2x | More breakout entries fire |
| `breakout_max_extension_pct` | 3% | 5% | Wider window to catch breakouts |
| `consolidation_prior_move_pct` | 30% | 20% | Admits lower-beta stocks |
| `consolidation_atr_ratio` | 0.95 | 1.0 | Accepts any ATR contraction |
| `consolidation_ma_tolerance_pct` | 3% | 5% | Looser MA proximity check |
| Prior 6-month rally filter (EP) | 50% | 80% or remove | Admits strong leaders |

---

## Reference: Exit Reasons

| Exit Reason | Description | What to Check |
|-------------|-------------|---------------|
| `stop_hit` | Stop-loss order triggered | Was stop level correct? Slippage acceptable? |
| `trailing_stop` | Trailing stop tightened and hit | Was the tightened level appropriate? |
| `trailing_ma_close` | Daily close below trailing MA | Did stock actually close < SMA? Was it the right call? |
| `parabolic_target` | Profit target hit (10d/20d MA) | Was target level correct? |
| `manual` | Manual intervention | Why was manual exit needed? |
| `daily_loss_limit` | Daily loss limit triggered | Were the losses legitimate or from bad stops? |

---

## Reference: Automated Check Numbers

| # | Check | Pass Criteria |
|---|-------|---------------|
| 1 | Bot ran all phases | PRE-MARKET SCAN + EOD TASKS in logs |
| 2 | No critical errors | No CRITICAL/UNPROTECTED log lines |
| 3 | Scanner found candidates | > 0 watchlist items |
| 4 | Signals fired | > 0 signals |
| 5 | Entry prices valid | Entry within day's high/low |
| 6 | Order-broker sync | DB status matches Alpaca |
| 7 | Fill price slippage | Fill within 1% of limit |
| 8 | Position-broker sync | DB positions = broker positions |
| 9 | All stops in place | Every open position has active stop |
| 10 | Stop prices match | DB stop = broker stop |
| 11 | daily_pnl exists | Record for date |
| 12 | Realized P&L math | Closed positions sum = daily realized |
| 13 | Per-trade P&L math | (exit-entry)*shares = realized_pnl |
| 14 | Stop exit slippage | Stop fills within 2% of stop price |
| 15 | MA-close exit valid | Close was actually < SMA on exit date |
| 16 | Risk per trade | Risk ~1% of portfolio |
| 17 | Position sizing | Notional <= 15% of portfolio |
| 18 | Max positions | Never > 4 concurrent |
