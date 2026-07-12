"""maintenance_schedules

Revision ID: f2b7e9c1a486
Revises: e91c4a5d8f37
Create Date: 2026-07-12

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2b7e9c1a486"  # pragma: allowlist secret
down_revision = "e91c4a5d8f37"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=2000), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("scheduled_date", sa.String(length=32), nullable=True),
        sa.Column("assigned_to", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pm_hours", sa.Float(), nullable=False),
        sa.Column("notes", sa.String(length=4000), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maintenance_schedules_org_id", "maintenance_schedules", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_schedules_org_id", table_name="maintenance_schedules")
    op.drop_table("maintenance_schedules")
