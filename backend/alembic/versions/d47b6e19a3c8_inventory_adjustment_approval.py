"""inventory adjustment approval

Revision ID: d47b6e19a3c8
Revises: 80403c683082
Create Date: 2026-07-15

"""
from alembic import op
import sqlalchemy as sa

revision = "d47b6e19a3c8"  # pragma: allowlist secret
down_revision = "80403c683082"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("adjust_approval_threshold", sa.Numeric(12, 4), nullable=False, server_default="50"),
    )

    op.add_column("inventory_transactions", sa.Column("counted_quantity", sa.Numeric(12, 4), nullable=True))
    op.add_column("inventory_transactions", sa.Column("expected_quantity", sa.Numeric(12, 4), nullable=True))
    op.add_column("inventory_transactions", sa.Column("reason_note", sa.String(500), nullable=True))

    op.create_table(
        "inventory_adjustment_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column(
            "location_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity_delta", sa.Numeric(12, 4), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(12, 4), nullable=True),
        sa.Column("expected_quantity_at_submit", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason_code", sa.String(20), nullable=False),
        sa.Column("reason_note", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(500), nullable=True),
    )
    op.create_index("ix_inventory_adjustment_requests_org_id", "inventory_adjustment_requests", ["org_id"])
    op.create_index("ix_inventory_adjustment_requests_item_id", "inventory_adjustment_requests", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_adjustment_requests_item_id", table_name="inventory_adjustment_requests")
    op.drop_index("ix_inventory_adjustment_requests_org_id", table_name="inventory_adjustment_requests")
    op.drop_table("inventory_adjustment_requests")

    op.drop_column("inventory_transactions", "reason_note")
    op.drop_column("inventory_transactions", "expected_quantity")
    op.drop_column("inventory_transactions", "counted_quantity")

    op.drop_column("orgs", "adjust_approval_threshold")
