"""Frozen v1.2 data model: users, suppliers, prices, suggestions and orders.

Revision ID: 0003_procurement
Revises: 0002_balance_guard
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_procurement"
down_revision = "0002_balance_guard"
branch_labels = None
depends_on = None

AMOUNT = sa.Numeric(24, 6)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True)


def _fk(name: str, target: str, *, nullable: bool) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey(target), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "users",
        _id(),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('ADMIN', 'WAREHOUSE')", name="ck_users_role"),
    )
    # Who recorded each audit event. Nullable until authentication exists (REQ-14).
    # Adding a column does not fire the row-level immutability triggers of 0001.
    op.add_column("movements", _fk("created_by", "users.id", nullable=True))
    op.add_column("counts", _fk("created_by", "users.id", nullable=True))

    op.create_table(
        "suppliers",
        _id(),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("price_source_url", sa.String(1000)),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("adapter", sa.String(50)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("lead_time_days >= 0", name="ck_suppliers_lead_time_nonnegative"),
    )
    op.create_table(
        "supplier_products",
        _id(),
        _fk("supplier_id", "suppliers.id", nullable=False),
        _fk("item_id", "items.id", nullable=False),
        sa.Column("name_on_site", sa.String(300), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("pack_quantity", AMOUNT, nullable=False),
        sa.Column("min_packs", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("supplier_id", "url", name="uq_supplier_products_supplier_url"),
        sa.CheckConstraint("pack_quantity > 0", name="ck_supplier_products_pack_positive"),
        sa.CheckConstraint("min_packs >= 1", name="ck_supplier_products_min_packs"),
    )
    op.create_index("ix_supplier_products_item_id", "supplier_products", ["item_id"])

    op.create_table(
        "scrape_runs",
        _id(),
        _fk("supplier_id", "suppliers.id", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("message", sa.Text()),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'OK', 'PARTIAL', 'ERROR')", name="ck_scrape_runs_status"
        ),
    )
    # Prices come only from scraping (D9, C2): no source column, no manual entry.
    # Stored as published, VAT included (C14).
    op.create_table(
        "price_quotes",
        _id(),
        _fk("supplier_product_id", "supplier_products.id", nullable=False),
        _fk("run_id", "scrape_runs.id", nullable=True),
        sa.Column("pack_price_clp", AMOUNT, nullable=False),
        sa.Column("unit_price_clp", AMOUNT, nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("pack_price_clp > 0", name="ck_price_quotes_pack_positive"),
        sa.CheckConstraint("unit_price_clp > 0", name="ck_price_quotes_unit_positive"),
        sa.CheckConstraint("valid_until > observed_at", name="ck_price_quotes_validity"),
    )
    op.create_index(
        "ix_price_quotes_product_observed", "price_quotes", ["supplier_product_id", "observed_at"]
    )

    op.create_table(
        "suggestions",
        _id(),
        _fk("item_id", "items.id", nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("required_qty", AMOUNT, nullable=False),
        sa.Column("in_transit_qty", AMOUNT, nullable=False),
        _fk("supplier_product_id", "supplier_products.id", nullable=True),
        sa.Column("packs", sa.Integer()),
        sa.Column("purchased_qty", AMOUNT),
        sa.Column("total_clp", AMOUNT),
        sa.Column("next_best_total_clp", AMOUNT),
        sa.Column("savings_clp", AMOUNT),
        sa.Column("inputs_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("created_by", "users.id", nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')", name="ck_suggestions_status"
        ),
        sa.CheckConstraint("required_qty >= 0", name="ck_suggestions_required_nonnegative"),
        sa.CheckConstraint("in_transit_qty >= 0", name="ck_suggestions_transit_nonnegative"),
        sa.CheckConstraint("packs IS NULL OR packs >= 1", name="ck_suggestions_packs"),
    )
    op.create_index("ix_suggestions_item_id", "suggestions", ["item_id"])

    op.create_table(
        "orders",
        _id(),
        _fk("suggestion_id", "suggestions.id", nullable=False),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("status", sa.String(10), nullable=False),
        _fk("decided_by", "users.id", nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        _fk("receipt_movement_id", "movements.id", nullable=True),
        sa.UniqueConstraint("suggestion_id", name="uq_orders_suggestion"),
        sa.UniqueConstraint("receipt_movement_id", name="uq_orders_receipt_movement"),
        sa.CheckConstraint("status IN ('APPROVED', 'RECEIVED')", name="ck_orders_status"),
        sa.CheckConstraint(
            "(status = 'RECEIVED') = (received_at IS NOT NULL AND receipt_movement_id IS NOT NULL)",
            name="ck_orders_receipt_consistent",
        ),
    )


def downgrade() -> None:
    op.drop_table("orders")
    op.drop_index("ix_suggestions_item_id", table_name="suggestions")
    op.drop_table("suggestions")
    op.drop_index("ix_price_quotes_product_observed", table_name="price_quotes")
    op.drop_table("price_quotes")
    op.drop_table("scrape_runs")
    op.drop_index("ix_supplier_products_item_id", table_name="supplier_products")
    op.drop_table("supplier_products")
    op.drop_table("suppliers")
    op.drop_column("counts", "created_by")
    op.drop_column("movements", "created_by")
    op.drop_table("users")
