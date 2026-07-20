"""auth hardening: unique email, account lockout, pending mfa secret, is_active

Revision ID: d6a3d9def5ac
Revises: 0e27b70df337
Create Date: 2026-07-07

"""
from alembic import op
import sqlalchemy as sa

revision = "d6a3d9def5ac"
down_revision = "0e27b70df337"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Superseded by the unique constraint below (which creates its own index).
    op.drop_index("ix_users_email", table_name="users")
    op.create_unique_constraint("uq_users_email", "users", ["email"])

    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column(
        "users", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("pending_mfa_secret", sa.String(255), nullable=True))

    # mfa_secret now stores ciphertext (see app.core.security.encrypt_secret), which is
    # longer than the raw base32 TOTP secret it replaces.
    op.alter_column("users", "mfa_secret", type_=sa.String(255), existing_type=sa.String(64))


def downgrade() -> None:
    # Post-upgrade values are Fernet ciphertext, longer than VARCHAR(64) - shrinking
    # with real ciphertext present would abort the migration. Those secrets are
    # meaningless to pre-hardening app code anyway (it never decrypts them), so
    # clear anything that won't fit before narrowing the column.
    op.execute("UPDATE users SET mfa_secret = NULL WHERE length(mfa_secret) > 64")
    op.alter_column("users", "mfa_secret", type_=sa.String(64), existing_type=sa.String(255))
    op.drop_column("users", "pending_mfa_secret")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
    op.drop_column("users", "is_active")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.create_index("ix_users_email", "users", ["email"])
