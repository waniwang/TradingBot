# Parabolic Short Strategy

> **DISABLED** — Negative expectancy in 6-year backtest (Sharpe -0.39 OOS). Kept for reference.

Short setup on overextended multi-day runners. Scans pre-market, trades intraday with custom exit logic.

## Flow

1. **6:00 AM** — `scanner.py` finds multi-day parabolic runners
2. **9:30 AM** — `signal.py` watches for ORB low break + VWAP failure
3. **Ongoing** — `exits.py` custom profit targets at 10d/20d MA

## Scanner Filters (`scanner.py`)

| Filter | Threshold |
|--------|-----------|
| Price | > $5 |
| Multi-day gain | Large-cap (>$50): >= 50%, Small-cap (<$20): >= 200%, interpolated between |
| Consecutive up days | >= 3 |

## Entry Signal (`signal.py`)

1. Price < ORB low (5-min opening range low)
2. Price < VWAP (VWAP failure confirmation)

**Stop**: Day's high.

## Custom Exits (`exits.py`)

- Cover 50% at 10-day MA
- Cover remaining at 20-day MA
- Exit reason: `parabolic_target`

## Key Config (`config.yaml`)

```yaml
min_gain_pct_largecap: 50.0
min_gain_pct_smallcap: 200.0
min_days: 3
```
