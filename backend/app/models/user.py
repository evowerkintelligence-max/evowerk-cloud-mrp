from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    # Python attribute is `hashed_password` (not `password_hash`) because
    # fastapi-users' BaseUserManager reads/writes this exact attribute name
    # directly (`user.hashed_password`). The DB column name is left as
    # `password_hash` via the mapped_column name override below, so this is a
    # Python-only rename - no data migration, every existing hash keeps working.
    hashed_password: Mapped[str] = mapped_column("password_hash", String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # fastapi-users schema requirement - app authorization decisions still use
    # `role` above, this column is unused by application logic.
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # New registrations start unverified (fastapi-users' UserManager.create()
    # sets this False explicitly); existing rows are backfilled True by the
    # migration so no deployed customer's users get locked out retroactively.
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Encrypted at rest (see app.core.security.encrypt_secret) - these hold ciphertext,
    # not the raw TOTP secret, so they're wider than the 32-char base32 secret itself.
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Set during /mfa/enroll, only promoted to mfa_secret once /mfa/verify succeeds -
    # so an enroll call alone can never disable or replace an already-active MFA secret.
    pending_mfa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # selectin (not joined): joined eager-loading a to-many collection makes
    # SQLAlchemy require .unique() on every Result, which would break the
    # many existing `select(User)...)` call sites across this codebase
    # (login, get_current_user, test fixtures) that don't call it. selectin
    # issues a separate query instead - no duplicate rows, no .unique()
    # needed, and it's async-session-safe (no implicit lazy IO).
    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship(lazy="selectin")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class OAuthAccount(Base):
    """A linked Google/Microsoft identity for a User. Matches the attribute
    names fastapi_users_db_sqlalchemy.SQLAlchemyUserDatabase expects (checked
    structurally, not via inheritance) - see app/core/users.py."""

    __tablename__ = "oauth_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    oauth_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    access_token: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    account_id: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    account_email: Mapped[str] = mapped_column(String(320), nullable=False)
