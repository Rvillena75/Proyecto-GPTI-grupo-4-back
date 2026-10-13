"""Connect public-product adapter metadata to the existing procurement model.

Revision ID: 0004_scrape_config
Revises: 0003_procurement
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_scrape_config"
down_revision = "0003_procurement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: all existing suppliers, quotes, suggestions and orders remain valid.
    op.add_column("supplier_products", sa.Column("scrape_config", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("supplier_products", "scrape_config")
