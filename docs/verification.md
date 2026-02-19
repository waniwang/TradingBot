# Verification Plan

---

## Unit Tests

### Signal tests (`tests/test_signals.py`)
Each signal module tested with synthetic OHLCV fixtures from `tests/fixtures/`.

| Test case | Fixture | Expected result |
|---|---|---|
| Breakout fires on valid setup | `breakout_setup.csv` | `SignalResult` with correct entry/stop |
| Breakout does not fire below 20d MA | modified fixture | `None` |
| Breakout does not fire without volume | modified fixture | `None` |
| EP fires on 10%+ gap + volume | `ep_setup.csv` | `SignalResult` |
| EP does not fire on gap < 10% | modified fixture | `None` |
| Parabolic fires on ORB break + VWAP fail | `parabolic_setup.csv` | `SignalResult` (side=SHORT) |
| ORH computed correctly from 5m window | inline data | correct float |

### Risk manager tests (`tests/test_risk.py`)

| Test case | Expected result |
|---|---|
| Position size formula (basic case) | `floor(1000/3.50) = 285` |
| Position size capped by 10% notional | 192 shares (not 285) |
| Exposure check passes when < 4 positions | `True` |
| Exposure check blocks at 4 positions | `False` |
| Daily loss check passes at -2.5% | `True` |
| Daily loss check blocks at -3.1% | `False` |
| Weekly loss check blocks at -5.2% | `False` |

### Executor tests (mock futu-api)

| Test case | Verified |
|---|---|
| `place_limit_order` sends correct params to futu | order_id returned |
| `place_stop_order` sends correct params | order_id returned |
| `cancel_order` calls correct futu method | called once with right id |
| `close_position` uses market order type | `OrderType.MARKET` verified |

---

## Backtesting Targets

Run against 2022–2024 daily OHLCV for S&P 1500 (Polygon.io).

| Metric | Target |
|---|---|
| Win rate | > 45% |
| Avg winner / avg loser ratio | > 3x |
| Sharpe ratio | > 1.0 |
| Max drawdown | < 20% |
| Profit factor | > 2.0 |

Hold out 2024 data as out-of-sample to prevent overfitting. Walk-forward test across 2022–2023.

---

## Paper Trading Checklist

Run `environment: simulate` for 3-4 weeks. Verify each item:

### Signal & Entry
- [ ] EP signal fires correctly the morning after a real earnings gap-up
- [ ] Breakout signal fires on valid ORH break with volume confirmation
- [ ] No signals fire in the first 5 minutes (before 9:35 AM ET)
- [ ] No entries placed if 4 positions already open

### Stop Placement
- [ ] Stop order placed within 5 seconds of fill confirmation
- [ ] Stop price matches the correct level per setup type
- [ ] Stop order is on the correct side (sell stop for long, buy stop for short)

### Partial Exit
- [ ] Partial exit fires automatically at day 3+ when gain ≥ 15%
- [ ] Correct fraction of shares sold (40%)
- [ ] Stop moves to break-even after partial exit

### Trailing Stop
- [ ] Trailing stop updates at 4:00 PM ET with correct MA level
- [ ] Trailing stop never moves further away from entry
- [ ] Position closes correctly when price closes below 10d MA

### Risk Controls
- [ ] Daily loss limit halts all trading correctly
- [ ] Weekly loss limit halts correctly
- [ ] Bot resumes next day / next week after halt (not permanently halted)

### Infrastructure
- [ ] Reconnect handler recovers from OpenD disconnect without data loss
- [ ] Telegram alerts arrive within 30 seconds for all event types
- [ ] Dashboard shows correct live position data
- [ ] Manual flatten from dashboard closes position correctly
- [ ] Edge case: no fills (order timeout, thin liquidity) handled gracefully
- [ ] Edge case: early market close (e.g., day before Thanksgiving) handled
- [ ] Edge case: trading halt on a position symbol handled

---

## Pre-Live Checklist

- [ ] All unit tests passing (`pytest tests/`)
- [ ] Backtests show positive expectancy (all metrics above targets)
- [ ] 3+ weeks paper trading with no critical bugs
- [ ] Paper P&L aligned with backtest expectations (within reason)
- [ ] Config reviewed: correct Moomoo account, `environment: real`
- [ ] Risk params set conservatively: `risk_per_trade_pct: 0.5`, `max_positions: 2`
- [ ] Kill switch tested: manual flatten from dashboard closes position in Moomoo
- [ ] OpenD watchdog process confirmed running
- [ ] Telegram bot confirmed active and responsive
- [ ] Database backup procedure in place
