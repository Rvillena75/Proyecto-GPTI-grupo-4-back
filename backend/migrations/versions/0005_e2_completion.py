"""E2 authentication, decisions, evidence, product versions and receipt integrity.

Revision ID: 0005_e2_completion
Revises: 0004_scrape_config
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_e2_completion"
down_revision = "0004_scrape_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "price_host_clocks",
        sa.Column("host", sa.String(254), primary_key=True),
        sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.drop_constraint("uq_supplier_products_supplier_url", "supplier_products", type_="unique")
    op.create_index(
        "uq_supplier_products_active_url",
        "supplier_products",
        ["supplier_id", "url"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.add_column("price_quotes", sa.Column("evidence_snapshot", JSONB(), nullable=True))
    op.add_column("price_quotes", sa.Column("evidence_sha256", sa.String(64), nullable=True))
    op.add_column(
        "suggestions",
        sa.Column("decided_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("suggestions", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("suggestions", sa.Column("rejection_reason", sa.Text(), nullable=True))
    # Existing duplicate pending suggestions or corrupt receipts deliberately stop upgrade.
    # Operators must reconcile them explicitly; this migration never discards history.
    op.create_index(
        "uq_suggestions_pending_item",
        "suggestions",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_constraint("ck_orders_receipt_consistent", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_receipt_consistent",
        "orders",
        "(status = 'RECEIVED' AND received_at IS NOT NULL AND receipt_movement_id IS NOT NULL) "
        "OR (status = 'APPROVED' AND received_at IS NULL AND receipt_movement_id IS NULL)",
    )


def downgrade() -> None:
    # May fail if versions share a URL. Do not delete history to force a downgrade.
    op.drop_constraint("ck_orders_receipt_consistent", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_receipt_consistent",
        "orders",
        "(status = 'RECEIVED') = (received_at IS NOT NULL AND receipt_movement_id IS NOT NULL)",
    )
    op.drop_index("uq_suggestions_pending_item", table_name="suggestions")
    for name in ("rejection_reason", "decided_at", "decided_by"):
        op.drop_column("suggestions", name)
    op.drop_column("price_quotes", "evidence_sha256")
    op.drop_column("price_quotes", "evidence_snapshot")
    op.drop_index("uq_supplier_products_active_url", table_name="supplier_products")
    op.create_unique_constraint(
        "uq_supplier_products_supplier_url", "supplier_products", ["supplier_id", "url"]
    )
    op.drop_table("price_host_clocks")
    op.drop_table("login_throttles")
