# Auth: Migrate to fastapi-users Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-rolled JWT/argon2 auth stack in `backend/app/api/routes/auth.py` with `fastapi-users`, adding password reset, email verification, and Google/Microsoft OAuth login, while preserving TOTP MFA, audit logging, rate limiting, account lockout, and the multi-tenant org model exactly as they behave today.

**Architecture:** `fastapi-users` supplies `UserManager`/`SQLAlchemyUserDatabase` abstractions and the register/forgot-password/reset-password/verify routers. A custom `UserManager` subclass injects org bootstrapping, audit logging, and account lockout. `/auth/login` stays a hand-written route (MFA gating + our existing refresh-token issuance need a response shape fastapi-users' built-in login router doesn't produce), now using `fastapi-users`' `password_helper` instead of hand-rolled passlib calls. OAuth (Google/Microsoft) is wired as custom callback routes calling `UserManager.oauth_callback()` directly, so the email-verified-by-provider check can gate auto-linking per-request.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, `fastapi-users[sqlalchemy]`, `httpx-oauth`, `boto3` (SES), Alembic, pytest/pytest-asyncio, React/Vite/TypeScript, Vitest, Terraform (AWS).

## Global Constraints

- No rehashing or migration of existing password hashes — `PasswordHelper` wraps the same argon2 `CryptContext` already in `app/core/security.py`. Every currently-seeded user (including `dev@evowerk.dev` on the AWS deployment) must keep working unchanged.
- Existing users are backfilled `is_verified=true`; only new registrations require verification.
- `backend/tests/test_auth.py`'s existing assertions (status codes, response shapes) must keep passing without modification except where this plan explicitly calls out a change.
- No Azure email work — the Azure Terraform module doesn't exist yet.
- Refresh-token rotation (`RefreshToken` model, `/auth/refresh`, `/auth/logout`) stays exactly as implemented today.
- OAuth auto-link only when the provider asserts the email is verified; fail closed (no auto-link, separate account) whenever that can't be determined.

---

## File Structure

New files:
- `backend/app/core/users.py` — `UserManager`, `get_user_db`, `get_user_manager`, `UserCreate`/`UserRead`/`UserUpdate` schemas, the `FastAPIUsers` instance.
- `backend/app/core/email.py` — `send_email()` interface + SES (`boto3`) implementation + local dev log-only fallback.
- `backend/app/api/routes/oauth.py` — custom Google/Microsoft authorize + callback routes.
- `backend/alembic/versions/<rev>_fastapi_users_columns.py` — schema migration.
- `frontend/src/pages/ForgotPassword.tsx`, `frontend/src/pages/ResetPassword.tsx`, `frontend/src/pages/VerifyEmail.tsx`, `frontend/src/pages/OAuthCallback.tsx`.

Modified files:
- `backend/app/models/user.py` — `User.hashed_password` (renamed from `password_hash`, same DB column), `is_superuser`, `is_verified`, new `OAuthAccount` model.
- `backend/app/api/routes/auth.py` — replace hand-rolled `/register` and `/login` with `fastapi-users`-backed versions; `/refresh`, `/logout`, `/mfa/enroll`, `/mfa/verify` unchanged.
- `backend/app/main.py` — mount `oauth.router`.
- `backend/app/core/config.py` — new settings (`frontend_base_url`, `ses_from_email`, `aws_region`, OAuth client id/secret pairs).
- `backend/requirements.txt` — add `fastapi-users[sqlalchemy]`, `httpx-oauth`, `boto3`.
- `backend/scripts/seed_dev_user.py` — `password_hash=` → `hashed_password=`.
- ~15 backend test fixture files — same kwarg rename (mechanical, see Task 2).
- `frontend/src/pages/Login.tsx`, `frontend/src/App.tsx` — new links/routes.
- `infra/terraform/aws/secrets.tf` (new SES resources file: `ses.tf`), `iam.tf`, `ecs.tf`, `variables.tf`.
- `docs/superpowers/specs/2026-07-21-auth-fastapi-users-migration-design.md` — no change needed, already reflects this plan.

---

### Task 1: Add backend dependencies

**Files:**
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: `fastapi_users`, `fastapi_users_db_sqlalchemy`, `httpx_oauth`, `boto3` importable in the backend venv.

- [ ] **Step 1: Add the three new dependencies**

Add these lines to `backend/requirements.txt`, right after the existing `pyjwt>=2.8.0` line (keeping the auth-related deps grouped):

```
fastapi-users[sqlalchemy]>=13.0.0,<14.0.0
httpx-oauth>=0.15.0,<1.0.0
boto3>=1.34.0
```

- [ ] **Step 2: Install and verify import**

Run: `cd backend && pip install -r requirements.txt`
Then: `python -c "import fastapi_users, fastapi_users_db_sqlalchemy, httpx_oauth, boto3; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "build: add fastapi-users, httpx-oauth, boto3 dependencies"
```

---

### Task 2: User model changes + migration + fixture rename

**Files:**
- Modify: `backend/app/models/user.py`
- Create: `backend/alembic/versions/<new_rev>_fastapi_users_columns.py`
- Modify: `backend/scripts/seed_dev_user.py`
- Modify: every backend test file matched by `grep -rl "password_hash=" backend/tests/`
- Test: `backend/tests/test_auth.py` (existing suite is the regression check for this task)

**Interfaces:**
- Produces: `User.hashed_password: str` (Python attribute; DB column stays `password_hash`), `User.is_superuser: bool`, `User.is_verified: bool`, `User.oauth_accounts: list[OAuthAccount]`, new `OAuthAccount` model with columns `id, user_id, oauth_name, access_token, expires_at, refresh_token, account_id, account_email`.

- [ ] **Step 1: Update the User model and add OAuthAccount**

Edit `backend/app/models/user.py`. Replace the `password_hash` column and add the two new columns and the relationship, and append the `OAuthAccount` model:

```python
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
```

- [ ] **Step 2: Find the current Alembic head**

Run: `cd backend && python -m alembic heads`
Expected output: `9ea16e274e9a (head)`

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/a1f4c8b02d17_fastapi_users_columns.py`:

```python
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
```

- [ ] **Step 4: Rename `password_hash=` to `hashed_password=` across test fixtures and the seed script**

This is a mechanical keyword-argument rename (SQLAlchemy's generated `User.__init__` uses the Python attribute name `hashed_password`, not the DB column name `password_hash`). Every file that constructs `User(..., password_hash=...)` directly needs the same rename.

Run this exact command from the repo root (PowerShell):

```powershell
Get-ChildItem -Recurse -Include *.py backend/tests, backend/scripts | ForEach-Object {
  (Get-Content $_.FullName -Raw) -replace 'password_hash=', 'hashed_password=' | Set-Content -Encoding utf8 $_.FullName
}
```

Or the Bash equivalent:

```bash
grep -rl 'password_hash=' backend/tests backend/scripts | xargs sed -i 's/password_hash=/hashed_password=/g'
```

- [ ] **Step 5: Verify the rename didn't miss or over-match anything**

Run: `grep -rn "password_hash=" backend/` (Grep tool, path `backend`)
Expected: no matches (only `hashed_password=` should remain; `app/core/security.py`'s `hash_password`/`verify_password` *function names* are untouched since they don't end in `password_hash=`).

- [ ] **Step 6: Run the full backend test suite**

Run: `cd backend && pytest -q`
Expected: all tests pass (this migration is purely additive/renaming - no test assertions should need to change yet; `test_auth.py` will start actually exercising the new model in the next task).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/user.py backend/alembic/versions/a1f4c8b02d17_fastapi_users_columns.py backend/scripts/seed_dev_user.py backend/tests
git commit -m "feat: add fastapi-users columns (is_superuser, is_verified, oauth_accounts)"
```

