"""Small PostgreSQL schema for the P1–P5 inventory slice."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


AMOUNT = Numeric(24, 6)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("current_stock >= 0", name="ck_items_stock_nonnegative"),
        CheckConstraint(
            "manual_safe_stock IS NULL OR manual_safe_stock >= 0",
            name="ck_items_manual_stock_nonnegative",
        ),
        CheckConstraint("base_unit IN ('kg', 'L', 'unidad')", name="ck_items_base_unit"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(10), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(100), nullable=False)
    current_stock: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    manual_safe_stock: Mapped[Decimal | None] = mapped_column(AMOUNT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index(
            "uq_observations_one_active_per_item",
            "item_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False)


class Movement(Base):
    __tablename__ = "movements"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('INITIAL', 'ENTRY', 'EXIT', 'COUNT_ADJUSTMENT', 'REVERSAL', 'CORRECTION')",
            name="ck_movements_kind",
        ),
        Index(
            "uq_movements_one_invalidation",
            "invalidates_movement_id",
            unique=True,
            postgresql_where=text("invalidates_movement_id IS NOT NULL"),
        ),
        Index("ix_movements_observation_time", "observation_id", "occurred_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False, index=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("observations.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    delta: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    input_quantity: Mapped[Decimal | None] = mapped_column(AMOUNT)
    input_unit: Mapped[str | None] = mapped_column(String(10))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    invalidates_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("movements.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text)


class Count(Base):
    __tablename__ = "counts"
    __table_args__ = (
        CheckConstraint("physical_stock >= 0", name="ck_counts_physical_nonnegative"),
        CheckConstraint("shortage >= 0", name="ck_counts_shortage_nonnegative"),
        Index("ix_counts_observation_time", "observation_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False, index=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("observations.id"), nullable=False)
    adjustment_movement_id: Mapped[int] = mapped_column(
        ForeignKey("movements.id"), nullable=False, unique=True
    )
    corrects_movement_id: Mapped[int | None] = mapped_column(ForeignKey("movements.id"))
    theoretical_before: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    physical_stock: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    difference: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    shortage: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    cause: Mapped[str | None] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
