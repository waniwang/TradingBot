---
description: Run the trading bot test suite
user_invocable: true
---

# Run Tests

Run the trading bot test suite and report results.

## Steps

1. Run: `cd /Users/hanlin/Developer/Trading/trading-bot && .venv/bin/pytest tests/ -v`
2. If all tests pass, report the count (should be 155 tests)
3. If any tests fail:
   - List the failing tests with their error messages
   - Analyze the failures and suggest fixes
   - If the failures are related to recent code changes, identify which changes likely caused them

## Running specific tests
- Single file: `.venv/bin/pytest tests/test_signals.py -v`
- Single test: `.venv/bin/pytest tests/test_signals.py::test_name -v`
- With output: `.venv/bin/pytest tests/ -v -s` (shows print statements)
