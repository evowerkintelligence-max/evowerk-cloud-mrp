"""work centers

Revision ID: 800c8b11b0c4
Revises: a2f7c5e91d84
Create Date: 2026-07-17 05:42:01.360815

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '800c8b11b0c4'  # pragma: allowlist secret
down_revision = 'a2f7c5e91d84'  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_centers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("machine_capacity", sa.Integer(), nullable=False),
        sa.Column("default_cycle_time_seconds", sa.Float(), nullable=True),
        sa.Column("default_mtbf_hours", sa.Float(), nullable=True),
        sa.Column("default_mttr_hours", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "code", name="uq_work_centers_org_code"),
    )
    op.create_index("ix_work_centers_org_id", "work_centers", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_work_centers_org_id", table_name="work_centers")
    op.drop_table("work_centers")
