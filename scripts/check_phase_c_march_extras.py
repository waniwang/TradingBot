"""
Apply Phase C (market cap, security class, earnings/no-earnings) to the
March 2026 bot-path qualifiers, to see which of the 4 bot-only trades
would be filtered out — and thus whether bot end-to-end ~= Spikeet.

Phase C mirrors strategies/ep_earnings/scanner.py and ep_news/scanner.py:
  - market cap >= $800M (earn) or $1B (news)  [we test both]
  - quoteType == EQUITY
  - earnings today/yesterday (earn) OR no earnings (news)
"""
from __future__ import annotations
import time
from datetime import date, timedelta
import yfinance as yf

# (date, ticker, strategy, day2_ret, bot_only?)
TRADES = [
    ("2026-03-02", "AVAV",  "earn_C", 9.59, False),
    ("2026-03-10", "MSLE",  "earn_C", 4.82, True),
    ("2026-03-11", "NATR",  "earn_C", 1.78, True),
    ("2026-03-17", "IBRX",  "earn_C", 4.26, False),
    ("2026-03-18", "PDYN",  "earn_C", 5.71, True),
    ("2026-03-20", "UAMY",  "earn_C", 9.56, False),
    ("2026-03-24", "SLNHP", "earn_C", 1.25, True),
    ("2026-03-30", "SMTC",  "earn_C", 8.89, False),
]

MIN_MCAP_EARN = 800_000_000

def get_info(tkr, retries=4):
    for i in range(retries):
        try:
            info = yf.Ticker(tkr).info
            return info
        except Exception as e:
            if "Too Many" in str(e) and i < retries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            return {"_err": str(e)}
    return {"_err": "retries exhausted"}

def earnings_near(tkr, d):
    try:
        ed = yf.Ticker(tkr).get_earnings_dates(limit=8)
        if ed is None or ed.empty:
            return None
        target = date.fromisoformat(d)
        window = {target - timedelta(days=i) for i in range(0, 3)}
        for dt in ed.index:
            dd = dt.date() if hasattr(dt, "date") else dt
            if dd in window:
                return True
        return False
    except Exception as e:
        return None  # unknown

print(f"{'Date':<11} {'Tkr':<6} {'MCap':<10} {'Type':<8} {'Earn≤2d':<8} {'Passes C?':<10} {'Bot-only':<8}")
print("-" * 72)
for d, t, s, r, bot_only in TRADES:
    info = get_info(t)
    mc = float(info.get("marketCap", 0) or 0)
    qt = str(info.get("quoteType", "") or "")
    mc_str = f"${mc/1e6:.0f}M" if mc < 1e9 else f"${mc/1e9:.2f}B"
    en = earnings_near(t, d)
    en_str = "yes" if en is True else ("no" if en is False else "?")

    # Earn strategy Phase C: mcap >= $800M, type=EQUITY, earnings recent
    pass_mcap = mc >= MIN_MCAP_EARN
    pass_type = (qt.upper() == "EQUITY")
    pass_earn = (en is True)
    passes = pass_mcap and pass_type and pass_earn
    fail_reasons = []
    if not pass_mcap: fail_reasons.append(f"mcap<{MIN_MCAP_EARN/1e6:.0f}M")
    if not pass_type: fail_reasons.append(f"type={qt or '?'}")
    if not pass_earn: fail_reasons.append("no_earnings" if en is False else "earn=?")
    status = "PASS" if passes else "FAIL: " + ",".join(fail_reasons)
    print(f"{d:<11} {t:<6} {mc_str:<10} {qt:<8} {en_str:<8} {status:<30} {'*' if bot_only else ''}")
    time.sleep(0.8)
