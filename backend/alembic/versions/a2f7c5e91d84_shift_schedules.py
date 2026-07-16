"""shift schedules

Revision ID: a2f7c5e91d84
Revises: e7c1a4f83b92
Create Date: 2026-07-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a2f7c5e91d84"  # pragma: allowlist secret
down_revision = "e7c1a4f83b92"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shift_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("shift_label", sa.String(length=64), nullable=False),
        sa.Column("start_time", sa.String(length=5), nullable=False),
        sa.Column("end_time", sa.String(length=5), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "shift_label", name="uq_shift_schedules_org_label"),
    )
    op.create_index("ix_shift_schedules_org_id", "shift_schedules", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_shift_schedules_org_id", table_name="shift_schedules")
    op.drop_table("shift_schedules")
