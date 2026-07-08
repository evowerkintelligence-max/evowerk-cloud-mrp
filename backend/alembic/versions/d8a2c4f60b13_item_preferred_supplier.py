"""item preferred_supplier_id

Revision ID: d8a2c4f60b13
Revises: c3f5a1b8e246
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "d8a2c4f60b13"
down_revision = "c3f5a1b8e246"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("preferred_supplier_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_items_preferred_supplier_id",
        "items",
        "suppliers",
        ["preferred_supplier_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_items_preferred_supplier_id", "items", ["preferred_supplier_id"])


def downgrade() -> None:
    op.drop_index("ix_items_preferred_supplier_id", table_name="items")
    op.drop_constraint("fk_items_preferred_supplier_id", "items", type_="foreignkey")
    op.drop_column("items", "preferred_supplier_id")
