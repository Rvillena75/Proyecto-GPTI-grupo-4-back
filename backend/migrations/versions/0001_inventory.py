"""Initial P1–P5 inventory schema and immutable audit records.

Revision ID: 0001_inventory
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_inventory"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("base_unit", sa.String(10), nullable=False),
        sa.Column("timezone_name", sa.String(100), nullable=False),
        sa.Column("current_stock", sa.Numeric(24, 6), nullable=False),
        sa.Column("manual_safe_stock", sa.Numeric(24, 6)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("current_stock >= 0", name="ck_items_stock_nonnegative"),
        sa.CheckConstraint(
            "manual_safe_stock IS NULL OR manual_safe_stock >= 0",
            name="ck_items_manual_stock_nonnegative",
        ),
        sa.CheckConstraint("base_unit IN ('kg', 'L', 'unidad')", name="ck_items_base_unit"),
    )
    op.create_table(
        "observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("item_id", sa.BigInteger(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_observations_item_id", "observations", ["item_id"])
    op.create_index(
        "uq_observations_one_active_per_item",
        "observations",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "movements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("item_id", sa.BigInteger(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column(
            "observation_id", sa.BigInteger(), sa.ForeignKey("observations.id"), nullable=False
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("delta", sa.Numeric(24, 6), nullable=False),
        sa.Column("input_quantity", sa.Numeric(24, 6)),
        sa.Column("input_unit", sa.String(10)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidates_movement_id", sa.BigInteger(), sa.ForeignKey("movements.id")),
        sa.Column("note", sa.Text()),
        sa.CheckConstraint(
            "kind IN ('INITIAL', 'ENTRY', 'EXIT', 'COUNT_ADJUSTMENT', 'REVERSAL', 'CORRECTION')",
            name="ck_movements_kind",
        ),
    )
    op.create_index("ix_movements_item_id", "movements", ["item_id"])
    op.create_index(
        "ix_movements_observation_time", "movements", ["observation_id", "occurred_at", "id"]
    )
    op.create_index(
        "uq_movements_one_invalidation",
        "movements",
        ["invalidates_movement_id"],
        unique=True,
        postgresql_where=sa.text("invalidates_movement_id IS NOT NULL"),
    )
    op.create_table(
        "counts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("item_id", sa.BigInteger(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column(
            "observation_id", sa.BigInteger(), sa.ForeignKey("observations.id"), nullable=False
        ),
        sa.Column(
            "adjustment_movement_id",
            sa.BigInteger(),
            sa.ForeignKey("movements.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("corrects_movement_id", sa.BigInteger(), sa.ForeignKey("movements.id")),
        sa.Column("theoretical_before", sa.Numeric(24, 6), nullable=False),
        sa.Column("physical_stock", sa.Numeric(24, 6), nullable=False),
        sa.Column("difference", sa.Numeric(24, 6), nullable=False),
        sa.Column("shortage", sa.Numeric(24, 6), nullable=False),
        sa.Column("cause", sa.String(30)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("physical_stock >= 0", name="ck_counts_physical_nonnegative"),
        sa.CheckConstraint("shortage >= 0", name="ck_counts_shortage_nonnegative"),
    )
    op.create_index("ix_counts_item_id", "counts", ["item_id"])
    op.create_index("ix_counts_observation_time", "counts", ["observation_id", "occurred_at"])

    op.execute(
        """
        CREATE FUNCTION reject_audit_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'historical audit records are immutable';
        END;
        $$;
        """
    )
    for table in ("movements", "counts"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_audit_change()"
        )
    op.execute(
        """
        CREATE FUNCTION reject_observation_start_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.started_at IS DISTINCT FROM OLD.started_at
               OR NEW.item_id IS DISTINCT FROM OLD.item_id THEN
                RAISE EXCEPTION 'observation start is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER observation_start_immutable BEFORE UPDATE ON observations "
        "FOR EACH ROW EXECUTE FUNCTION reject_observation_start_change()"
    )
    op.execute(
        "CREATE TRIGGER observations_immutable_delete BEFORE DELETE ON observations "
        "FOR EACH ROW EXECUTE FUNCTION reject_audit_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER observations_immutable_delete ON observations")
    op.execute("DROP TRIGGER observation_start_immutable ON observations")
    op.execute("DROP FUNCTION reject_observation_start_change()")
    op.execute("DROP TRIGGER counts_immutable ON counts")
    op.execute("DROP TRIGGER movements_immutable ON movements")
    op.execute("DROP FUNCTION reject_audit_change()")
    op.drop_table("counts")
    op.drop_table("movements")
    op.drop_table("observations")
    op.drop_table("items")
