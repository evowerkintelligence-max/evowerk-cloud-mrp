"""operator registry

Revision ID: b3f8a2d914c7
Revises: 7d1a9e4c2b60
Create Date: 2026-07-29

"""
from alembic import op
import sqlalchemy as sa

revision = "b3f8a2d914c7"  # pragma: allowlist secret
down_revision = "7d1a9e4c2b60"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operators",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("badge_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operators_org_id", "operators", ["org_id"])
    op.create_unique_constraint(
        "uq_operators_org_normalized_name", "operators", ["org_id", "normalized_name"]
    )
    op.create_index(
        "uq_operators_org_badge",
        "operators",
        ["org_id", "badge_id"],
        unique=True,
        postgresql_where=sa.text("badge_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("operators")
