"""bom: items, boms, bom_lines

Revision ID: ad775f9b6888
Revises: 0c1e0833260f
Create Date: 2026-07-07

"""
from alembic import op
import sqlalchemy as sa

revision = "ad775f9b6888"
down_revision = "0c1e0833260f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("item_type", sa.String(50), nullable=False, server_default="component"),
        sa.Column("unit_of_measure", sa.String(20), nullable=False, server_default="ea"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_items_org_id", "items", ["org_id"])
    op.create_unique_constraint("uq_items_org_sku", "items", ["org_id", "sku"])

    op.create_table(
        "boms",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_boms_org_id", "boms", ["org_id"])
    op.create_index("ix_boms_item_id_status", "boms", ["item_id", "status"])
    op.create_unique_constraint("uq_boms_org_item_version", "boms", ["org_id", "item_id", "version"])

    op.create_table(
        "bom_lines",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bom_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("boms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("component_item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_bom_lines_org_id", "bom_lines", ["org_id"])
    op.create_index("ix_bom_lines_bom_id", "bom_lines", ["bom_id"])


def downgrade() -> None:
    op.drop_table("bom_lines")
    op.drop_table("boms")
    op.drop_table("items")
