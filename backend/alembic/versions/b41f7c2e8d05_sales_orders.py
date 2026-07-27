"""sales orders and customers

Revision ID: b41f7c2e8d05
Revises: c7a3d5e91f42
Create Date: 2026-07-27

"""
from alembic import context, op
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
    _set_sales_entitlement(granted=True)


def downgrade() -> None:
    _set_sales_entitlement(granted=False)
    op.drop_table("sales_order_lines")
    op.drop_table("sales_orders")
    op.drop_table("customers")
    op.drop_column("orgs", "demand_policy")


# The feature key this migration ships, hardcoded rather than imported from
# app.core.features so a later edit to that registry cannot rewrite history.
_SALES_FEATURE = "sales"

# jsonb_exists rather than the `?` operator: `?` collides with the DBAPI
# paramstyle and would be read as a bind parameter.
_OFFLINE_GRANT_SQL = """
UPDATE orgs
   SET enabled_features = (enabled_features::jsonb || '["sales"]'::jsonb)::json
 WHERE jsonb_array_length(enabled_features::jsonb) > 0
   AND NOT jsonb_exists(enabled_features::jsonb, 'sales')
"""

_OFFLINE_REVOKE_SQL = """
UPDATE orgs
   SET enabled_features = ((enabled_features::jsonb) - 'sales')::json
 WHERE jsonb_exists(enabled_features::jsonb, 'sales')
"""


def _set_sales_entitlement(*, granted: bool, connection=None) -> None:
    """Add or remove "sales" on every org already holding entitlements.

    Org.enabled_features is a stored JSON array read by require_feature (see
    app.api.deps), so adding a key to the FEATURES registry does nothing for
    rows already in the database. Without this backfill the whole feature
    ships invisible to every existing customer: 403 on /customers and
    /sales-orders, and no nav entry. Same reasoning as b2e9f4a71c83, which
    grandfathered every org into Core when gating first went live.

    An org with NO entitlements is left alone: an empty list is a deliberate
    "not provisioned yet" state, not a Core org, so granting it access here
    would hand out a feature nobody asked for.

    `connection` defaults to the migration's own bind; it is a parameter so
    backend/tests/test_sales_entitlement_backfill.py can exercise this against
    a scratch database. The suite builds its schema with create_all and never
    runs Alembic, so without that seam this code would ship untested.
    """
    orgs = sa.table("orgs", sa.column("id", sa.Uuid()), sa.column("enabled_features", sa.JSON()))
    if connection is None:
        if context.is_offline_mode():
            # Offline (--sql) mode cannot run the SELECT the row loop below
            # needs, so emit the equivalent as one statement. Postgres-specific
            # because that is the only engine a real deployment renders SQL
            # for; every online path (CI, docker-compose, ECS, Container Apps)
            # takes the portable loop instead.
            op.execute(_OFFLINE_GRANT_SQL if granted else _OFFLINE_REVOKE_SQL)
            return
        connection = op.get_bind()
    for org_id, features in connection.execute(sa.select(orgs.c.id, orgs.c.enabled_features)):
        current = list(features or [])
        if not current:
            continue
        if granted and _SALES_FEATURE not in current:
            updated = [*current, _SALES_FEATURE]
        elif not granted and _SALES_FEATURE in current:
            updated = [f for f in current if f != _SALES_FEATURE]
        else:
            continue
        connection.execute(
            orgs.update().where(orgs.c.id == org_id).values(enabled_features=updated)
        )
