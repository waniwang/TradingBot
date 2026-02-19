"""
Qullamaggie Trading Bot — Dashboard

Sections:
  1. Status bar   — running / phase / next job countdown
  2. Portfolio    — value, cash, daily P&L, open positions
  3. Positions    — live table with unrealized P&L + flatten buttons
  4. Watchlist    — today's candidates being monitored
  5. Signals      — every signal fired today
  6. Trade history — closed positions with realized P&L
  7. P&L chart    — cumulative daily P&L over last 30 days
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# Allow running from project root or dashboard/
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from db.models import init_db, get_session, Position, Signal, Order, DailyPnl

ET = ZoneInfo("America/New_York")
STATUS_FILE = ROOT / "bot_status.json"
REFRESH_SECONDS = 30

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Trading Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Shared resources (cached across reruns)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("alpaca", {})["api_key"] = (
        os.environ.get("ALPACA_API_KEY") or cfg["alpaca"].get("api_key", "")
    )
    cfg.setdefault("alpaca", {})["secret_key"] = (
        os.environ.get("ALPACA_SECRET_KEY") or cfg["alpaca"].get("secret_key", "")
    )
    return cfg


@st.cache_resource
def get_db_engine():
    config = get_config()
    return init_db(config["database"]["url"])


@st.cache_resource
def get_alpaca():
    """Alpaca client for live price lookups."""
    from executor.alpaca_client import AlpacaClient
    client = AlpacaClient(get_config())
    client.connect()
    return client


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def read_bot_status() -> dict:
    if not STATUS_FILE.exists():
        return {"running": False, "phase": "unknown", "next_job": None,
                "next_job_time": None, "watchlist": [], "environment": "paper",
                "last_heartbeat": None}
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"running": False, "phase": "error"}


def load_open_positions(engine) -> list[Position]:
    with get_session(engine) as session:
        return session.query(Position).filter_by(is_open=True).all()


def load_closed_today(engine) -> list[Position]:
    today_start = datetime.combine(date.today(), datetime.min.time())
    with get_session(engine) as session:
        return (
            session.query(Position)
            .filter(Position.is_open == False,
                    Position.closed_at >= today_start)
            .order_by(Position.closed_at.desc())
            .all()
        )


def load_signals_today(engine) -> list[Signal]:
    today_start = datetime.combine(date.today(), datetime.min.time())
    with get_session(engine) as session:
        return (
            session.query(Signal)
            .filter(Signal.fired_at >= today_start)
            .order_by(Signal.fired_at.desc())
            .all()
        )


def load_pnl_history(engine, days: int = 30) -> pd.DataFrame:
    cutoff = date.today() - timedelta(days=days)
    with get_session(engine) as session:
        rows = (
            session.query(DailyPnl)
            .filter(DailyPnl.trade_date >= cutoff)
            .order_by(DailyPnl.trade_date)
            .all()
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "Date": r.trade_date,
        "Daily P&L": r.total_pnl,
        "Realized": r.realized_pnl,
        "Portfolio": r.portfolio_value,
        "Trades": r.num_trades,
        "W": r.num_winners,
        "L": r.num_losers,
    } for r in rows])
    df["Cumulative"] = df["Daily P&L"].cumsum()
    return df


def load_closed_history(engine, limit: int = 50) -> pd.DataFrame:
    with get_session(engine) as session:
        rows = (
            session.query(Position)
            .filter_by(is_open=False)
            .order_by(Position.closed_at.desc())
            .limit(limit)
            .all()
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "Date": p.closed_at.strftime("%m/%d %H:%M") if p.closed_at else "",
        "Ticker": p.ticker,
        "Setup": p.setup_type.replace("_", " ").title(),
        "Side": p.side.upper(),
        "Entry": f"${p.entry_price:.2f}",
        "Exit": f"${p.exit_price:.2f}" if p.exit_price else "—",
        "P&L": p.realized_pnl or 0.0,
        "Days": p.days_held,
        "Reason": (p.exit_reason or "").replace("_", " "),
    } for p in rows])


def get_live_price(ticker: str) -> float | None:
    try:
        bar = get_alpaca().get_latest_bar(ticker)
        return bar["last_price"]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHASE_LABELS = {
    "idle":          ("💤 Idle",          "—"),
    "scanning":      ("🔍 Scanning",       "Running pre-market scan"),
    "watchlist_ready": ("📋 Watchlist Ready", "Waiting for market open"),
    "observing":     ("👁 Observing",      "Monitoring watchlist for signals"),
    "trading":       ("⚡ Trading",        "Signal detected — managing order"),
    "end_of_day":    ("🌙 End of Day",     "Running EOD tasks"),
    "unknown":       ("❓ Unknown",        "Status file not found — is the bot running?"),
    "error":         ("❌ Error",          "Could not read status file"),
}

JOB_LABELS = {
    "premarket_scan":    "Pre-market scan",
    "subscribe_watchlist": "Subscribe watchlist",
    "intraday_monitor":  "Intraday monitor start",
    "eod_tasks":         "End-of-day tasks",
    "heartbeat":         "Heartbeat",
}


def fmt_countdown(iso_time: str | None) -> str:
    if not iso_time:
        return "—"
    try:
        next_dt = datetime.fromisoformat(iso_time)
        now = datetime.now(timezone.utc).astimezone(next_dt.tzinfo)
        delta = next_dt - now
        if delta.total_seconds() < 0:
            return "now"
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}h {m}m"
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"
    except Exception:
        return "—"


def fmt_next_job_time(iso_time: str | None) -> str:
    if not iso_time:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_time).astimezone(ET)
        return dt.strftime("%-I:%M %p ET")
    except Exception:
        return "—"


def pnl_color(val: float) -> str:
    if val > 0:
        return "🟢"
    if val < 0:
        return "🔴"
    return "⚪"


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main():
    engine = get_db_engine()
    status = read_bot_status()

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    env_badge = "🟡 PAPER" if status.get("environment", "paper") == "paper" else "🔴 LIVE"
    st.markdown(f"## 📈 Trading Bot &nbsp;&nbsp; `{env_badge}`")

    # -----------------------------------------------------------------------
    # Status bar
    # -----------------------------------------------------------------------
    phase_key = status.get("phase", "unknown")
    phase_label, phase_desc = PHASE_LABELS.get(phase_key, ("❓", phase_key))

    heartbeat = status.get("last_heartbeat")
    if heartbeat:
        try:
            hb_dt = datetime.fromisoformat(heartbeat)
            age_secs = (datetime.now(timezone.utc) - hb_dt.astimezone(timezone.utc)).total_seconds()
            bot_running = age_secs < 120  # stale if no heartbeat for 2 min
        except Exception:
            bot_running = False
    else:
        bot_running = False

    running_badge = "🟢 Running" if bot_running else "🔴 Stopped"
    next_job = JOB_LABELS.get(status.get("next_job", ""), status.get("next_job") or "—")
    next_time = fmt_next_job_time(status.get("next_job_time"))
    countdown  = fmt_countdown(status.get("next_job_time"))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bot Status", running_badge)
    c2.metric("Current Phase", phase_label, phase_desc)
    c3.metric("Next Job", next_job, next_time)
    c4.metric("In", countdown)

    st.divider()

    # -----------------------------------------------------------------------
    # Portfolio metrics
    # -----------------------------------------------------------------------
    try:
        alpaca = get_alpaca()
        portfolio_value = alpaca.get_portfolio_value()
        cash = alpaca.get_cash()
    except Exception:
        portfolio_value = 0.0
        cash = 0.0

    open_positions = load_open_positions(engine)
    closed_today = load_closed_today(engine)

    daily_realized = sum(p.realized_pnl or 0.0 for p in closed_today)
    daily_unrealized = 0.0
    for p in open_positions:
        price = get_live_price(p.ticker)
        if price:
            daily_unrealized += p.unrealized_pnl(price)
    total_daily_pnl = daily_realized + daily_unrealized
    daily_pnl_pct = total_daily_pnl / portfolio_value * 100 if portfolio_value else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Portfolio Value", f"${portfolio_value:,.2f}")
    m2.metric("Cash", f"${cash:,.2f}")
    m3.metric(
        "Daily P&L",
        f"${total_daily_pnl:+,.2f}",
        f"{daily_pnl_pct:+.2f}%",
        delta_color="normal",
    )
    m4.metric("Open Positions", len(open_positions), f"max {get_config()['risk']['max_positions']}")
    m5.metric("Trades Today", len(closed_today))

    st.divider()

    # -----------------------------------------------------------------------
    # Open positions + Watchlist (side by side)
    # -----------------------------------------------------------------------
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Open Positions")
        if not open_positions:
            st.info("No open positions.")
        else:
            rows = []
            for p in open_positions:
                price = get_live_price(p.ticker) or p.entry_price
                unreal = p.unrealized_pnl(price)
                gain = p.gain_pct(price)
                remaining = p.shares - p.partial_exit_shares
                rows.append({
                    "_id": p.id,
                    "Ticker": p.ticker,
                    "Setup": p.setup_type.replace("_", " ").title(),
                    "Side": p.side.upper(),
                    "Shares": remaining,
                    "Entry": p.entry_price,
                    "Stop": p.stop_price,
                    "Current": price,
                    "Gain %": f"{gain:+.2f}%",
                    "Unreal P&L": unreal,
                    "Days": p.days_held,
                    "Partial": "✓" if p.partial_exit_done else "",
                })

            df = pd.DataFrame(rows).drop(columns=["_id"])
            st.dataframe(
                df.style.format({
                    "Entry": "${:.2f}",
                    "Stop": "${:.2f}",
                    "Current": "${:.2f}",
                    "Unreal P&L": "${:+.2f}",
                }),
                use_container_width=True,
                hide_index=True,
            )

            st.caption("Manual close:")
            btn_cols = st.columns(min(len(open_positions), 4))
            for i, p in enumerate(open_positions):
                with btn_cols[i % 4]:
                    if st.button(f"Flatten {p.ticker}", key=f"flat_{p.id}"):
                        try:
                            remaining = p.shares - p.partial_exit_shares
                            get_alpaca().close_position(p.ticker, remaining, p.side)
                            st.success(f"Market close order sent for {p.ticker}")
                        except Exception as e:
                            st.error(str(e))

    with right:
        st.subheader("Watchlist")
        watchlist = status.get("watchlist", [])
        if not watchlist:
            st.info("No watchlist yet — runs at 6:00 AM ET.")
        else:
            wdf = pd.DataFrame([{
                "Ticker": w["ticker"],
                "Setup": w.get("setup_type", "").replace("_", " ").title(),
                "Gap %": f"{w['gap_pct']:.1f}%" if w.get("gap_pct") else "—",
            } for w in watchlist])
            st.dataframe(wdf, use_container_width=True, hide_index=True)

    st.divider()

    # -----------------------------------------------------------------------
    # P&L chart
    # -----------------------------------------------------------------------
    st.subheader("Cumulative P&L — Last 30 Days")
    pnl_df = load_pnl_history(engine)
    if pnl_df.empty:
        st.info("No P&L history yet. Trades will appear here after market close.")
    else:
        st.line_chart(pnl_df.set_index("Date")[["Cumulative", "Daily P&L"]])

        sc1, sc2, sc3, sc4 = st.columns(4)
        total_trades = int(pnl_df["Trades"].sum())
        total_wins   = int(pnl_df["W"].sum())
        win_rate     = total_wins / total_trades * 100 if total_trades > 0 else 0
        sc1.metric("Total P&L", f"${pnl_df['Daily P&L'].sum():+,.2f}")
        sc2.metric("Win Rate",  f"{win_rate:.0f}%")
        sc3.metric("Total Trades", total_trades)
        sc4.metric("Best Day",  f"${pnl_df['Daily P&L'].max():+,.2f}")

    st.divider()

    # -----------------------------------------------------------------------
    # Today's signals
    # -----------------------------------------------------------------------
    st.subheader("Signals Today")
    signals = load_signals_today(engine)
    if not signals:
        st.info("No signals fired today.")
    else:
        sdf = pd.DataFrame([{
            "Time": s.fired_at.strftime("%H:%M:%S"),
            "Ticker": s.ticker,
            "Setup": s.setup_type.replace("_", " ").title(),
            "Entry": f"${s.entry_price:.2f}",
            "Stop": f"${s.stop_price:.2f}",
            "Gap %": f"{s.gap_pct:.1f}%" if s.gap_pct else "—",
            "Acted": "✓" if s.acted_on else "—",
        } for s in signals])
        st.dataframe(sdf, use_container_width=True, hide_index=True)

    st.divider()

    # -----------------------------------------------------------------------
    # Trade history
    # -----------------------------------------------------------------------
    st.subheader("Trade History")
    hist_df = load_closed_history(engine)
    if hist_df.empty:
        st.info("No closed trades yet.")
    else:
        def _style_pnl(val):
            if isinstance(val, float):
                color = "#00c853" if val > 0 else ("#d50000" if val < 0 else "")
                return f"color: {color}; font-weight: bold"
            return ""

        st.dataframe(
            hist_df.style
                .applymap(_style_pnl, subset=["P&L"])
                .format({"P&L": "${:+.2f}"}),
            use_container_width=True,
            hide_index=True,
        )

    # -----------------------------------------------------------------------
    # Footer + auto-refresh
    # -----------------------------------------------------------------------
    st.divider()
    now_et = datetime.now(ET).strftime("%H:%M:%S ET")
    st.caption(f"Last loaded: {now_et} · Refreshes every {REFRESH_SECONDS}s")

    import time
    time.sleep(REFRESH_SECONDS)
    st.rerun()


if __name__ == "__main__":
    main()
