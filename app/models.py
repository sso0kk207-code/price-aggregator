from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (Index("ix_prices_symbol_ts", "symbol", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    price: Mapped[float] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderStatus(Base):
    __tablename__ = "provider_status"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # above | below
    threshold: Mapped[float] = mapped_column(Float)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    armed: Mapped[bool] = mapped_column(default=True)  # False после срабатывания, пока цена не вернётся обратно


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    price: Mapped[float] = mapped_column(Float)
    message: Mapped[str] = mapped_column(String(300))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
