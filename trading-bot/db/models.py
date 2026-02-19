"""SQLAlchemy models: Signal, Order, Position, DailyPnl."""

from __future__ import annotations

import os
from datetime import datetime, date

from sqlalchemy import (
    create_engine,
    String,
    Float,
    Integer,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session


class Base(DeclarativeBase):
    pass


class Signal(Base):
    """Every signal fired by the signal engine."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    setup_type: Mapped[str] = mapped_column(
        Enum("breakout", "episodic_pivot", "parabolic_short", name="setup_type_enum"),
        nullable=False,
    )
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float] = mapped_column(Float, nullable=False)
    gap_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    orh: Mapped[float | None] = mapped_column(Float, nullable=True)
    orb_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    acted_on: Mapped[bool] = mapped_column(Boolean, default=False)
    fired_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    orders: Mapped[list[Order]] = relationship("Order", back_populates="signal")


class Order(Base):
    """Every order submitted to the broker."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("signals.id"), nullable=True
    )
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str] = mapped_column(
        Enum("buy", "sell", "sell_short", "buy_to_cover", name="order_side_enum"),
        nullable=False,
    )
    order_type: Mapped[str] = mapped_column(
        Enum("limit", "stop", "market", name="order_type_enum"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "submitted",
            "filled",
            "partially_filled",
            "cancelled",
            "rejected",
            name="order_status_enum",
        ),
        default="pending",
    )
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    signal: Mapped[Signal | None] = relationship("Signal", back_populates="orders")
    position: Mapped[Position | None] = relationship(
        "Position", back_populates="entry_order", foreign_keys="Position.entry_order_id"
    )


class Position(Base):
    """Open and closed positions."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    setup_type: Mapped[str] = mapped_column(
        Enum("breakout", "episodic_pivot", "parabolic_short", name="position_setup_enum"),
        nullable=False,
    )
    side: Mapped[str] = mapped_column(
        Enum("long", "short", name="position_side_enum"), nullable=False
    )

    entry_order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id"), nullable=True
    )
    stop_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float] = mapped_column(Float, nullable=False)
    initial_stop_price: Mapped[float] = mapped_column(Float, nullable=False)

    partial_exit_done: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_exit_shares: Mapped[int] = mapped_column(Integer, default=0)
    partial_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(
        Enum(
            "stop_hit",
            "trailing_stop",
            "manual",
            "daily_loss_limit",
            name="exit_reason_enum",
        ),
        nullable=True,
    )

    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    entry_order: Mapped[Order | None] = relationship(
        "Order", back_populates="position", foreign_keys=[entry_order_id]
    )

    @property
    def days_held(self) -> int:
        end = self.closed_at or datetime.utcnow()
        return (end.date() - self.opened_at.date()).days

    def unrealized_pnl(self, current_price: float) -> float:
        remaining = self.shares - self.partial_exit_shares
        if self.side == "long":
            return remaining * (current_price - self.entry_price)
        else:
            return remaining * (self.entry_price - current_price)

    def gain_pct(self, current_price: float) -> float:
        if self.side == "long":
            return (current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - current_price) / self.entry_price * 100


class DailyPnl(Base):
    """End-of-day P&L summary."""

    __tablename__ = "daily_pnl"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    portfolio_value: Mapped[float] = mapped_column(Float, nullable=False)
    num_trades: Mapped[int] = mapped_column(Integer, default=0)
    num_winners: Mapped[int] = mapped_column(Integer, default=0)
    num_losers: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def get_engine(db_url: str | None = None):
    url = db_url or os.environ.get("DATABASE_URL", "sqlite:///trading_bot.db")
    return create_engine(url, echo=False)


def init_db(db_url: str | None = None):
    """Create all tables if they don't exist."""
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine) -> Session:
    return Session(engine)
