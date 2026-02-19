# Qullamaggie Trading Bot — Documentation Index

> All design decisions, strategy details, and implementation plans are recorded here for future reference and context restoration.

---

## Documents

| File | Contents |
|---|---|
| [strategy.md](strategy.md) | Trading strategy: the 3 setups, entry/exit rules, philosophy |
| [architecture.md](architecture.md) | System architecture, data flow, tech stack choices |
| [risk-management.md](risk-management.md) | Position sizing formulas, stop rules, hard limits |
| [implementation-plan.md](implementation-plan.md) | Phase-by-phase build plan with checklists |
| [config-reference.md](config-reference.md) | Full config.yaml schema and parameter documentation |
| [file-structure.md](file-structure.md) | Project file tree with description of each module |
| [verification.md](verification.md) | Test plan, backtest targets, paper trading checklist |
| [risks-and-mitigations.md](risks-and-mitigations.md) | Known risks and how they are handled |

---

## Quick Context

- **Trader modeled after**: Kristjan Kullamägi ("qullamaggie") — momentum trader
- **Broker**: Moomoo (futu-api) — paper trade via `TrdEnv.SIMULATE`, live via `TrdEnv.REAL`
- **Data**: Polygon.io for scanning; Moomoo push for real-time intraday
- **Language**: Python 3.12 + Poetry
- **Setups**: Breakout, Episodic Pivot (long), Parabolic Short (start with longs only)

---

## Current Status

See [implementation-plan.md](implementation-plan.md) for the phase checklist.
