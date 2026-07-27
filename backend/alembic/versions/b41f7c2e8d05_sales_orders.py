"""sales orders and customers

Revision ID: b41f7c2e8d05
Revises: c7a3d5e91f42
Create Date: 2026-07-27

"""
from alembic import op
import sqlalchemy as sa

revision = "b41f7c2e8d05"  # pragma: allowlist secret
down_revision = "c7a3d5e91f42"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_customers_org_id", "customers", ["org_id"])
    op.create_unique_constraint("uq_customers_org_name", "customers", ["org_id", "name"])

    op.create_table(
        "sales_orders",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("so_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("needed_by", sa.Date(), nullable=False),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sales_orders_org_id", "sales_orders", ["org_id"])
    op.create_index("ix_sales_orders_customer_id", "sales_orders", ["customer_id"])
    op.create_index("ix_sales_orders_status", "sales_orders", ["status"])
    op.create_unique_constraint("uq_sos_org_number", "sales_orders", ["org_id", "so_number"])

    op.create_table(
        "sales_order_lines",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "so_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sales_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("quantity_ordered", sa.Numeric(12, 4), nullable=False),
        sa.Column("quantity_delivered", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_sales_order_lines_org_id", "sales_order_lines", ["org_id"])
    op.create_index("ix_sales_order_lines_so_id", "sales_order_lines", ["so_id"])

    op.add_column(
        "orgs",
        sa.Column("demand_policy", sa.String(20), nullable=False, server_default="consume"),
    )


def downgrade() -> None:
    op.drop_table("sales_order_lines")
    op.drop_table("sales_orders")
    op.drop_table("customers")
    op.drop_column("orgs", "demand_policy")
