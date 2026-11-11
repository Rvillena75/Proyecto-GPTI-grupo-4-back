"""CSV retry ledger, additive only; inventory history remains immutable.

Revision ID: 0007_csv_imports
Revises: 0006_shipping_cost
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_csv_imports"
down_revision = "0006_shipping_cost"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "csv_imports",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("movement_ids", JSONB, nullable=False),
        sa.Column("created_by", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("csv_imports")
