"""org.enabled_features, user.is_staff

Revision ID: b2e9f4a71c83
Revises: a1f4c8b02d17
Create Date: 2026-07-23

"""
from alembic import op
import sqlalchemy as sa

revision = "b2e9f4a71c83"  # pragma: allowlist secret
down_revision = "a1f4c8b02d17"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("enabled_features", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "users",
        sa.Column("is_staff", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_staff")
    op.drop_column("orgs", "enabled_features")
