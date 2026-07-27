"""org.enabled_features, staff_accounts

Revision ID: b2e9f4a71c83
Revises: a1f4c8b02d17
Create Date: 2026-07-23

"""
from alembic import op
import sqlalchemy as sa

revision = "b2e9f4a71c83"  # pragma: allowlist secret
down_revision = "a1f4c8b02d17"  # pragma: allowlist secret
branch_labels = None
depends_on = None


# Core-tier feature keys as of this migration's authorship (see
# backend/app/core/features.py's FEATURES/CORE_FEATURES). Hardcoded rather
# than imported so this migration's behavior stays fixed even if the
# registry changes later.
_CORE_FEATURES_AT_MIGRATION_TIME = [
    "boms", "procurement", "lots", "warehouse", "planning", "plant_summary",
    "oee", "pace_tracking", "downtime", "downtime_tracking", "throughput",
    "scrap", "shifts", "spc", "kpis", "kpi_dashboards", "reports", "data_chat",
]

# Declares which entitlement keys this migration hands to orgs that already
# exist. backend/tests/test_feature_migration_coverage.py unions this across
# every migration and fails if a core key in the registry is not covered, so
# a new feature cannot ship invisible to current customers. Pure declaration,
# no behavior of its own.
GRANTS_FEATURES = list(_CORE_FEATURES_AT_MIGRATION_TIME)


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("enabled_features", sa.JSON(), nullable=False, server_default="[]"),
    )
    # Grandfather every pre-existing org into Core so deploying this
    # migration doesn't lock every current customer out the instant
    # require_feature() gating goes live. Premium-tier grants are still a
    # manual step - see
    # docs/superpowers/plans/2026-07-23-feature-entitlements-deploy-runbook.md.
    orgs = sa.table("orgs", sa.column("enabled_features", sa.JSON()))
    op.execute(orgs.update().values(enabled_features=_CORE_FEATURES_AT_MIGRATION_TIME))

    op.create_table(
        "staff_accounts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("employee_id", sa.String(), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_staff_accounts_employee_id", "staff_accounts", ["employee_id"])


def downgrade() -> None:
    op.drop_index("ix_staff_accounts_employee_id", table_name="staff_accounts")
    op.drop_table("staff_accounts")
    op.drop_column("orgs", "enabled_features")
