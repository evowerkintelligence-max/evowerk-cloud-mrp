"""report inbox items (send to report capture)

Revision ID: f3a8b2e7c641
Revises: d47b6e19a3c8
Create Date: 2026-07-15

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a8b2e7c641"  # pragma: allowlist secret
down_revision = "d47b6e19a3c8"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_inbox_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_inbox_items_org_id", "report_inbox_items", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_report_inbox_items_org_id", table_name="report_inbox_items")
    op.drop_table("report_inbox_items")