---

### Task 3: UserManager, password helper, and user_db wiring

**Files:**
- Create: `backend/app/core/users.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_users_manager.py`

**Interfaces:**
- Consumes: `User`, `OAuthAccount` (Task 2); `_pwd_context`, `hash_password` from `app/core/security.py`; `Org`, `Location` models; `log_event` from `app/middleware/audit.py`. <!-- pragma: allowlist secret -->
- Produces: `get_user_db(session) -> SQLAlchemyUserDatabase`, `get_user_manager(user_db) -> UserManager`, `UserCreate`, `UserRead`, `UserUpdate` schemas, `fastapi_users: FastAPIUsers[User, uuid.UUID]`. Later tasks (4, 5, 6, 7) depend on all of these exact names.

- [ ] **Step 1: Add new settings**

Edit `backend/app/core/config.py`, add after `mfa_encryption_key: str`:

```python
    # Base URL of the deployed frontend, used to build links embedded in
    # password-reset and email-verification messages (see app/core/email.py).
    frontend_base_url: str = "http://localhost:5173"

    # SES sender address (must be a verified identity in the target AWS
    # account - see infra/terraform/aws/ses.tf) and the region to call SES in.
    # Empty string means SES isn't configured (see app/core/email.py) - not
    # "production" env, since infra/terraform/aws/ecs.tf always sets
    # ENV=production regardless of whether a sender was ever configured.
    ses_from_email: str = ""
    aws_region: str = "us-east-1"

    # Empty string disables that provider's OAuth routes entirely (see
    # app/api/routes/oauth.py) - lets local dev run without registering
    # OAuth apps with Google/Microsoft.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_users_manager.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.tenant import Org
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def test_user_manager_create_bootstraps_org_and_default_location(db_session):
    from app.core.users import UserCreate, UserManager, get_user_db
    from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
    from app.models.user import OAuthAccount
    from app.core.security import _pwd_context
    from fastapi_users.password import PasswordHelper

    user_db = SQLAlchemyUserDatabase(db_session, User, OAuthAccount)
    manager = UserManager(user_db, PasswordHelper(_pwd_context))

    created = await manager.create(
        UserCreate(org_name="Acme", email="alice@example.com", password="correct-horse-battery-staple1")  # pragma: allowlist secret
    )

    assert created.is_verified is False
    assert created.org_id is not None

    org = (await db_session.execute(select(Org).where(Org.id == created.org_id))).scalar_one()
    assert org.name == "Acme"

    # password hash was produced through the same argon2 context, verifiable
    # via the manager's own password_helper (not a re-hash / different scheme)
    verified, _ = manager.password_helper.verify_and_update(
        "correct-horse-battery-staple1", created.hashed_password
    )
    assert verified is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_users_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.users'`

- [ ] **Step 4: Implement `app/core/users.py`**

```python
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions, schemas
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.password import PasswordHelper
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import _pwd_context
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.location import Location
from app.models.tenant import Org
from app.models.user import OAuthAccount, User

settings = get_settings()


class UserCreate(schemas.BaseUserCreate):
    org_name: str

    @field_validator("password")
    @classmethod
    def password_policy(cls, v: str) -> str:
        if len(v) < 12:
            raise ValueError("Password must be at least 12 characters long")
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one digit")
        return v


class UserRead(schemas.BaseUser[uuid.UUID]):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request and request.client else None


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    def __init__(self, user_db: SQLAlchemyUserDatabase, password_helper: PasswordHelper) -> None:
        super().__init__(user_db, password_helper)
        self.reset_password_token_secret = settings.jwt_secret_key
        self.verification_token_secret = settings.jwt_secret_key

    async def create(
        self,
        user_create: UserCreate,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        existing = await self.user_db.get_by_email(user_create.email)
        if existing is not None:
            raise exceptions.UserAlreadyExists()

        session = self.user_db.session

        user_dict = (
            user_create.create_update_dict() if safe else user_create.create_update_dict_superuser()
        )
        org_name = user_dict.pop("org_name")
        password = user_dict.pop("password")
        user_dict["hashed_password"] = self.password_helper.hash(password)
        user_dict["is_verified"] = False

        org = Org(name=org_name)
        session.add(org)
        await session.flush()
        # Every org starts with one default location so the location dimension is
        # optional: receive/consume/adjust fall back to it until the user creates
        # more (see app.services.inventory / app.api.routes.inventory).
        session.add(Location(org_id=org.id, code="MAIN", name="Main", is_default=True))
        user_dict["org_id"] = org.id
        # The org-founding user must be its owner - require_role("owner") gates
        # BOM/inventory/dataset/forecast/BI/item-catalog management throughout
        # the app. Without this, User.role's column default ("member") applies
        # and the founder is locked out of managing their own org.
        user_dict["role"] = "owner"

        created_user = await self.user_db.create(user_dict)
        await self.on_after_register(created_user, request)
        return created_user

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        session = self.user_db.session
        await log_event(
            session,
            action="user.register",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        from app.core.email import send_password_reset_email

        session = self.user_db.session
        await send_password_reset_email(user.email, token)
        await log_event(
            session,
            action="user.password_reset_requested",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()

    async def on_after_reset_password(self, user: User, request: Request | None = None) -> None:
        session = self.user_db.session
        await log_event(
            session,
            action="user.password_reset",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        from app.core.email import send_verification_email

        session = self.user_db.session
        await send_verification_email(user.email, token)
        await log_event(
            session,
            action="user.verification_requested",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()

    async def on_after_verify(self, user: User, request: Request | None = None) -> None:
        session = self.user_db.session
        await log_event(
            session,
            action="user.verified",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()


async def get_user_db(session: AsyncSession = Depends(get_db)) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db, PasswordHelper(_pwd_context))


bearer_transport = BearerTransport(tokenUrl="auth/login")


def _get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=settings.jwt_secret_key, lifetime_seconds=settings.access_token_expire_minutes * 60)


# Only used to satisfy FastAPIUsers' constructor (register/reset/verify
# routers don't need it) - /auth/login stays a custom route (see auth.py)
# because MFA gating and refresh-token issuance need a response shape this
# backend's default login router doesn't produce.
auth_backend = AuthenticationBackend(name="jwt", transport=bearer_transport, get_strategy=_get_jwt_strategy)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_users_manager.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/users.py backend/app/core/config.py backend/tests/test_users_manager.py
git commit -m "feat: add fastapi-users UserManager with org bootstrapping and audit hooks"
```

---

### Task 4: Replace register/login routes, keep refresh/logout/MFA

**Files:**
- Modify: `backend/app/api/routes/auth.py:1-249` (replace `register`/`login`, keep everything from `_issue_tokens` through `mfa_verify`)
- Test: `backend/tests/test_auth.py` (existing suite - must pass unchanged)

**Interfaces:**
- Consumes: `fastapi_users`, `UserCreate`, `UserRead`, `get_user_manager`, `UserManager` from `app/core/users.py` (Task 3).
- Produces: `POST /auth/register` (fastapi-users-backed, still 201/409/422 on success/duplicate/weak-password), `POST /auth/login` (custom, unchanged response shape).

