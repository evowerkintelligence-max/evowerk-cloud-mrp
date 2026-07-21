"""fastapi-users: is_superuser/is_verified columns, oauth_accounts table

Revision ID: a1f4c8b02d17
Revises: 9ea16e274e9a
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa

revision = "a1f4c8b02d17"  # pragma: allowlist secret
down_revision = "9ea16e274e9a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Existing rows backfilled True (grandfathered in) via server_default; only
    # new registrations get is_verified=False, set explicitly by UserManager.create().
    op.add_column(
        "users",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Drop the server defaults after backfill so future inserts must supply the
    # value explicitly (matches every other boolean column on this table).
    op.alter_column("users", "is_superuser", server_default=None)
    op.alter_column("users", "is_verified", server_default=None)

    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("oauth_name", sa.String(length=100), nullable=False),
        sa.Column("access_token", sa.String(length=1024), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.Column("refresh_token", sa.String(length=1024), nullable=True),
        sa.Column("account_id", sa.String(length=320), nullable=False),
        sa.Column("account_email", sa.String(length=320), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oauth_accounts_user_id", "oauth_accounts", ["user_id"])
    op.create_index("ix_oauth_accounts_oauth_name", "oauth_accounts", ["oauth_name"])
    op.create_index("ix_oauth_accounts_account_id", "oauth_accounts", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_accounts_account_id", table_name="oauth_accounts")
    op.drop_index("ix_oauth_accounts_oauth_name", table_name="oauth_accounts")
    op.drop_index("ix_oauth_accounts_user_id", table_name="oauth_accounts")
    op.drop_table("oauth_accounts")
    op.drop_column("users", "is_verified")
    op.drop_column("users", "is_superuser")
