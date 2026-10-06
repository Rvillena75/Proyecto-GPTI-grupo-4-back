"""PostgreSQL schema: P1–P5 inventory plus the frozen v1.2 procurement model."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


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
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('ADMIN', 'WAREHOUSE')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint("lead_time_days >= 0", name="ck_suppliers_lead_time_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    price_source_url: Mapped[str | None] = mapped_column(String(1000))
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    adapter: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SupplierProduct(Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "url", name="uq_supplier_products_supplier_url"),
        CheckConstraint("pack_quantity > 0", name="ck_supplier_products_pack_positive"),
        CheckConstraint("min_packs >= 1", name="ck_supplier_products_min_packs"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False, index=True)
    name_on_site: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    pack_quantity: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    min_packs: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'OK', 'PARTIAL', 'ERROR')", name="ck_scrape_runs_status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)


class PriceQuote(Base):
    """A scraped price, stored as published with VAT included (C14)."""

    __tablename__ = "price_quotes"
    __table_args__ = (
        CheckConstraint("pack_price_clp > 0", name="ck_price_quotes_pack_positive"),
        CheckConstraint("unit_price_clp > 0", name="ck_price_quotes_unit_positive"),
        CheckConstraint("valid_until > observed_at", name="ck_price_quotes_validity"),
        Index("ix_price_quotes_product_observed", "supplier_product_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    supplier_product_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_products.id"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_runs.id"))
    pack_price_clp: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    unit_price_clp: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    available: Mapped[bool] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Suggestion(Base):
    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')", name="ck_suggestions_status"
        ),
        CheckConstraint("required_qty >= 0", name="ck_suggestions_required_nonnegative"),
        CheckConstraint("in_transit_qty >= 0", name="ck_suggestions_transit_nonnegative"),
        CheckConstraint("packs IS NULL OR packs >= 1", name="ck_suggestions_packs"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    required_qty: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    in_transit_qty: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    supplier_product_id: Mapped[int | None] = mapped_column(ForeignKey("supplier_products.id"))
    packs: Mapped[int | None] = mapped_column(Integer)
    purchased_qty: Mapped[Decimal | None] = mapped_column(AMOUNT)
    total_clp: Mapped[Decimal | None] = mapped_column(AMOUNT)
    next_best_total_clp: Mapped[Decimal | None] = mapped_column(AMOUNT)
    savings_clp: Mapped[Decimal | None] = mapped_column(AMOUNT)
    inputs_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("suggestion_id", name="uq_orders_suggestion"),
        UniqueConstraint("receipt_movement_id", name="uq_orders_receipt_movement"),
        CheckConstraint("status IN ('APPROVED', 'RECEIVED')", name="ck_orders_status"),
        CheckConstraint(
            "(status = 'RECEIVED') = (received_at IS NOT NULL AND receipt_movement_id IS NOT NULL)",
            name="ck_orders_receipt_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(ForeignKey("suggestions.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_movement_id: Mapped[int | None] = mapped_column(ForeignKey("movements.id"))