- [ ] **Step 1: Replace the top of `auth.py` through the `login` function**

Edit `backend/app/api/routes/auth.py`. Replace lines 1-249 (from the top imports through the end of the `login` function) with:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users import exceptions
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, CurrentUser
from app.core.config import get_settings
from app.core.time import ensure_utc
from app.core.security import (
    create_access_token,
    decrypt_secret,
    encrypt_secret,
    generate_refresh_token,
    generate_totp_secret,
    hash_refresh_token,
    refresh_token_expiry,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)
from app.core.users import UserCreate, UserManager, UserRead, get_user_manager
from app.db.session import get_db
from app.middleware.audit import log_event
from app.middleware.rate_limit import rate_limit
from app.models.user import RefreshToken, User

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

_register_rate_limit = rate_limit("register", settings.rate_limit_register_per_hour, 3600)
_login_rate_limit = rate_limit("login", settings.rate_limit_login_per_5min, 300)
_refresh_rate_limit = rate_limit("refresh", settings.rate_limit_refresh_per_5min, 300)
_mfa_verify_rate_limit = rate_limit("mfa_verify", settings.rate_limit_mfa_verify_per_5min, 300)


class RegisterResponse(UserRead):
    # org_id/user_id kept alongside the standard UserRead fields: several
    # other test modules' _register_and_login() helpers read reg["org_id"]/
    # reg["user_id"] straight off the register response to seed fixtures via
    # direct DB inserts. Dropping them (UserRead alone) is a breaking change
    # to that contract, not just to test_auth.py - see Step 2 below.
    org_id: uuid.UUID
    user_id: uuid.UUID


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_register_rate_limit)],
)
async def register(
    user_create: UserCreate,
    request: Request,
    user_manager: UserManager = Depends(get_user_manager),
):
    # Not fastapi_users.get_register_router(): its built-in UserAlreadyExists
    # handling hardcodes 409->400, which would break the API's existing
    # contract (and test_register_duplicate_email_rejected). Same UserManager
    # .create() the built-in router calls, just with our own status mapping.
    try:
        created_user = await user_manager.create(user_create, safe=True, request=request)
    except exceptions.UserAlreadyExists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    return RegisterResponse(
        id=created_user.id,
        email=created_user.email,
        is_active=created_user.is_active,
        is_superuser=created_user.is_superuser,
        is_verified=created_user.is_verified,
        org_id=created_user.org_id,
        user_id=created_user.id,
    )


class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    mfa_required: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str


class MfaEnrollRequest(BaseModel):
    # Required to (re-)start enrollment on an account that already has MFA enabled,
    # so a stolen/short-lived access token alone can't be used to swap out the secret.
    password: str | None = None


class MfaEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class MfaVerifyRequest(BaseModel):
    code: str


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _is_locked(user: User) -> bool:
    if user.locked_until is None:
        return False
    return ensure_utc(user.locked_until) > datetime.now(timezone.utc)


def _register_failed_attempt(user: User) -> None:
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= settings.max_failed_login_attempts:
        user.locked_until = datetime.now(timezone.utc) + timedelta(
            minutes=settings.account_lockout_minutes
        )
        user.failed_login_attempts = 0


def _reset_failed_attempts(user: User) -> None:
    user.failed_login_attempts = 0
    user.locked_until = None


