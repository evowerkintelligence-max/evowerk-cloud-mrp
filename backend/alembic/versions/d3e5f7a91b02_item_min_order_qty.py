"""item min_order_qty

Revision ID: d3e5f7a91b02
Revises: c9d4e6f81a23
Create Date: 2026-07-11

"""
from alembic import op
import sqlalchemy as sa

revision = "d3e5f7a91b02"  # pragma: allowlist secret
down_revision = "c9d4e6f81a23"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("min_order_qty", sa.Numeric(12, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "min_order_qty")
