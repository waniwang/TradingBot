# Breakout Strategy

Multi-day long setup on consolidation breakouts. Two-phase scan (nightly + premarket), trades intraday.

## Flow

1. **5:00 PM** — `scanner_nightly.py` ranks universe by momentum, analyzes consolidation patterns
2. **6:00 AM** — `scanner_premarket.py` promotes "ready" candidates to "active" for trading
3. **9:30 AM** — `signal.py` monitors 1m bars for ORH breakout
4. **EOD** — Unfired candidates demoted back to "ready" (multi-day persistence, not expired)

## Nightly Scan (`scanner_nightly.py`)

1. Fetch universe from Alpaca
2. Rank by momentum (top 20 by composite RS score via `scanner/momentum_rank.py`)
3. Analyze consolidation pattern for each candidate via `scanner/consolidation.py`
4. Persist to DB as "watching" or "ready" stage

### Consolidation Requirements

| Condition | Threshold |
|-----------|-----------|
| Prior large move | >= 30% in ~2 months before consolidation |
| Duration | 10-40 trading days |
| ATR contraction | Recent/older ATR ratio < 0.95 |
| Near 10d & 20d MA | Within 3% tolerance |
| Higher lows | Positive slope via linear regression |

## Entry Signal (`signal.py`)

All conditions must pass:

1. Price > ORH (5-min opening range high)
2. Extension <= 3% above ORH
3. Price > 20-day SMA
4. RVOL >= 1.5x 20-day avg (time-of-day normalized)

**Stop**: LOD, capped at 1.0x ATR(14).

## Key Config (`config.yaml`)

```yaml
# Consolidation (nightly)
consolidation_days_min: 10
consolidation_days_max: 40
consolidation_atr_ratio: 0.95
# Signal (intraday)
volume_multiplier: 1.5
max_extension_pct: 3.0
stop_atr_mult: 1.0
```
