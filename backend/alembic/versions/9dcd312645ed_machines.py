"""machines

Revision ID: 9dcd312645ed
Revises: 800c8b11b0c4
Create Date: 2026-07-17 05:53:55.824548

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9dcd312645ed'  # pragma: allowlist secret
down_revision = '800c8b11b0c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("work_center_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("mtbf_hours", sa.Float(), nullable=True),
        sa.Column("mttr_hours", sa.Float(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_center_id"], ["work_centers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "code", name="uq_machines_org_code"),
    )
    op.create_index("ix_machines_org_id", "machines", ["org_id"])
    op.create_index("ix_machines_work_center_id", "machines", ["work_center_id"])


def downgrade() -> None:
    op.drop_index("ix_machines_work_center_id", table_name="machines")
    op.drop_index("ix_machines_org_id", table_name="machines")
    op.drop_table("machines")
