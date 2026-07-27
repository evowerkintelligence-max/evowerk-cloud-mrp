"""work orders

Revision ID: 3caf4cd09df8
Revises: b41f7c2e8d05
Create Date: 2026-07-27

"""
from alembic import context, op
import sqlalchemy as sa

revision = "3caf4cd09df8"  # pragma: allowlist secret
down_revision = "b41f7c2e8d05"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_orders",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("bom_id", sa.Uuid(as_uuid=True), sa.ForeignKey("boms.id"), nullable=False),
        sa.Column("routing_id", sa.Uuid(as_uuid=True), sa.ForeignKey("routings.id"), nullable=True),
        sa.Column("wo_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("quantity_planned", sa.Numeric(12, 4), nullable=False),
        sa.Column("quantity_good", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("quantity_scrapped", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("scrap_reason", sa.String(500), nullable=True),
        sa.Column("remainder_disposition", sa.String(20), nullable=True),
        sa.Column("location_id", sa.Uuid(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("scheduled_start", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_work_orders_org_id", "work_orders", ["org_id"])
    op.create_index("ix_work_orders_item_id", "work_orders", ["item_id"])
    op.create_index("ix_work_orders_status", "work_orders", ["status"])
    op.create_unique_constraint("uq_wos_org_number", "work_orders", ["org_id", "wo_number"])

    op.create_table(
        "work_order_components",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wo_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("work_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("quantity_required", sa.Numeric(12, 4), nullable=False),
        sa.Column("quantity_consumed", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_work_order_components_org_id", "work_order_components", ["org_id"])
    op.create_index("ix_work_order_components_wo_id", "work_order_components", ["wo_id"])

    op.create_table(
        "work_order_component_lots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wo_component_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("work_order_components.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lot_id", sa.Uuid(as_uuid=True), sa.ForeignKey("lots.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
    )
    op.create_index(
        "ix_work_order_component_lots_org_id", "work_order_component_lots", ["org_id"]
    )
    op.create_index(
        "ix_work_order_component_lots_wo_component_id",
        "work_order_component_lots",
        ["wo_component_id"],
    )

    _set_production_entitlement(granted=True)


def downgrade() -> None:
    _set_production_entitlement(granted=False)
    op.drop_table("work_order_component_lots")
    op.drop_table("work_order_components")
    op.drop_table("work_orders")


# The feature key this migration ships, hardcoded rather than imported from
# app.core.features so a later edit to that registry cannot rewrite history.
_PRODUCTION_FEATURE = "production"

# See b2e9f4a71c83's GRANTS_FEATURES: declares what this migration hands to
# orgs that already exist, so test_feature_migration_coverage can prove no
# core key ships without a backfill.
GRANTS_FEATURES = [_PRODUCTION_FEATURE]

# jsonb_exists rather than the `?` operator: `?` collides with the DBAPI
# paramstyle and would be read as a bind parameter.
_OFFLINE_GRANT_SQL = """
UPDATE orgs
   SET enabled_features = (enabled_features::jsonb || '["production"]'::jsonb)::json
 WHERE jsonb_array_length(enabled_features::jsonb) > 0
   AND NOT jsonb_exists(enabled_features::jsonb, 'production')
"""

_OFFLINE_REVOKE_SQL = """
UPDATE orgs
   SET enabled_features = ((enabled_features::jsonb) - 'production')::json
 WHERE jsonb_exists(enabled_features::jsonb, 'production')
"""


def _set_production_entitlement(*, granted: bool, connection=None) -> None:
    """Add or remove "production" on every org already holding entitlements.

    Org.enabled_features is a stored JSON array read by require_feature (see
    app.api.deps), so adding a key to the FEATURES registry does nothing for
    rows already in the database. Without this backfill the whole feature
    ships invisible to every existing customer: 403 on /work-orders, and no
    nav entry. Same reasoning as b2e9f4a71c83, which grandfathered every org
    into Core when gating first went live.

    An org with NO entitlements is left alone: an empty list is a deliberate
    "not provisioned yet" state, not a Core org, so granting it access here
    would hand out a feature nobody asked for.

    `connection` defaults to the migration's own bind; it is a parameter so
    a scratch-database test can exercise this without running Alembic, the
    same seam b41f7c2e8d05's _set_sales_entitlement offers.
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
        if granted and _PRODUCTION_FEATURE not in current:
            updated = [*current, _PRODUCTION_FEATURE]
        elif not granted and _PRODUCTION_FEATURE in current:
            updated = [f for f in current if f != _PRODUCTION_FEATURE]
        else:
            continue
        connection.execute(
            orgs.update().where(orgs.c.id == org_id).values(enabled_features=updated)
        )