async def _issue_tokens(db: AsyncSession, user: User) -> TokenResponse:
    access_token = create_access_token(user_id=str(user.id), org_id=str(user.org_id))
    raw_refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            org_id=user.org_id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=refresh_token_expiry(),
        )
    )
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(_login_rate_limit)])
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_manager: UserManager = Depends(get_user_manager),
):
    try:
        user = await user_manager.get_by_email(body.email)
    except exceptions.UserNotExists:
        # Always pay the argon2 cost, even with no real hash to check, so this path
        # can't be timed against the "wrong password" path to enumerate emails.
        user_manager.password_helper.hash(body.password)
        await log_event(db, action="user.login_failed", target=body.email, ip_address=_client_ip(request))
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if _is_locked(user):
        await log_event(
            db,
            action="user.login_locked_out",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts, try again later")

    verified, updated_hash = user_manager.password_helper.verify_and_update(body.password, user.hashed_password)
    if updated_hash is not None:
        user.hashed_password = updated_hash

    if not verified:
        _register_failed_attempt(user)
        await log_event(
            db,
            action="user.login_failed",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if not user.is_active:
        await log_event(
            db,
            action="user.login_disabled",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    if user.mfa_enabled:
        if not body.totp_code:
            await db.commit()  # nothing changed, but keep session hygiene consistent
            return TokenResponse(access_token="", refresh_token="", mfa_required=True)
        if not verify_totp(decrypt_secret(user.mfa_secret or ""), body.totp_code):
            _register_failed_attempt(user)
            await log_event(
                db,
                action="user.login_mfa_failed",
                org_id=user.org_id,
                actor_user_id=user.id,
                ip_address=_client_ip(request),
            )
            await db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA code")

    _reset_failed_attempts(user)
    tokens = await _issue_tokens(db, user)
    await log_event(
        db,
        action="user.login",
        org_id=user.org_id,
        actor_user_id=user.id,
        ip_address=_client_ip(request),
    )
    await db.commit()
    return tokens
```

**Note:** the login route goes through `user_manager.get_by_email` + `password_helper.verify_and_update` explicitly (not fastapi-users' built-in login/`authenticate()`), preserving the exact original check-ordering: lockout is checked *before* password verification, matching `test_account_lockout_after_repeated_failures`.

Leave everything from `@router.post("/mfa/enroll", ...)` (originally starting at line 252) through the end of the file (`logout`) **unchanged** - do not touch `mfa_enroll`, `mfa_verify`, `refresh`, or `logout`.

- [ ] **Step 2: Confirm the register response shape is preserved**

`RegisterResponse` above deliberately keeps `org_id`/`user_id` alongside `UserRead`'s standard fields, because more than `frontend/src/pages/Register.tsx` and `backend/tests/test_auth.py` depend on the old `{org_id, user_id}` shape - a full-suite run surfaced 10 other test files with their own local `_register_and_login()` helpers that read `reg["org_id"]`/`reg["user_id"]` off the register response to seed fixtures via direct DB inserts (`test_ai_insights.py`, `test_automl_retrain.py`, `test_dataset_overwrite.py`, `test_demand_forecasting.py`, `test_downtime_tracking.py`, `test_inventory_analytics.py`, `test_morning_brief.py`, `test_rca.py`, `test_scenario.py`, `test_shift_forecast.py`, `test_telemetry.py`). Run (Grep tool): search `reg\[.org_id.\]|reg\[.user_id.\]` across `backend/tests/` to confirm the full set before moving on - this is why Step 3 below runs the full suite, not just `test_auth.py`, before committing.

- [ ] **Step 3: Run the full auth test suite**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: all tests PASS, including `test_register_rejects_weak_password` (still 422 - the password policy validator lives on `UserCreate` in `app/core/users.py`, which pydantic validates before any manager code runs) and `test_account_lockout_after_repeated_failures` (429 on the 6th, correct-password attempt).

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: all tests pass (nothing outside auth touches these routes).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/auth.py
git commit -m "feat: replace register/login with fastapi-users-backed implementation"
```

---

### Task 5: Email interface + SES sender

**Files:**
- Create: `backend/app/core/email.py`
- Test: `backend/tests/test_email.py`

**Interfaces:**
- Produces: `async def send_email(to: str, subject: str, body: str) -> None`, `async def send_password_reset_email(to: str, token: str) -> None`, `async def send_verification_email(to: str, token: str) -> None`. Task 3's `UserManager` hooks already call the latter two.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_email.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_send_email_dev_env_logs_only(caplog):
    from app.core.email import send_email

    with caplog.at_level("INFO"):
        await send_email("someone@example.com", "Subject", "Body text")

    assert "someone@example.com" in caplog.text
    assert "Subject" in caplog.text


async def test_send_password_reset_email_builds_link_with_token():
    from app.core.email import send_password_reset_email

    with patch("app.core.email.send_email") as mock_send:
        await send_password_reset_email("someone@example.com", "the-token-123")

    mock_send.assert_awaited_once()
    to, subject, body = mock_send.await_args.args
    assert to == "someone@example.com"
    assert "the-token-123" in body


async def test_ses_sender_calls_boto3_send_email():
    from app.core.email import _send_via_ses

    with patch("app.core.email.boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client

        await _send_via_ses("someone@example.com", "Subject", "Body text")

    mock_client_factory.assert_called_once_with("ses", region_name="us-east-1")
    mock_client.send_email.assert_called_once()
    call_kwargs = mock_client.send_email.call_args.kwargs
    assert call_kwargs["Destination"]["ToAddresses"] == ["someone@example.com"]
    assert call_kwargs["Message"]["Subject"]["Data"] == "Subject"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_email.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.email'`

- [ ] **Step 3: Implement `app/core/email.py`**

```python
from __future__ import annotations

import logging
from functools import partial

import boto3
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def _send_via_ses(to: str, subject: str, body: str) -> None:
    def _call() -> None:
        client = boto3.client("ses", region_name=settings.aws_region)
        client.send_email(
            Source=settings.ses_from_email,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )

    await run_in_threadpool(_call)


async def send_email(to: str, subject: str, body: str) -> None:
    """Provider-agnostic email interface. AWS deployments route through SES
    (see _send_via_ses, authenticated via the ECS task's IAM role - no static
    SMTP credentials to manage) once a sending identity is configured. Gated
    on ses_from_email being set, not on settings.env == "production" -
    infra/terraform/aws/ecs.tf always sets ENV=production regardless of
    whether ses_from_email was ever configured, so that would send-or-crash
    on every deployment instead of degrading to logging. Local dev and tests
    (ses_from_email unset by default) never need real AWS credentials."""
    if settings.ses_from_email:
        await _send_via_ses(to, subject, body)
        return
    logger.info("send_email (no SES sender configured, not actually sent): to=%s subject=%s\n%s", to, subject, body)


async def send_password_reset_email(to: str, token: str) -> None:
    link = f"{settings.frontend_base_url}/reset-password?token={token}"
    await send_email(
        to,
        "Reset your Evowerk password",
        f"Click the link below to reset your password. This link expires shortly.\n\n{link}",
    )


async def send_verification_email(to: str, token: str) -> None:
    link = f"{settings.frontend_base_url}/verify-email?token={token}"
    await send_email(
        to,
        "Verify your Evowerk email",
        f"Click the link below to verify your email address.\n\n{link}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_email.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/email.py backend/tests/test_email.py
git commit -m "feat: add provider-agnostic email interface with SES backend"
```

---

### Task 6: Wire password reset + email verification routers

**Files:**
- Modify: `backend/app/api/routes/auth.py` (add two `router.include_router(...)` calls)
- Test: `backend/tests/test_password_reset.py`

**Interfaces:**
- Consumes: `fastapi_users`, `UserRead` from `app/core/users.py`; `send_password_reset_email`/`send_verification_email` (already wired via `UserManager` hooks in Task 3 - this task only mounts the routes).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_password_reset.py`:

```python
from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def test_forgot_password_always_returns_202_even_for_unknown_email(client):
    # fastapi-users' forgot-password endpoint deliberately returns 202 regardless
    # of whether the email exists, so a caller can't enumerate registered emails.
    res = await client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert res.status_code == 202


async def test_forgot_password_then_reset_changes_password(client):
    await client.post(
        "/auth/register", json={"org_name": "Acme", "email": "reset@example.com", "password": PASSWORD}
    )

    with patch("app.core.email.send_email") as mock_send:
        res = await client.post("/auth/forgot-password", json={"email": "reset@example.com"})
    assert res.status_code == 202
    mock_send.assert_awaited_once()
    reset_link_body = mock_send.await_args.args[2]
    token = reset_link_body.split("token=")[1].split()[0].strip()

    new_password = "a-brand-new-password-1"  # pragma: allowlist secret
    reset_res = await client.post(
        "/auth/reset-password", json={"token": token, "password": new_password}
    )
    assert reset_res.status_code == 200

    old_login = await client.post("/auth/login", json={"email": "reset@example.com", "password": PASSWORD})
    assert old_login.status_code == 401

    new_login = await client.post(
        "/auth/login", json={"email": "reset@example.com", "password": new_password}
    )
    assert new_login.status_code == 200


async def test_request_verify_then_verify_marks_user_verified(client):
    await client.post(
        "/auth/register", json={"org_name": "Acme", "email": "verify@example.com", "password": PASSWORD}
    )

    with patch("app.core.email.send_email") as mock_send:
        res = await client.post("/auth/request-verify-token", json={"email": "verify@example.com"})
    assert res.status_code == 202
    verify_link_body = mock_send.await_args.args[2]
    token = verify_link_body.split("token=")[1].split()[0].strip()

    verify_res = await client.post("/auth/verify", json={"token": token})
    assert verify_res.status_code == 200
    assert verify_res.json()["is_verified"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_password_reset.py -v`
Expected: FAIL with 404s (`/auth/forgot-password` etc. not mounted yet)

- [ ] **Step 3: Mount the routers**

Edit `backend/app/api/routes/auth.py`. Task 4's `register` route is a hand-written function (not a mounted `fastapi_users` router - see Task 4's final implementation), so `fastapi_users` isn't imported yet in this file. Add it to the existing `from app.core.users import UserCreate, UserManager, UserRead, get_user_manager` line, then add these two router mounts right after the `register` route function (before the `class LoginRequest` block):

```python
from app.core.users import UserCreate, UserManager, UserRead, fastapi_users, get_user_manager
```

```python
router.include_router(fastapi_users.get_reset_password_router())
router.include_router(fastapi_users.get_verify_router(UserRead))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_password_reset.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/auth.py backend/tests/test_password_reset.py
git commit -m "feat: wire password reset and email verification routes"
```

---

### Task 7: Google + Microsoft OAuth login

**Files:**
- Create: `backend/app/api/routes/oauth.py`
- Modify: `backend/app/main.py:9-46` (import + `app.include_router(oauth.router)`)
- Test: `backend/tests/test_oauth.py`

**Interfaces:**
- Consumes: `UserManager`, `get_user_manager` (Task 3); `_issue_tokens`, `TokenResponse` (Task 4, `auth.py`).
- Produces: `GET /auth/google/authorize`, `GET /auth/google/callback`, `GET /auth/microsoft/authorize`, `GET /auth/microsoft/callback`.

**No self-service OAuth signup.** This app's access model is admin-provisioned:
a company's admin creates accounts for their employees (via `/register` or a
future invite flow) - it is not a public app anyone can sign up for. So
`GET /auth/{provider}/callback` must reject outright when no account already
exists for the authenticated email, *before* ever touching `UserManager`'s
user-creation path (`User.org_id` is NOT NULL and only `UserManager.create()`
- which requires an `org_name` the OAuth flow never collects - creates an
`Org`; letting `oauth_callback()`'s new-user branch run would either crash on
that NOT NULL constraint or, if "fixed" by inventing a placeholder org, let a
stranger who merely owns a matching Google/Microsoft account mint themselves
a new org, which is exactly what this access model forbids). Google/Microsoft
login is a convenience for people who already have a provisioned account,
never a second signup path.

**Before writing code:** `BaseUserManager.oauth_callback()` is the lower-level method `fastapi_users.get_oauth_router()` calls internally, and it accepts `associate_by_email`/`is_verified_by_default` as call-time arguments - that's what makes a per-request verified-email check possible (the auto-generated router bakes these in as fixed config instead). Confirm the exact signature against the installed package before writing the callback body:

Run: `python -c "import inspect, fastapi_users.manager; print(inspect.signature(fastapi_users.manager.BaseUserManager.oauth_callback))"`

Use whatever signature that prints (parameter names/order may differ slightly by version) to adjust the call in Step 3 below if needed.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_oauth.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_google_authorize_redirects_to_google(client):
    with patch("app.api.routes.oauth.google_client") as mock_client:
        mock_client.get_authorization_url = AsyncMock(return_value="https://accounts.google.com/o/oauth2/auth?...")
        res = await client.get("/auth/google/authorize", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert "accounts.google.com" in res.headers["location"]


async def test_google_callback_logs_in_existing_account_when_email_verified(client):
    # No self-service OAuth signup - this account must already exist
    # (provisioned via /register, standing in here for "an admin created it").
    await client.post(
        "/auth/register",
        json={"org_name": "Acme", "email": "provisioned@example.com", "password": "correct-horse-battery-staple1"},  # pragma: allowlist secret
    )

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-1", "provisioned@example.com"))

        res = await client.get("/auth/google/callback?code=fake-code&state=fake-state", follow_redirects=False)

    assert res.status_code in (302, 307)
    location = res.headers["location"]
    assert "access_token=" in location
    assert "refresh_token=" in location


async def test_google_callback_rejects_unknown_email(client):
    from fastapi import status

    # No account was ever registered for this email - OAuth must not create
    # one, even though the provider itself vouches the caller owns it.
    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-3", "stranger@example.com"))

        res = await client.get("/auth/google/callback?code=fake-code&state=fake-state", follow_redirects=False)

    assert res.status_code == status.HTTP_403_FORBIDDEN


async def test_google_callback_rejects_auto_link_when_email_not_verified(client):
    from fastapi import status

    await client.post(
        "/auth/register",
        json={"org_name": "Acme", "email": "existing@example.com", "password": "correct-horse-battery-staple1"},  # pragma: allowlist secret
    )

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=False)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-2", "existing@example.com"))

        res = await client.get("/auth/google/callback?code=fake-code&state=fake-state", follow_redirects=False)

    # Must NOT silently log in as the existing password account. Because
    # `email` is a unique column, "don't auto-link" can't mean "create a
    # second User row with the same email" either - it must mean "reject
    # the OAuth login" (no tokens issued) rather than trusting an
    # unverified provider email that happens to match an existing account.
    assert res.status_code == status.HTTP_400_BAD_REQUEST

    # The existing account itself is untouched - its real password still works.
    login = await client.post(
        "/auth/login", json={"email": "existing@example.com", "password": "correct-horse-battery-staple1"}  # pragma: allowlist secret
    )
    assert login.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_oauth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.oauth'`

- [ ] **Step 3: Implement `app/api/routes/oauth.py`**

```python
from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi_users import exceptions
from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.clients.microsoft import MicrosoftGraphOAuth2

from app.api.routes.auth import TokenResponse, _issue_tokens  # noqa: F401 (TokenResponse kept for symmetry/typing)
from app.core.config import get_settings
from app.core.users import UserManager, get_user_manager
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/auth", tags=["oauth"])
settings = get_settings()

google_client = GoogleOAuth2(settings.google_oauth_client_id, settings.google_oauth_client_secret)
microsoft_client = MicrosoftGraphOAuth2(settings.microsoft_oauth_client_id, settings.microsoft_oauth_client_secret)


async def _google_email_verified(access_token: str) -> bool:
    """Google's standard OIDC userinfo endpoint reports email_verified - this
    is the actual per-token verification signal the auto-link decision relies
    on (httpx-oauth's GoogleOAuth2.get_id_email() drops this field, so it's
    fetched separately here)."""
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return bool(resp.json().get("email_verified", False))


async def _microsoft_email_verified(access_token: str) -> bool:
    """Microsoft Graph's /me endpoint has no per-request email_verified claim
    the way Google's userinfo endpoint does. Treating Microsoft OAuth logins
    as provider-verified by default (both work/school tenant provisioning and
    MSA signup already verify the email at account-creation time) - flagged
    here as a real asymmetry with the Google check above, not an oversight.
    Revisit if Microsoft's identity platform exposes a stronger signal."""
    return True


def _redirect_with_tokens(tokens: TokenResponse) -> RedirectResponse:
    query = urlencode({"access_token": tokens.access_token, "refresh_token": tokens.refresh_token})
    return RedirectResponse(url=f"{settings.frontend_base_url}/oauth/callback#{query}")


async def _handle_callback(
    *,
    oauth_name: str,
    code: str,
    client: GoogleOAuth2 | MicrosoftGraphOAuth2,
    email_verified_check,
    request: Request,
    db: AsyncSession,
    user_manager: UserManager,
) -> RedirectResponse:
    redirect_uri = str(request.url_for(f"{oauth_name}_callback"))
    token_data = await client.get_access_token(code, redirect_uri)
    access_token = token_data["access_token"]
    account_id, account_email = await client.get_id_email(access_token)

    # No self-service OAuth signup: reject before ever reaching
    # UserManager's user-creation path (see the note at the top of this
    # task). A stranger who merely controls a matching Google/Microsoft
    # account must not be able to mint themselves an account/org here.
    try:
        await user_manager.get_by_email(account_email)
    except exceptions.UserNotExists:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "No account found for this email. Contact your organization administrator.",
        ) from None

    verified = await email_verified_check(access_token)

    try:
        user = await user_manager.oauth_callback(
            oauth_name=oauth_name,
            access_token=access_token,
            account_id=account_id,
            account_email=account_email,
            expires_at=token_data.get("expires_in"),
            refresh_token=token_data.get("refresh_token"),
            request=request,
            associate_by_email=verified,
            is_verified_by_default=verified,
        )
    except exceptions.UserAlreadyExists:
        # associate_by_email=False (unverified email) and this email already
        # has an account - narrow catch so a genuine failure inside
        # oauth_callback() (DB error, etc.) surfaces as a 500 instead of
        # being silently downgraded to a misleading 400.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not sign in with this account") from None

    tokens = await _issue_tokens(db, user)
    await db.commit()
    return _redirect_with_tokens(tokens)


@router.get("/google/authorize")
async def google_authorize(request: Request) -> RedirectResponse:
    redirect_uri = str(request.url_for("google_callback"))
    auth_url = await google_client.get_authorization_url(redirect_uri, scope=["openid", "email", "profile"])
    return RedirectResponse(url=auth_url)


@router.get("/google/callback", name="google_callback")
async def google_callback(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
    user_manager: UserManager = Depends(get_user_manager),
) -> RedirectResponse:
    return await _handle_callback(
        oauth_name="google",
        code=code,
        client=google_client,
        email_verified_check=_google_email_verified,
        request=request,
        db=db,
        user_manager=user_manager,
    )


@router.get("/microsoft/authorize")
async def microsoft_authorize(request: Request) -> RedirectResponse:
    redirect_uri = str(request.url_for("microsoft_callback"))
    auth_url = await microsoft_client.get_authorization_url(redirect_uri, scope=["openid", "email", "profile"])
    return RedirectResponse(url=auth_url)


@router.get("/microsoft/callback", name="microsoft_callback")
async def microsoft_callback(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
    user_manager: UserManager = Depends(get_user_manager),
) -> RedirectResponse:
    return await _handle_callback(
        oauth_name="microsoft",
        code=code,
        client=microsoft_client,
        email_verified_check=_microsoft_email_verified,
        request=request,
        db=db,
        user_manager=user_manager,
    )
```

Only mount this router when at least one provider is configured (avoids crashing local dev with empty client id/secret strings) - see Step 4.

- [ ] **Step 4: Mount the router in `main.py`**

Edit `backend/app/main.py`. Add `oauth` to the `from app.api.routes import (...)` block (alphabetically, after `orgs`), and after the existing `app.include_router(auth.router)` line, add:

```python
if settings.google_oauth_client_id or settings.microsoft_oauth_client_id:
    app.include_router(oauth.router)
```

- [ ] **Step 5: Make sure the OAuth router is actually mounted for tests**

The conditional mount added in Step 4 only registers `oauth.router` when a client ID is configured. `conftest.py` imports `app.main` once per test process, so the env var must be set before that first import happens - add these two lines to `backend/tests/conftest.py`, right next to the existing `os.environ.setdefault("JWT_SECRET_KEY", ...)` line (before the `from app.main import app` line below it):

```python
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")  # pragma: allowlist secret
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest tests/test_oauth.py -v`
Expected: PASS

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routes/oauth.py backend/app/main.py backend/tests/test_oauth.py backend/tests/conftest.py
git commit -m "feat: add Google and Microsoft OAuth login with verified-email gating"
```

---

### Task 8: Terraform - SES resources + IAM + task env

**Files:**
- Create: `infra/terraform/aws/ses.tf`
- Modify: `infra/terraform/aws/iam.tf`, `infra/terraform/aws/ecs.tf`, `infra/terraform/aws/variables.tf`, `infra/terraform/aws/README.md`

**Interfaces:**
- Produces: `aws_ses_domain_identity.main`, `ses_from_email`/`frontend_base_url`/OAuth client id/secret env vars on the backend ECS task.

- [ ] **Step 1: Add variables**

Edit `infra/terraform/aws/variables.tf`, add after the existing `route53_zone_id` block:

```hcl
variable "ses_from_email" {
  description = "Sender address for password-reset/verification emails (e.g. no-reply@mrp.customer.com) - must be on a domain you can add DNS records for, since SES requires domain verification."
  type        = string
  default     = ""
}

variable "google_oauth_client_id" {
  description = "Google OAuth 2.0 client ID (leave empty to disable Google login)"
  type        = string
  default     = ""
}

variable "google_oauth_client_secret" {
  description = "Google OAuth 2.0 client secret"
  type        = string
  default     = ""
  sensitive   = true
}

variable "microsoft_oauth_client_id" {
  description = "Microsoft OAuth 2.0 (Azure AD app registration) client ID (leave empty to disable Microsoft login)"
  type        = string
  default     = ""
}

variable "microsoft_oauth_client_secret" {
  description = "Microsoft OAuth 2.0 client secret"
  type        = string
  default     = ""
  sensitive   = true
}
```

- [ ] **Step 2: Create the SES domain identity**

Create `infra/terraform/aws/ses.tf`:

```hcl
# Only created when ses_from_email is set - customers who skip email flows
# entirely (password reset/verification stay log-only, see
# backend/app/core/email.py) don't need this.
resource "aws_ses_domain_identity" "main" {
  count  = var.ses_from_email != "" ? 1 : 0
  domain = split("@", var.ses_from_email)[1]
}

output "ses_domain_verification_record" {
  description = "TXT record to add at your DNS provider to verify the SES sending domain (skip if ses_from_email is unset)"
  value       = var.ses_from_email != "" ? aws_ses_domain_identity.main[0].verification_token : null
}
```

- [ ] **Step 3: Grant the backend task role SES send permission**

Edit `infra/terraform/aws/iam.tf`. After the existing `resource "aws_iam_role" "ecs_task"` block, add:

```hcl
data "aws_iam_policy_document" "ses_send" {
  count = var.ses_from_email != "" ? 1 : 0
  statement {
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ecs_task_ses" {
  count  = var.ses_from_email != "" ? 1 : 0
  name   = "${local.name_prefix}-ses-send"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ses_send[0].json
}
```

- [ ] **Step 4: Pass the new settings to the backend task**

Edit `infra/terraform/aws/ecs.tf`. In `aws_ecs_task_definition.backend`'s `environment` list, add:

```hcl
        { name = "SES_FROM_EMAIL", value = var.ses_from_email },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "FRONTEND_BASE_URL", value = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.main.dns_name}" },
        { name = "GOOGLE_OAUTH_CLIENT_ID", value = var.google_oauth_client_id },
        { name = "MICROSOFT_OAUTH_CLIENT_ID", value = var.microsoft_oauth_client_id },
```

And add the two OAuth secrets to the `secrets` block (only referencing them when set would require a conditional secret resource; simplest is to always create the Secrets Manager entries, matching the existing `jwt_secret`/`mfa_key` pattern, even if empty):

```hcl
        { name = "GOOGLE_OAUTH_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.google_oauth_secret.arn },
        { name = "MICROSOFT_OAUTH_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.microsoft_oauth_secret.arn },
```

Then add the two backing secrets to `infra/terraform/aws/secrets.tf`, following the existing pattern exactly:

```hcl
resource "aws_secretsmanager_secret" "google_oauth_secret" {
  name                    = "${local.name_prefix}/google-oauth-client-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "google_oauth_secret" {
  secret_id     = aws_secretsmanager_secret.google_oauth_secret.id
  secret_string = var.google_oauth_client_secret
}

resource "aws_secretsmanager_secret" "microsoft_oauth_secret" {
  name                    = "${local.name_prefix}/microsoft-oauth-client-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "microsoft_oauth_secret" {
  secret_id     = aws_secretsmanager_secret.microsoft_oauth_secret.id
  secret_string = var.microsoft_oauth_client_secret
}
```

Also add these two new secret ARNs to the existing `data "aws_iam_policy_document" "secrets_read"` block's `resources` list in `iam.tf` (alongside `jwt_secret`, `mfa_key`, `database_url`), so the execution role can actually read them:

```hcl
      aws_secretsmanager_secret.google_oauth_secret.arn,
      aws_secretsmanager_secret.microsoft_oauth_secret.arn,
```

- [ ] **Step 5: Validate the Terraform**

Run: `cd infra/terraform/aws && terraform validate`
Expected: `Success! The configuration is valid.`

- [ ] **Step 6: Document the SES verification step**

Edit `infra/terraform/aws/README.md`. Add a new section after "## DNS + TLS":

```markdown
## Email (password reset / verification)

Leave `ses_from_email` empty to skip - password-reset and verification links
are logged instead of emailed (see `backend/app/core/email.py`), fine for a
test deploy. To send real email:

1. Set `ses_from_email` in `terraform.tfvars` (e.g. `no-reply@mrp.customer.com`).
2. After `terraform apply`, read the `ses_domain_verification_record` output
   and add it as a TXT record (`_amazonses.<domain>`) at your DNS provider.
3. Wait for AWS to detect verification (Console: SES > Verified identities).
4. SES accounts start in sandbox mode (can only send to verified addresses) -
   request production access in the SES console before relying on this for
   real users.
```

- [ ] **Step 7: Commit**

```bash
git add infra/terraform/aws/
git commit -m "feat: add SES, OAuth secrets, and related env vars to AWS Terraform module"
```

---

### Task 9: Frontend - forgot password / reset password

**Files:**
- Create: `frontend/src/pages/ForgotPassword.tsx`, `frontend/src/pages/ResetPassword.tsx`
- Modify: `frontend/src/pages/Login.tsx:52-64` (footer), `frontend/src/App.tsx:1,60-61` (routes)
- Test: `frontend/src/pages/__tests__/ForgotPassword.test.tsx`, `frontend/src/pages/__tests__/ResetPassword.test.tsx`

**Interfaces:**
- Consumes: `AuthLayout`, `Field`, `Input`, `Button`, `Alert`, `errorMessage` (existing, same imports as `Login.tsx`/`Register.tsx`).

- [ ] **Step 1: Check for an existing frontend test for a similar page**

Run (Grep tool): search for `describe` in `frontend/src/pages/__tests__/` or any `*.test.tsx` under `frontend/src/pages` to confirm the test setup pattern (React Testing Library + Vitest) before writing new tests. If no `__tests__` directory exists yet under `pages/`, check `frontend/src/**/__tests__/*.test.tsx` anywhere in the repo for the established pattern (render helper, mocked `fetch`, etc.) and follow it exactly instead of inventing a new one.

- [ ] **Step 2: Write `ForgotPassword.tsx`**

Create `frontend/src/pages/ForgotPassword.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await fetch("/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      // Always show the same confirmation regardless of outcome - the
      // endpoint itself never reveals whether the email is registered
      // (see backend/tests/test_password_reset.py), so the UI shouldn't either.
      setSubmitted(true);
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <AuthLayout title="Check your email" subtitle="If an account exists for that address, a reset link is on its way.">
        <Link to="/login" className="text-sm font-medium text-primary hover:underline">
          Back to log in
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Reset your password"
      subtitle="Enter your email and we'll send you a reset link"
      footer={
        <Link to="/login" className="font-medium text-primary hover:underline">
          Back to log in
        </Link>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <Field label="Email" htmlFor="email">
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </Field>
        <Button type="submit" className="w-full" loading={submitting}>
          Send reset link
        </Button>
      </form>
    </AuthLayout>
  );
}
```

- [ ] **Step 3: Write `ResetPassword.tsx`**

Create `frontend/src/pages/ResetPassword.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { errorMessage } from "@/lib/utils";

export function ResetPassword() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(errorMessage(body, "Could not reset password. The link may have expired."));
        return;
      }
      navigate("/login");
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <AuthLayout title="Invalid link" subtitle="This password reset link is missing its token.">
        <Link to="/forgot-password" className="text-sm font-medium text-primary hover:underline">
          Request a new link
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Set a new password" subtitle="Choose a new password for your account">
      <form onSubmit={onSubmit} className="space-y-4">
        <Field label="New password" htmlFor="password" hint="At least 12 characters.">
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={12}
          />
        </Field>
        {error && <Alert variant="destructive">{error}</Alert>}
        <Button type="submit" className="w-full" loading={submitting}>
          Reset password
        </Button>
      </form>
    </AuthLayout>
  );
}
```

- [ ] **Step 4: Add the routes**

Edit `frontend/src/App.tsx`. Add imports after the `Register` import:

```tsx
import { ForgotPassword } from "./pages/ForgotPassword";
import { ResetPassword } from "./pages/ResetPassword";
```

Add routes after the existing `<Route path="/register" element={<Register />} />` line:

```tsx
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
```

- [ ] **Step 5: Add the "Forgot password?" link to Login.tsx**

Edit `frontend/src/pages/Login.tsx`. In the `<Field label="Password" ...>` block, add a link above the `<Input>` (mirroring the label row):

```tsx
        <Field
          label={
            <div className="flex items-center justify-between">
              <span>Password</span>
              <Link to="/forgot-password" className="text-xs font-medium text-primary hover:underline">
                Forgot password?
              </Link>
            </div>
          }
          htmlFor="password"
        >
```

This requires `Field`'s `label` prop to accept a `ReactNode`, not just `string`. Update `frontend/src/components/Field.tsx`'s prop type from `label: string;` to `label: ReactNode;`.

- [ ] **Step 6: Write component tests**

Follow whatever pattern Step 1 found. If none exists anywhere in the repo, create `frontend/src/pages/__tests__/ForgotPassword.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ForgotPassword } from "../ForgotPassword";

describe("ForgotPassword", () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true });
  });

  it("shows a confirmation message after submitting", async () => {
    render(
      <MemoryRouter>
        <ForgotPassword />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));

    await waitFor(() => expect(screen.getByText(/check your email/i)).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith(
      "/auth/forgot-password",
      expect.objectContaining({ method: "POST" })
    );
  });
});
```

(If `@testing-library/react` isn't already a dev dependency, check `frontend/package.json` first - if this project doesn't have component-level tests set up at all yet, skip this step and note it in the task's final commit message instead of adding a new testing framework as a side effect of this feature.)

- [ ] **Step 7: Manual verification in the browser**

Start the dev server (`preview_start` with the frontend's launch config), navigate to `/login`, click "Forgot password?", confirm it lands on `/forgot-password`, submit an email, confirm the "Check your email" state renders. Then navigate directly to `/reset-password?token=test` and confirm the form renders (a real reset requires a real token from a real email/log line).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/ForgotPassword.tsx frontend/src/pages/ResetPassword.tsx frontend/src/pages/Login.tsx frontend/src/components/Field.tsx frontend/src/App.tsx frontend/src/pages/__tests__
git commit -m "feat: add forgot password / reset password pages"
```

---

### Task 10: Frontend - email verification prompt

**Files:**
- Create: `frontend/src/pages/VerifyEmail.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: same shared components as Task 9.

- [ ] **Step 1: Write `VerifyEmail.tsx`**

Create `frontend/src/pages/VerifyEmail.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { AuthLayout } from "@/components/AuthLayout";
import { Alert } from "@/components/ui/alert";

export function VerifyEmail() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [status, setStatus] = useState<"pending" | "ok" | "error">("pending");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      return;
    }
    fetch("/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    })
      .then((res) => setStatus(res.ok ? "ok" : "error"))
      .catch(() => setStatus("error"));
  }, [token]);

  return (
    <AuthLayout title="Email verification">
      {status === "pending" && <p className="text-center text-sm text-muted-foreground">Verifying…</p>}
      {status === "ok" && (
        <div className="space-y-4">
          <Alert variant="success">Your email is verified.</Alert>
          <Link to="/login" className="block text-center text-sm font-medium text-primary hover:underline">
            Continue to log in
          </Link>
        </div>
      )}
      {status === "error" && <Alert variant="destructive">This verification link is invalid or expired.</Alert>}
    </AuthLayout>
  );
}
```

- [ ] **Step 2: Add the route**

Edit `frontend/src/App.tsx`. Add import after `ResetPassword`:

```tsx
import { VerifyEmail } from "./pages/VerifyEmail";
```

Add route after `/reset-password`:

```tsx
      <Route path="/verify-email" element={<VerifyEmail />} />
```

- [ ] **Step 3: Manual verification**

Navigate to `/verify-email?token=bad-token` in the browser, confirm the "invalid or expired" error state renders (a real success state requires a real token from a real registration's log line, per Task 5's dev-mode logging).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/VerifyEmail.tsx frontend/src/App.tsx
git commit -m "feat: add email verification page"
```

---

### Task 11: Frontend - OAuth buttons + callback page

**Files:**
- Create: `frontend/src/pages/OAuthCallback.tsx`
- Modify: `frontend/src/pages/Login.tsx`, `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `useAuthStore` (`setTokens`), same shared components.

- [ ] **Step 1: Write `OAuthCallback.tsx`**

Create `frontend/src/pages/OAuthCallback.tsx`:

```tsx
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "../store/authStore";
import { AuthLayout } from "@/components/AuthLayout";

export function OAuthCallback() {
  const setTokens = useAuthStore((s) => s.setTokens);
  const navigate = useNavigate();

  useEffect(() => {
    // Tokens arrive in the URL fragment (not the query string) so they never
    // hit server access logs or a Referer header - see
    // backend/app/api/routes/oauth.py's _redirect_with_tokens.
    const params = new URLSearchParams(window.location.hash.slice(1));
    const accessToken = params.get("access_token");
    const refreshToken = params.get("refresh_token");
    if (accessToken && refreshToken) {
      setTokens(accessToken, refreshToken);
      navigate("/", { replace: true });
    } else {
      navigate("/login", { replace: true });
    }
  }, [navigate, setTokens]);

  return (
    <AuthLayout title="Signing you in…">
      <p className="text-center text-sm text-muted-foreground">One moment…</p>
    </AuthLayout>
  );
}
```

- [ ] **Step 2: Add the route**

Edit `frontend/src/App.tsx`. Add import after `VerifyEmail`:

```tsx
import { OAuthCallback } from "./pages/OAuthCallback";
```

Add route after `/verify-email`:

```tsx
      <Route path="/oauth/callback" element={<OAuthCallback />} />
```

- [ ] **Step 3: Add OAuth buttons to Login.tsx**

Edit `frontend/src/pages/Login.tsx`. Add after the closing `</form>` tag, still inside `<AuthLayout>`:

```tsx
        <div className="mt-6 space-y-2">
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t border-border" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-background px-2 text-muted-foreground">Or continue with</span>
            </div>
          </div>
          <a href="/auth/google/authorize" className="block">
            <Button type="button" variant="outline" className="w-full">
              Continue with Google
            </Button>
          </a>
          <a href="/auth/microsoft/authorize" className="block">
            <Button type="button" variant="outline" className="w-full">
              Continue with Microsoft
            </Button>
          </a>
        </div>
```

Check `frontend/src/components/ui/button.tsx` for the exact `variant` prop values it supports (Grep for `variant` in that file) before using `"outline"` - substitute whatever the closest existing secondary-style variant is named if `"outline"` doesn't exist.

- [ ] **Step 4: Manual verification in the browser**

With `GOOGLE_OAUTH_CLIENT_ID`/`MICROSOFT_OAUTH_CLIENT_ID` unset locally, confirm the buttons still render (they'll 404 on click since the backend router isn't mounted without configured credentials - acceptable for local dev, note this in the PR description). Confirm `/oauth/callback` with no hash redirects to `/login`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/OAuthCallback.tsx frontend/src/pages/Login.tsx frontend/src/App.tsx
git commit -m "feat: add Google/Microsoft OAuth buttons and callback handler"
```

---

### Task 12: Full regression pass + README update

**Files:**
- Modify: `README.md:186-192` (Testing section coverage list)

**Interfaces:** none new - this task only verifies and documents.

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: all tests pass, including every test file added in Tasks 2-7.

- [ ] **Step 2: Run backend lint**

Run: `cd backend && ruff check .`
Expected: no errors (fix any import-order/unused-import issues introduced by the `auth.py` rewrite in Task 4, e.g. `hash_password`/`verify_password` if no longer called directly anywhere in `auth.py`).

- [ ] **Step 3: Run frontend build + lint**

Run: `cd frontend && npm run lint && npm run build`
Expected: no errors.

- [ ] **Step 4: Run frontend tests** (if Task 9 added any)

Run: `cd frontend && npm run test`
Expected: all pass.

- [ ] **Step 5: Update README's Testing section**

Edit `README.md` around line 188 ("Coverage: register → login → MFA enroll → ..."). Replace that sentence with:

```markdown
Coverage: register → login → MFA enroll → login-with-MFA → refresh (rotates,
old token revoked) → logout (revokes token), account lockout, password
policy, the MFA-disable-bypass regression, cross-tenant isolation, per-IP
rate limiting, password reset request/confirm, email verification
request/confirm, and Google/Microsoft OAuth login (including the
verified-email auto-link gate).
```

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: update auth testing coverage list for fastapi-users migration"
```

---

## Self-Review

**Spec coverage:**
- Full replacement of register/login → Task 4. ✓
- UserManager with org bootstrap, TOTP gating, lockout, audit logging → Tasks 3, 4. ✓
- Password hash reuse (argon2, no rehash) → Task 3 (`PasswordHelper(_pwd_context)`), verified in Task 3's test. ✓
- `is_superuser`/`is_verified` columns, `oauth_accounts` table, existing users grandfathered `is_verified=true` → Task 2. ✓
- Password reset + email verification via `send_email()` → Tasks 5, 6. ✓
- SES via boto3 + ECS task IAM role (corrected from the spec's original SMTP approach) → Tasks 5, 8. ✓
- No Azure work → not present anywhere in this plan. ✓
- Google + Microsoft OAuth, auto-link only when provider-verified → Task 7. ✓
- Refresh tokens/logout unchanged → Task 4 explicitly preserves `_issue_tokens`/`refresh`/`logout` verbatim. ✓
- Frontend forgot/reset/verify/OAuth pages → Tasks 9, 10, 11. ✓
- Existing `test_auth.py` regression gate → asserted explicitly in Task 4 Step 3, re-run in Tasks 6-7, full suite in Task 12. ✓

**Placeholder scan:** no TBD/TODO; the one genuinely open item (Task 7's oauth_callback signature) is handled with a concrete verification command and an explicit instruction for what to do with its output, not left vague.

**Type consistency:** `send_email(to, subject, body)` used identically in Task 5's implementation and Task 3's `on_after_forgot_password`/`on_after_request_verify` hooks. `TokenResponse`/`_issue_tokens` defined in Task 4's `auth.py`, imported by name in Task 7's `oauth.py`. `UserCreate`/`UserRead`/`fastapi_users`/`get_user_manager`/`UserManager` defined in Task 3, imported identically in Tasks 4, 6, 7.

**Known risk flagged, not hidden:** Task 7's Microsoft email-verification check is a documented best-effort default (`_microsoft_email_verified` always returns `True`), not a verified claim check like Google's - called out in the code comment and this plan's "Before writing code" note, not silently assumed correct.
