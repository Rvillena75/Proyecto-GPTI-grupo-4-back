"""Fixed dispatch cost per supplier, frozen on suggestions as subtotal + shipping.

Revision ID: 0006_shipping_cost
Revises: 0005_e2_completion
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_shipping_cost"
down_revision = "0005_e2_completion"
branch_labels = None
depends_on = None

AMOUNT = sa.Numeric(24, 6)


def upgrade() -> None:
    op.add_column(
        "suppliers",
        sa.Column("shipping_cost_clp", AMOUNT, nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_suppliers_shipping_nonnegative", "suppliers", "shipping_cost_clp >= 0"
    )
    op.add_column("suggestions", sa.Column("subtotal_clp", AMOUNT, nullable=True))
    op.add_column("suggestions", sa.Column("shipping_cost_clp", AMOUNT, nullable=True))
    # Earlier suggestions excluded dispatch: their whole total is the subtotal.
    op.execute(
        "UPDATE suggestions SET subtotal_clp = total_clp, shipping_cost_clp = 0 "
        "WHERE total_clp IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("suggestions", "shipping_cost_clp")
    op.drop_column("suggestions", "subtotal_clp")
    op.drop_constraint("ck_suppliers_shipping_nonnegative", "suppliers", type_="check")
    op.drop_column("suppliers", "shipping_cost_clp")
