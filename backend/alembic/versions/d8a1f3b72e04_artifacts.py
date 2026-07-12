"""artifacts

Revision ID: d8a1f3b72e04
Revises: c4f8a2e60d97
Create Date: 2026-07-12

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8a1f3b72e04"  # pragma: allowlist secret
down_revision = "c4f8a2e60d97"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("report_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_org_id", "artifacts", ["org_id"])
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifacts_report_id", "artifacts", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_report_id", table_name="artifacts")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_index("ix_artifacts_org_id", table_name="artifacts")
    op.drop_table("artifacts")
