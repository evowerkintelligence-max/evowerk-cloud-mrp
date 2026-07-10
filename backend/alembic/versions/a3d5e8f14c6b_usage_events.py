"""usage_events (telemetry)

Revision ID: a3d5e8f14c6b
Revises: f2a9c7e13b40
Create Date: 2026-07-10

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3d5e8f14c6b"  # pragma: allowlist secret
down_revision = "f2a9c7e13b40"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_org_id", "usage_events", ["org_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_org_id", table_name="usage_events")
    op.drop_table("usage_events")
