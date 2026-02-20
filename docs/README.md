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
| [verification.md](verification.md) | Test plan, backtest targets, how to run backtests, paper trading checklist |
| [risks-and-mitigations.md](risks-and-mitigations.md) | Known risks and how they are handled |
| [operations.md](operations.md) | Bot operations: start/stop/deploy commands |

---

## Quick Context

- **Trader modeled after**: Kristjan Kullamagi ("qullamaggie") — momentum trader
- **Broker**: Alpaca (`alpaca-py`) — paper trade via `paper=True`, live via `paper=False`
- **Data**: Alpaca screener/snapshots for scanning; yfinance for daily bars and backtesting
- **Language**: Python 3.14 + pip/venv
- **Setups**: Breakout (long), Episodic Pivot (long), Parabolic Short

---

## Current Status

- Phases 1-5 complete (foundation, scanners, signals, risk/execution, backtesting)
- Phase 6 (paper trading) and Phase 7 (Telegram notifications) pending
- See [implementation-plan.md](implementation-plan.md) for the full phase checklist
