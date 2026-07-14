"""supplier active flag

Revision ID: a4f7c2e91b58
Revises: d5f9a1c73e28
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "a4f7c2e91b58"  # pragma: allowlist secret
down_revision = "d5f9a1c73e28"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "suppliers",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("suppliers", "is_active")
