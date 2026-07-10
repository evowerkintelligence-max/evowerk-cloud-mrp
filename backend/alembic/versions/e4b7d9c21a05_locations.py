"""per-location stock: locations + inventory_balances/transactions.location_id

Revision ID: e4b7d9c21a05
Revises: d8a2c4f60b13
Create Date: 2026-07-09

"""
from alembic import op
import sqlalchemy as sa

revision = "e4b7d9c21a05"
down_revision = "d8a2c4f60b13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_locations_org_id", "locations", ["org_id"])
    op.create_unique_constraint("uq_locations_org_code", "locations", ["org_id", "code"])

    # Give every existing org a default location, so the backfill below has a
    # target and existing on-hand keeps a home. gen_random_uuid()/now() are
    # built into Postgres 13+.
    op.execute(
        """
        INSERT INTO locations (id, org_id, code, name, is_default, created_at)
        SELECT gen_random_uuid(), o.id, 'MAIN', 'Main', true, now()
        FROM orgs o
        """
    )

    # inventory_balances.location_id: add nullable, backfill to each row's org
    # default, then lock down NOT NULL + the new (org, item, location) uniqueness.
    op.add_column("inventory_balances", sa.Column("location_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("inventory_transactions", sa.Column("location_id", sa.Uuid(as_uuid=True), nullable=True))
    op.execute(
        """
        UPDATE inventory_balances b
        SET location_id = (
            SELECT l.id FROM locations l WHERE l.org_id = b.org_id AND l.is_default LIMIT 1
        )
        """
    )
    op.execute(
        """
        UPDATE inventory_transactions t
        SET location_id = (
            SELECT l.id FROM locations l WHERE l.org_id = t.org_id AND l.is_default LIMIT 1
        )
        """
    )
    op.alter_column("inventory_balances", "location_id", nullable=False)
    op.alter_column("inventory_transactions", "location_id", nullable=False)

    op.create_foreign_key(
        "fk_inventory_balances_location_id",
        "inventory_balances",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_inventory_transactions_location_id",
        "inventory_transactions",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_inventory_balances_location_id", "inventory_balances", ["location_id"])
    op.create_index("ix_inventory_transactions_location_id", "inventory_transactions", ["location_id"])

    op.drop_constraint("uq_inventory_balances_org_item", "inventory_balances", type_="unique")
    op.create_unique_constraint(
        "uq_inventory_balances_org_item_loc",
        "inventory_balances",
        ["org_id", "item_id", "location_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_inventory_balances_org_item_loc", "inventory_balances", type_="unique")
    op.create_unique_constraint(
        "uq_inventory_balances_org_item", "inventory_balances", ["org_id", "item_id"]
    )
    op.drop_index("ix_inventory_transactions_location_id", table_name="inventory_transactions")
    op.drop_index("ix_inventory_balances_location_id", table_name="inventory_balances")
    op.drop_constraint("fk_inventory_transactions_location_id", "inventory_transactions", type_="foreignkey")
    op.drop_constraint("fk_inventory_balances_location_id", "inventory_balances", type_="foreignkey")
    op.drop_column("inventory_transactions", "location_id")
    op.drop_column("inventory_balances", "location_id")
    op.drop_table("locations")
