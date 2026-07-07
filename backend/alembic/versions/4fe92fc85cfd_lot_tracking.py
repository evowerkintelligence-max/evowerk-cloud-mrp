"""lot tracking: items.is_lot_tracked, lots, lot_transactions

Revision ID: 4fe92fc85cfd
Revises: 1e284768aa62
Create Date: 2026-07-07

"""
from alembic import op
import sqlalchemy as sa

revision = "4fe92fc85cfd"
down_revision = "1e284768aa62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items", sa.Column("is_lot_tracked", sa.Boolean(), nullable=False, server_default=sa.false())
    )

    op.create_table(
        "lots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("lot_number", sa.String(100), nullable=False),
        sa.Column("quantity_received", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("quantity_remaining", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lots_org_id", "lots", ["org_id"])
    op.create_index("ix_lots_item_id", "lots", ["item_id"])
    op.create_index("ix_lots_status", "lots", ["status"])
    op.create_unique_constraint("uq_lots_org_item_number", "lots", ["org_id", "item_id", "lot_number"])

    op.create_table(
        "lot_transactions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lot_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("lots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transaction_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason", sa.String(100), nullable=True),
        sa.Column(
            "source_receipt_line_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("purchase_order_receipt_lines.id"),
            nullable=True,
        ),
        sa.Column("created_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lot_transactions_org_id", "lot_transactions", ["org_id"])
    op.create_index("ix_lot_transactions_lot_id", "lot_transactions", ["lot_id"])
    op.create_index(
        "ix_lot_transactions_source_receipt_line_id", "lot_transactions", ["source_receipt_line_id"]
    )


def downgrade() -> None:
    op.drop_table("lot_transactions")
    op.drop_table("lots")
    op.drop_column("items", "is_lot_tracked")
