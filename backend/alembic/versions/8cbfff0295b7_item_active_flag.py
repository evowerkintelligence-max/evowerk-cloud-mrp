"""item active flag

Revision ID: 8cbfff0295b7
Revises: b7e14a2f9d63
Create Date: 2026-07-11

"""
from alembic import op
import sqlalchemy as sa

revision = "8cbfff0295b7"  # pragma: allowlist secret
down_revision = "b7e14a2f9d63"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("items", "is_active")
