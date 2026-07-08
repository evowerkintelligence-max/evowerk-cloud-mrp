"""inventory ledger: inventory_balances, inventory_transactions

Revision ID: c3f5a1b8e246
Revises: b7c1e9a4f2d8
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "c3f5a1b8e246"
down_revision = "b7c1e9a4f2d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_balances",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("quantity_on_hand", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inventory_balances_org_id", "inventory_balances", ["org_id"])
    op.create_index("ix_inventory_balances_item_id", "inventory_balances", ["item_id"])
    op.create_unique_constraint(
        "uq_inventory_balances_org_item", "inventory_balances", ["org_id", "item_id"]
    )

    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("transaction_type", sa.String(20), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(12, 4), nullable=False),
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
    op.create_index("ix_inventory_transactions_org_id", "inventory_transactions", ["org_id"])
    op.create_index("ix_inventory_transactions_item_id", "inventory_transactions", ["item_id"])
    op.create_index(
        "ix_inventory_transactions_source_receipt_line_id",
        "inventory_transactions",
        ["source_receipt_line_id"],
    )


def downgrade() -> None:
    op.drop_table("inventory_transactions")
    op.drop_table("inventory_balances")
