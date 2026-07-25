"""device_tokens: registered Expo push tokens for the mobile app

Revision ID: d1e5f8a72c93
Revises: a1f4c8b02d17
Create Date: 2026-07-24

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1e5f8a72c93"  # pragma: allowlist secret
down_revision = "a1f4c8b02d17"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expo_push_token", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("expo_push_token", name="uq_device_tokens_expo_push_token"),
    )
    op.create_index("ix_device_tokens_org_id", "device_tokens", ["org_id"])
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_device_tokens_user_id", table_name="device_tokens")
    op.drop_index("ix_device_tokens_org_id", table_name="device_tokens")
    op.drop_table("device_tokens")
