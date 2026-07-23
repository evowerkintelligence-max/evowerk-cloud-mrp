"""org.enabled_features, user.is_staff

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


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("enabled_features", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "users",
        sa.Column("is_staff", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Grandfather every pre-existing org into Core so deploying this
    # migration doesn't lock every current customer out the instant
    # require_feature() gating goes live. Staff bootstrap and any
    # Premium-tier grants are still a manual step - see
    # docs/superpowers/plans/2026-07-23-feature-entitlements-deploy-runbook.md.
    orgs = sa.table("orgs", sa.column("enabled_features", sa.JSON()))
    op.execute(orgs.update().values(enabled_features=_CORE_FEATURES_AT_MIGRATION_TIME))


def downgrade() -> None:
    op.drop_column("users", "is_staff")
    op.drop_column("orgs", "enabled_features")
