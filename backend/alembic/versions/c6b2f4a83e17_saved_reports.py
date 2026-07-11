"""saved reports (custom report builder)

Revision ID: c6b2f4a83e17
Revises: a1c4e7f92d58
Create Date: 2026-07-12

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c6b2f4a83e17"  # pragma: allowlist secret
down_revision = "a1c4e7f92d58"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_saved_reports_org_id", "saved_reports", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_reports_org_id", table_name="saved_reports")
    op.drop_table("saved_reports")
