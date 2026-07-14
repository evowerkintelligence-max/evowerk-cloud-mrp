"""supplier relationship depth

Revision ID: c8a1e5f93d64
Revises: a4f7c2e91b58
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "c8a1e5f93d64"  # pragma: allowlist secret
down_revision = "a4f7c2e91b58"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("address_line1", sa.String(255), nullable=True))
    op.add_column("suppliers", sa.Column("address_line2", sa.String(255), nullable=True))
    op.add_column("suppliers", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("suppliers", sa.Column("state", sa.String(100), nullable=True))
    op.add_column("suppliers", sa.Column("postal_code", sa.String(20), nullable=True))
    op.add_column("suppliers", sa.Column("country", sa.String(100), nullable=True))
    op.add_column("suppliers", sa.Column("payment_terms_days", sa.Integer(), nullable=True))
    op.add_column("suppliers", sa.Column("lead_time_days", sa.Integer(), nullable=True))
    op.add_column(
        "suppliers", sa.Column("tags", sa.JSON(), nullable=False, server_default="[]")
    )

    op.create_table(
        "supplier_contacts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "supplier_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_supplier_contacts_org_id", "supplier_contacts", ["org_id"])
    op.create_index("ix_supplier_contacts_supplier_id", "supplier_contacts", ["supplier_id"])

    op.add_column(
        "purchase_order_receipt_lines",
        sa.Column("quantity_rejected", sa.Numeric(12, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "purchase_order_receipt_lines", sa.Column("reject_reason", sa.String(500), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("purchase_order_receipt_lines", "reject_reason")
    op.drop_column("purchase_order_receipt_lines", "quantity_rejected")

    op.drop_index("ix_supplier_contacts_supplier_id", table_name="supplier_contacts")
    op.drop_index("ix_supplier_contacts_org_id", table_name="supplier_contacts")
    op.drop_table("supplier_contacts")

    op.drop_column("suppliers", "tags")
    op.drop_column("suppliers", "lead_time_days")
    op.drop_column("suppliers", "payment_terms_days")
    op.drop_column("suppliers", "country")
    op.drop_column("suppliers", "postal_code")
    op.drop_column("suppliers", "state")
    op.drop_column("suppliers", "city")
    op.drop_column("suppliers", "address_line2")
    op.drop_column("suppliers", "address_line1")
