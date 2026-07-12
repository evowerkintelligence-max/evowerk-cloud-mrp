"""realtime_streams

Revision ID: c4f8a2e60d97
Revises: b7e2d5a94c18
Create Date: 2026-07-12

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4f8a2e60d97"  # pragma: allowlist secret
down_revision = "b7e2d5a94c18"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "realtime_streams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("column_map", sa.JSON(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("accumulate", sa.Boolean(), nullable=False),
        sa.Column("desired_status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_realtime_streams_org_id", "realtime_streams", ["org_id"])
    op.create_index("ix_realtime_streams_integration_id", "realtime_streams", ["integration_id"])


def downgrade() -> None:
    op.drop_index("ix_realtime_streams_integration_id", table_name="realtime_streams")
    op.drop_index("ix_realtime_streams_org_id", table_name="realtime_streams")
    op.drop_table("realtime_streams")
