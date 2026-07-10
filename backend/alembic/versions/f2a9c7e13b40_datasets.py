"""datasets (analytics dataset layer)

Revision ID: f2a9c7e13b40
Revises: b2c3d4e5f6a7
Create Date: 2026-07-10

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a9c7e13b40"  # pragma: allowlist secret
down_revision = "b2c3d4e5f6a7"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("fmt", sa.String(length=16), nullable=False),
        sa.Column("storage_ext", sa.String(length=16), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_datasets_org_id", "datasets", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_datasets_org_id", table_name="datasets")
    op.drop_table("datasets")
