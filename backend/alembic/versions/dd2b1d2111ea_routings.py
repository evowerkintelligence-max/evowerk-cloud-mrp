"""routings

Revision ID: dd2b1d2111ea
Revises: 9dcd312645ed
Create Date: 2026-07-17 06:07:06.943404

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dd2b1d2111ea'  # pragma: allowlist secret
down_revision = '9dcd312645ed'  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "item_id", "version", name="uq_routings_org_item_version"),
    )
    op.create_index("ix_routings_org_id", "routings", ["org_id"])
    op.create_index("ix_routings_item_id", "routings", ["item_id"])

    op.create_table(
        "routing_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("routing_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("work_center_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_time_seconds", sa.Float(), nullable=True),
        sa.Column("setup_time_seconds", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["routing_id"], ["routings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_center_id"], ["work_centers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("routing_id", "sequence", name="uq_routing_steps_routing_sequence"),
    )
    op.create_index("ix_routing_steps_org_id", "routing_steps", ["org_id"])
    op.create_index("ix_routing_steps_routing_id", "routing_steps", ["routing_id"])


def downgrade() -> None:
    op.drop_index("ix_routing_steps_routing_id", table_name="routing_steps")
    op.drop_index("ix_routing_steps_org_id", table_name="routing_steps")
    op.drop_table("routing_steps")
    op.drop_index("ix_routings_item_id", table_name="routings")
    op.drop_index("ix_routings_org_id", table_name="routings")
    op.drop_table("routings")
