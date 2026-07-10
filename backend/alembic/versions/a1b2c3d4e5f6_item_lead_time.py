"""item lead_time_days

Revision ID: a1b2c3d4e5f6
Revises: f6a3c9d18e72
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f6a3c9d18e72"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "lead_time_days")
