"""drop inventory_daily_snapshots (inventory analytics removal)

Revision ID: c4d5e6f7a8b9
Revises: d5b0c4f936e2
Create Date: 2026-07-31

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7a8b9"  # pragma: allowlist secret
down_revision = "d5b0c4f936e2"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("inventory_daily_snapshots")


def downgrade() -> None:
    op.create_table(
        "inventory_daily_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("on_hand", sa.Numeric(12, 4), nullable=False),
        sa.Column("avg_daily_demand", sa.Float(), nullable=False),
        sa.Column("days_of_supply", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inventory_daily_snapshots_org_id", "inventory_daily_snapshots", ["org_id"])
    op.create_index("ix_inventory_daily_snapshots_item_id", "inventory_daily_snapshots", ["item_id"])
    op.create_index(
        "ix_inventory_daily_snapshots_snapshot_date", "inventory_daily_snapshots", ["snapshot_date"]
    )
    op.create_unique_constraint(
        "uq_inventory_daily_snapshots_org_item_date",
        "inventory_daily_snapshots",
        ["org_id", "item_id", "snapshot_date"],
    )
