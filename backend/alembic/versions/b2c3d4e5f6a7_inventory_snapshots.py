"""inventory daily snapshots (morning brief)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_constraint(
        "uq_inventory_daily_snapshots_org_item_date", "inventory_daily_snapshots", type_="unique"
    )
    op.drop_index("ix_inventory_daily_snapshots_snapshot_date", table_name="inventory_daily_snapshots")
    op.drop_index("ix_inventory_daily_snapshots_item_id", table_name="inventory_daily_snapshots")
    op.drop_index("ix_inventory_daily_snapshots_org_id", table_name="inventory_daily_snapshots")
    op.drop_table("inventory_daily_snapshots")
