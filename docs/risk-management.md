# Risk Management

## Position Sizing Formula

```
shares = floor((portfolio_value × risk_pct) / (entry_price - stop_price))
```

**Parameters:**
- `risk_pct` = 1.0% (configurable in `config.yaml` as `risk.risk_per_trade_pct`)
- Round **down** to whole shares (never round up — never exceed risk budget)
- **Cap 1**: position notional ≤ 10% of portfolio (`risk.max_position_pct`)
- **Cap 2**: total open positions ≤ 4 (`risk.max_positions`)

### Example Calculation

| Parameter | Value |
|---|---|
| Portfolio | $100,000 |
| Entry price | $52.00 |
| Stop price | $48.50 |
| Risk per share | $3.50 |
| Max risk (1%) | $1,000 |
| Raw shares | floor($1,000 / $3.50) = **285 shares** |
| Notional | 285 × $52 = $14,820 (14.8% of portfolio) |
| After 10% cap | floor($10,000 / $52) = **192 shares** |

The 10% portfolio cap overrides the R-based size here — use 192 shares.

---

## Stop Loss Levels by Setup

| Setup | Initial Stop | After Partial Exit | Trailing Stop |
|---|---|---|---|
| Breakout | Below consolidation base low | Break-even (entry price) | 10d MA daily close |
| EP | Low of day (LOD) at entry time | Break-even (entry price) | 10d or 20d MA daily close |
| Parabolic Short | Above day's high / VWAP reclaim | Break-even (entry price) | 10d MA from below |

### Stop distance by setup (typical ranges)
- Breakout: 3-8% below entry
- EP: 3-10% below entry (EPs more volatile)
- Parabolic Short: 2-5% above short entry

---

## Partial Exit Rules

| Trigger | Action |
|---|---|
| Days in trade ≥ 3 **AND** price up ≥ 15% | Sell `partial_exit_fraction` (default 40%) of position as limit order |
| After partial exit | Move stop to break-even (entry price) |
| After partial exit | Trail remaining position with 10d MA |

Config params:
- `exits.partial_exit_after_days`: 3
- `exits.partial_exit_gain_threshold_pct`: 15.0
- `exits.partial_exit_fraction`: 0.40
- `exits.trailing_ma_period`: 10

---

## Hard Rules

1. **Never move a stop further away from entry** — only tighten or move to break-even
2. **Daily loss limit**: if daily loss > 3% of portfolio, halt all trading for the rest of the day
3. **Weekly loss limit**: if weekly loss > 5% of portfolio, halt for the rest of the week and review setups
4. **Maximum 4 concurrent positions** at all times
5. **No trading in the first 5 minutes** (9:30–9:35 ET) — let the opening range form
6. **No market orders** except for emergency exits

---

## Daily / Weekly Loss Halt Logic

```python
# Pseudo-code
daily_pnl = sum(realized_pnl_today) + sum(unrealized_pnl_open_positions)
if daily_pnl / portfolio_value < -0.03:
    halt_trading(reason="daily loss limit")
    send_telegram_alert("Daily loss limit hit. Trading halted for the day.")

weekly_pnl = sum(realized_pnl_this_week) + unrealized
if weekly_pnl / portfolio_value < -0.05:
    halt_trading(reason="weekly loss limit")
    send_telegram_alert("Weekly loss limit hit. Trading halted for the week.")
```

---

## Exposure Checks Before Each Entry

Before placing any new entry order, the risk manager must verify:

1. `open_positions < max_positions` (default: 4)
2. `new_position_notional / portfolio_value <= max_position_pct` (default: 10%)
3. `daily_loss_pct > -daily_loss_limit_pct` (default: -3%)
4. `weekly_loss_pct > -weekly_loss_limit_pct` (default: -5%)
5. `current_time > 9:35 AM ET` (no trading in first 5 minutes)

If any check fails, log the reason and skip the entry.
