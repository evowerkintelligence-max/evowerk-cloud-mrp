"""per-location stock for lot-tracked items: lots.location_id

Revision ID: f6a3c9d18e72
Revises: e4b7d9c21a05
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa

revision = "f6a3c9d18e72"
down_revision = "e4b7d9c21a05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # lots.location_id: add nullable, backfill to each row's org default (every
    # org already has one - see the locations migration), then lock down NOT
    # NULL + the new (org, item, lot_number, location) uniqueness.
    op.add_column("lots", sa.Column("location_id", sa.Uuid(as_uuid=True), nullable=True))
    op.execute(
        """
        UPDATE lots o
        SET location_id = (
            SELECT l.id FROM locations l WHERE l.org_id = o.org_id AND l.is_default LIMIT 1
        )
        """
    )
    op.alter_column("lots", "location_id", nullable=False)

    op.create_foreign_key(
        "fk_lots_location_id", "lots", "locations", ["location_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_lots_location_id", "lots", ["location_id"])

    op.drop_constraint("uq_lots_org_item_number", "lots", type_="unique")
    op.create_unique_constraint(
        "uq_lots_org_item_number_location", "lots", ["org_id", "item_id", "lot_number", "location_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_lots_org_item_number_location", "lots", type_="unique")
    op.create_unique_constraint("uq_lots_org_item_number", "lots", ["org_id", "item_id", "lot_number"])
    op.drop_index("ix_lots_location_id", table_name="lots")
    op.drop_constraint("fk_lots_location_id", "lots", type_="foreignkey")
    op.drop_column("lots", "location_id")
