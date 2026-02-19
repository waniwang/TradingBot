"""
Streamlit dashboard.

Shows:
- Open positions table (entry, stop, current P&L, days held)
- Daily P&L chart
- Signal log
- Portfolio exposure gauge
- Manual flatten button per position
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from db.models import init_db, get_session, Position, Signal, Order, DailyPnl


@st.cache_resource
def load_config():
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


@st.cache_resource
def get_engine():
    config = load_config()
    return init_db(config["database"]["url"])


def get_broker_client():
    config = load_config()
    from executor.alpaca_client import AlpacaClient
    client = AlpacaClient(config)
    client.connect()
    return client


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_open_positions(engine) -> pd.DataFrame:
    with get_session(engine) as session:
        rows = session.query(Position).filter_by(is_open=True).all()
        if not rows:
            return pd.DataFrame()
        data = []
        for p in rows:
            data.append({
                "ID": p.id,
                "Ticker": p.ticker,
                "Setup": p.setup_type,
                "Side": p.side,
                "Shares": p.shares,
                "Entry": p.entry_price,
                "Stop": p.stop_price,
                "Initial Stop": p.initial_stop_price,
                "Partial Exit": "Yes" if p.partial_exit_done else "No",
                "Days Held": p.days_held,
                "Opened": p.opened_at.strftime("%Y-%m-%d %H:%M"),
            })
        return pd.DataFrame(data)


def load_daily_pnl(engine, days_back: int = 30) -> pd.DataFrame:
    cutoff = date.today() - timedelta(days=days_back)
    with get_session(engine) as session:
        rows = (
            session.query(DailyPnl)
            .filter(DailyPnl.trade_date >= cutoff)
            .order_by(DailyPnl.trade_date)
            .all()
        )
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "Date": r.trade_date,
                "Realized P&L": r.realized_pnl,
                "Total P&L": r.total_pnl,
                "Portfolio": r.portfolio_value,
                "Trades": r.num_trades,
                "Winners": r.num_winners,
                "Losers": r.num_losers,
            }
            for r in rows
        ])


def load_recent_signals(engine, limit: int = 50) -> pd.DataFrame:
    with get_session(engine) as session:
        rows = (
            session.query(Signal)
            .order_by(Signal.fired_at.desc())
            .limit(limit)
            .all()
        )
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "Time": r.fired_at.strftime("%Y-%m-%d %H:%M"),
                "Ticker": r.ticker,
                "Setup": r.setup_type,
                "Entry": r.entry_price,
                "Stop": r.stop_price,
                "Gap%": r.gap_pct,
                "Acted": "Yes" if r.acted_on else "No",
            }
            for r in rows
        ])


def load_closed_positions(engine, limit: int = 50) -> pd.DataFrame:
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
        return pd.DataFrame([
            {
                "Ticker": p.ticker,
                "Setup": p.setup_type,
                "Side": p.side,
                "Entry": p.entry_price,
                "Exit": p.exit_price,
                "P&L": p.realized_pnl,
                "Reason": p.exit_reason,
                "Days": p.days_held,
                "Closed": p.closed_at.strftime("%Y-%m-%d %H:%M") if p.closed_at else "",
            }
            for p in rows
        ])


# ---------------------------------------------------------------------------
# App layout
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="Qullamaggie Bot",
        page_icon="📈",
        layout="wide",
    )
    st.title("📈 Qullamaggie Trading Bot")

    engine = get_engine()

    # Auto-refresh every 60 seconds
    st.sidebar.header("Controls")
    auto_refresh = st.sidebar.checkbox("Auto-refresh (60s)", value=True)
    if auto_refresh:
        import time
        st.sidebar.write(f"Last update: {datetime.now().strftime('%H:%M:%S')}")

    # ---------------------------------------------------------------------------
    # Open Positions
    # ---------------------------------------------------------------------------
    st.header("Open Positions")
    pos_df = load_open_positions(engine)

    if pos_df.empty:
        st.info("No open positions.")
    else:
        # Colour P&L column — requires live prices (not available without broker)
        st.dataframe(pos_df, use_container_width=True)

        # Manual flatten buttons
        st.subheader("Manual Close")
        col_tickers = pos_df["Ticker"].tolist()
        col_ids = pos_df["ID"].tolist()
        for ticker, pos_id in zip(col_tickers, col_ids):
            if st.button(f"Flatten {ticker}", key=f"flat_{pos_id}"):
                try:
                    client = get_broker_client()
                    with get_session(engine) as session:
                        pos = session.get(Position, pos_id)
                        if pos and pos.is_open:
                            remaining = pos.shares - pos.partial_exit_shares
                            client.close_position(ticker, remaining, pos.side)
                            pos.exit_price = 0.0  # will be filled by broker
                            pos.exit_reason = "manual"
                            pos.is_open = False
                            pos.closed_at = datetime.utcnow()
                            session.commit()
                    st.success(f"Flatten order sent for {ticker}")
                except Exception as e:
                    st.error(f"Failed to flatten {ticker}: {e}")

    # ---------------------------------------------------------------------------
    # Daily P&L Chart
    # ---------------------------------------------------------------------------
    st.header("Daily P&L (last 30 days)")
    pnl_df = load_daily_pnl(engine)
    if pnl_df.empty:
        st.info("No P&L history yet.")
    else:
        pnl_df["Cumulative P&L"] = pnl_df["Total P&L"].cumsum()
        st.line_chart(pnl_df.set_index("Date")[["Total P&L", "Cumulative P&L"]])

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total P&L", f"${pnl_df['Total P&L'].sum():,.2f}")
        total_trades = pnl_df["Trades"].sum()
        total_wins = pnl_df["Winners"].sum()
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        col2.metric("Win Rate", f"{win_rate:.1f}%")
        col3.metric("Total Trades", int(total_trades))
        col4.metric("Best Day", f"${pnl_df['Total P&L'].max():,.2f}")

    # ---------------------------------------------------------------------------
    # Signal Log
    # ---------------------------------------------------------------------------
    st.header("Recent Signals")
    sig_df = load_recent_signals(engine)
    if sig_df.empty:
        st.info("No signals fired yet.")
    else:
        st.dataframe(sig_df, use_container_width=True)

    # ---------------------------------------------------------------------------
    # Trade History
    # ---------------------------------------------------------------------------
    st.header("Recent Closed Trades")
    closed_df = load_closed_positions(engine)
    if closed_df.empty:
        st.info("No closed trades yet.")
    else:
        # Colour P&L green/red
        def color_pnl(val):
            color = "green" if val and val > 0 else ("red" if val and val < 0 else "")
            return f"color: {color}"

        st.dataframe(
            closed_df.style.applymap(color_pnl, subset=["P&L"]),
            use_container_width=True,
        )

    # ---------------------------------------------------------------------------
    # Config preview
    # ---------------------------------------------------------------------------
    with st.expander("Current config"):
        config = load_config()
        # Mask sensitive keys
        safe = dict(config)
        if "telegram" in safe:
            safe["telegram"] = {k: "***" for k in safe["telegram"]}
        if "polygon" in safe:
            safe["polygon"] = {"api_key": "***"}
        st.json(safe)

    if auto_refresh:
        import time
        time.sleep(60)
        st.rerun()


if __name__ == "__main__":
    main()
