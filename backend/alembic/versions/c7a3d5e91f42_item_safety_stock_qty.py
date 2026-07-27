"""items.safety_stock_qty

Revision ID: c7a3d5e91f42
Revises: 959ffc9982e3
Create Date: 2026-07-26

"""
from alembic import op
import sqlalchemy as sa

revision = "c7a3d5e91f42"  # pragma: allowlist secret
down_revision = "959ffc9982e3"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no server_default on purpose: NULL means "no buffer policy
    # set" and nets identically to today's plan (see
    # app.services.mrp._explode_and_net), so backfilling a 0 would be noise.
    op.add_column("items", sa.Column("safety_stock_qty", sa.Numeric(12, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "safety_stock_qty")
