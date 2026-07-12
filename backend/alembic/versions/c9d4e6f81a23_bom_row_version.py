"""bom row_version

Revision ID: c9d4e6f81a23
Revises: 24a3df0522d7
Create Date: 2026-07-11

"""
from alembic import op
import sqlalchemy as sa

revision = "c9d4e6f81a23"  # pragma: allowlist secret
down_revision = "24a3df0522d7"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "boms",
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("boms", "row_version")
