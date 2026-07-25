# Staff Portal (Separate Employee Auth) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `User.is_staff`-on-a-customer-account approach with a dedicated `StaffAccount`
credential (employee ID + password), a `POST /staff/auth/login` endpoint, and a standalone `/staff`
portal reached via a "Staff Login" link on the public login page - with zero staff concept left in
the customer-facing app afterward.

**Architecture:** A new `StaffAccount` table with no relation to `Org`/`User` (staff aren't
tenant-scoped). Auth reuses the plain-PyJWT pattern already in `app/core/security.py`
(`create_access_token`/`decode_token`, both generic over a `"type"` field) with a new
`"staff_access"` type instead of building a second fastapi-users identity. Frontend keeps a fully
separate `useStaffAuthStore`/`staffApiFetch` pair so a staff session and a customer session can
never be confused client-side.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, PyJWT, argon2 (via existing `passlib`
`CryptContext`), React, TanStack Query, Zustand, react-router-dom, Tailwind.

## Global Constraints

- This is a not-yet-merged branch (`feature-entitlements`) - edit the existing
  `b2e9f4a71c83_org_features_user_staff.py` migration in place rather than adding a second one.
- No refresh tokens, MFA, or account lockout for staff accounts in this iteration - a short-lived
  access token only (see spec's Non-goals).
- Staff accounts are seeded via a CLI script only - no self-service "add employee" UI/endpoint.
- No employee-ID format validation beyond DB uniqueness.
- Follow existing code conventions exactly: argon2 password hashing via `app.core.security`, plain
  PyJWT (not fastapi-users' `JWTStrategy`) for staff tokens, `sa.Uuid(as_uuid=True)` for UUID
  columns in migrations, `Mapped[...]`/`mapped_column` in models.

---

### Task 1: `StaffAccount` model + migration edit

**Files:**
- Create: `backend/app/models/staff.py`
- Modify: `backend/app/models/user.py` (remove `is_staff` column)
- Modify: `backend/alembic/versions/b2e9f4a71c83_org_features_user_staff.py`
- Modify: `backend/alembic/env.py` (register new model with `Base.metadata`)
- Modify: `backend/tests/conftest.py` (same registration, for the test DB)
- Test: `backend/tests/test_staff_account_model.py`

**Interfaces:**
- Produces: `app.models.staff.StaffAccount` with fields `id: uuid.UUID`, `employee_id: str`,
  `hashed_password: str`, `created_at: datetime`. Later tasks (2-5) import this.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_staff_account_model.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.staff import StaffAccount

pytestmark = pytest.mark.asyncio


async def test_staff_account_round_trips_through_the_db(db_session):
    staff = StaffAccount(employee_id="zeeshan", hashed_password="not-a-real-hash")
    db_session.add(staff)
    await db_session.commit()

    fetched = (
        await db_session.execute(select(StaffAccount).where(StaffAccount.employee_id == "zeeshan"))
    ).scalar_one()
    assert fetched.id is not None
    assert isinstance(fetched.id, uuid.UUID)
    assert fetched.hashed_password == "not-a-real-hash"  # pragma: allowlist secret
    assert fetched.created_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `python -m pytest tests/test_staff_account_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.staff'`

- [ ] **Step 3: Create the model**

```python
# backend/app/models/staff.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StaffAccount(Base):
    """An EvoWerk employee credential - unrelated to Org/User, since staff aren't
    tenant-scoped. See docs/superpowers/specs/2026-07-23-staff-portal-design.md."""

    __tablename__ = "staff_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 4: Register the model so `Base.metadata` picks it up**

In `backend/alembic/env.py`, add `staff` to the `from app.models import (...)` list (alphabetical,
after `simulation`, before `telemetry`):

```python
from app.models import (  # noqa: F401 - registers models with Base.metadata
    alert,
    audit,
    bom,
    dataset,
    forecast,
    inventory,
    inventory_snapshot,
    integration,
    kpi,
    location,
    lot,
    machine,
    maintenance_schedule,
    procurement,
    rca_case,
    realtime,
    routing,
    sensor,
    shift_schedule,
    simulation,
    staff,
    telemetry,
    tenant,
    user,
    work_center,
)
```

In `backend/tests/conftest.py`, add `staff` to its own (separate, wider) `from app.models import
(...)` list the same way, alphabetically after `simulation`, before `telemetry`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_staff_account_model.py -v`
Expected: PASS

- [ ] **Step 6: Remove `User.is_staff` and edit the migration**

In `backend/app/models/user.py`, delete this line entirely:

```python
    is_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

(If `Boolean` becomes unused in that file's `sqlalchemy` import line as a result, remove it from
the import too - check with a text search for `Boolean` elsewhere in the file first.)

Replace the full contents of `backend/alembic/versions/b2e9f4a71c83_org_features_user_staff.py`
with:

```python
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
```

- [ ] **Step 7: Run the full backend test suite to confirm nothing else references `is_staff` yet**

Run: `python -m pytest -q`
Expected: New failures in `test_admin_customers.py`, `test_feature_gating.py`,
`test_org_features_migration.py`, and `app/api/routes/orgs.py`/`app/api/deps.py` import errors -
this is expected; those are fixed in Tasks 2-4. Confirm `test_staff_account_model.py` still passes
and no *other* unrelated test broke.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/staff.py backend/app/models/user.py backend/alembic/env.py backend/alembic/versions/b2e9f4a71c83_org_features_user_staff.py backend/tests/conftest.py backend/tests/test_staff_account_model.py
git commit -m "feat: add StaffAccount model, drop User.is_staff"
```

---

### Task 2: Staff JWT issuance + auth dependency

**Files:**
- Modify: `backend/app/core/security.py` (add `create_staff_access_token`)
- Modify: `backend/app/api/deps.py` (add `StaffSession`, `get_current_staff`, `require_staff_session`; remove `require_staff`)
- Test: `backend/tests/test_staff_session.py`

**Interfaces:**
- Consumes: `StaffAccount` (Task 1), `decode_token` (existing, `app.core.security`).
- Produces: `create_staff_access_token(*, staff_id: str) -> str`; `StaffSession` (attrs `.id: uuid.UUID`, `.employee_id: str`); `get_current_staff` / `require_staff_session` (same callable, FastAPI dependency, returns `StaffSession`, raises 401). Tasks 3 and 4 depend on all of these.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_staff_session.py
from __future__ import annotations

import uuid

import pytest

from app.core.security import create_staff_access_token, decode_token

pytestmark = pytest.mark.asyncio


def test_staff_access_token_has_staff_type_and_correct_subject():
    staff_id = str(uuid.uuid4())
    token = create_staff_access_token(staff_id=staff_id)

    payload = decode_token(token)
    assert payload["type"] == "staff_access"
    assert payload["sub"] == staff_id


async def test_get_current_staff_rejects_a_customer_access_token(client, db_session):
    from app.models.staff import StaffAccount
    from app.core.security import hash_password
    from app.api.deps import get_current_staff
    from app.core.config import get_settings
    import jwt as pyjwt

    staff = StaffAccount(employee_id="e1", hashed_password=hash_password("x"))
    db_session.add(staff)
    await db_session.commit()

    settings = get_settings()
    # A well-formed customer "access" token, not a "staff_access" one.
    customer_token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "org_id": str(uuid.uuid4()), "type": "access"},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(Exception):
        await get_current_staff(token=customer_token, db=db_session)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_staff_session.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_staff_access_token'`

- [ ] **Step 3: Add `create_staff_access_token` to `app/core/security.py`**

Add directly below the existing `create_access_token` function:

```python
def create_staff_access_token(*, staff_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": staff_id,
        "type": "staff_access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
```

- [ ] **Step 4: Add `StaffSession`/`get_current_staff`/`require_staff_session` to `app/api/deps.py`, remove `require_staff`**

Add the import `from app.models.staff import StaffAccount` near the existing `from app.models.user
import User` line. Delete the existing `require_staff` function:

```python
async def require_staff(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.user.is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff only")
    return current
```

Replace it with:

```python
class StaffSession:
    def __init__(self, staff: StaffAccount):
        self.id = staff.id
        self.employee_id = staff.employee_id


async def get_current_staff(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> StaffSession:
    credentials_error = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "staff_access":
            raise credentials_error
        staff_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise credentials_error from exc

    result = await db.execute(select(StaffAccount).where(StaffAccount.id == staff_id))
    staff = result.scalar_one_or_none()
    if staff is None:
        raise credentials_error
    return StaffSession(staff)


# No roles/tiers among staff - one dependency is enough, but a distinct name
# at call sites (admin.py) reads better than a bare get_current_staff there.
require_staff_session = get_current_staff
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_staff_session.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/security.py backend/app/api/deps.py backend/tests/test_staff_session.py
git commit -m "feat: add staff JWT issuance and auth dependency"
```

---

### Task 3: `POST /staff/auth/login` endpoint

**Files:**
- Create: `backend/app/api/routes/staff_auth.py`
- Modify: `backend/app/main.py` (register the router)
- Test: `backend/tests/test_staff_auth.py`

**Interfaces:**
- Consumes: `StaffAccount` (Task 1), `create_staff_access_token`/`get_current_staff` (Task 2), `verify_password_timing_safe`/`hash_password` (existing, `app.core.security`), `rate_limit` (existing, `app.middleware.rate_limit`).
- Produces: `POST /staff/auth/login` returning `{access_token, token_type}`. Task 9 (frontend) calls this.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_staff_auth.py
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.db import session as db_session
from app.models.staff import StaffAccount

pytestmark = pytest.mark.asyncio


async def _seed_staff(employee_id="zeeshan", password="correct-horse-battery-staple1"):
    async with db_session.async_session_factory() as db:
        db.add(StaffAccount(employee_id=employee_id, hashed_password=hash_password(password)))
        await db.commit()


async def test_staff_login_succeeds_with_correct_credentials(client):
    await _seed_staff()
    res = await client.post("/staff/auth/login", json={"employee_id": "zeeshan", "password": "correct-horse-battery-staple1"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_staff_login_401s_on_wrong_password(client):
    await _seed_staff()
    res = await client.post("/staff/auth/login", json={"employee_id": "zeeshan", "password": "wrong-password-here"})
    assert res.status_code == 401


async def test_staff_login_401s_on_unknown_employee_id(client):
    res = await client.post("/staff/auth/login", json={"employee_id": "nobody", "password": "whatever12345"})
    assert res.status_code == 401


async def test_staff_token_rejected_by_customer_only_route(client):
    await _seed_staff()
    login = await client.post("/staff/auth/login", json={"employee_id": "zeeshan", "password": "correct-horse-battery-staple1"})
    staff_token = login.json()["access_token"]

    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {staff_token}"})
    assert res.status_code == 401
```

Note: `test_admin_customers.py`'s existing helper (`_register_login_and_make_staff`) is updated in
Task 4, not here - this task only proves the login endpoint itself.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_staff_auth.py -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Create `backend/app/api/routes/staff_auth.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import create_staff_access_token, verify_password_timing_safe
from app.db.session import get_db
from app.middleware.rate_limit import rate_limit
from app.core.config import get_settings
from app.models.staff import StaffAccount

settings = get_settings()
router = APIRouter(prefix="/staff/auth", tags=["staff-auth"])
_staff_login_rate_limit = rate_limit("staff_login", settings.rate_limit_login_per_5min, 300)


class StaffLoginRequest(BaseModel):
    employee_id: str
    password: str


class StaffTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=StaffTokenResponse, dependencies=[Depends(_staff_login_rate_limit)])
async def staff_login(body: StaffLoginRequest, db: AsyncSession = Depends(get_db)):
    staff = (
        await db.execute(select(StaffAccount).where(StaffAccount.employee_id == body.employee_id))
    ).scalar_one_or_none()
    # Always pay the argon2 cost, even for an unknown employee_id, so this
    # path can't be timed to enumerate valid IDs (same protection customer
    # login already has via the same helper).
    ok = await run_in_threadpool(
        verify_password_timing_safe, body.password, staff.hashed_password if staff else None
    )
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    return StaffTokenResponse(access_token=create_staff_access_token(staff_id=str(staff.id)))
```

- [ ] **Step 4: Register the router in `backend/app/main.py`**

Add `staff_auth` to the `from app.api.routes import (...)` list (alphabetically, right after
`shifts` or wherever it sorts), and add this line right after `app.include_router(auth.router)`:

```python
app.include_router(staff_auth.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_staff_auth.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/staff_auth.py backend/app/main.py backend/tests/test_staff_auth.py
git commit -m "feat: add POST /staff/auth/login"
```

---

### Task 4: Swap `/admin/*` to staff sessions, remove `is_staff` from orgs/customer auth

**Files:**
- Modify: `backend/app/api/routes/admin.py`
- Modify: `backend/app/api/routes/orgs.py`
- Modify: `backend/tests/test_admin_customers.py`
- Modify: `backend/tests/test_feature_gating.py`
- Modify: `backend/tests/test_org_features_migration.py`

**Interfaces:**
- Consumes: `StaffSession`/`require_staff_session` (Task 2).

- [ ] **Step 1: Update `admin.py`'s import and dependency**

Change:
```python
from app.api.deps import CurrentUser, require_staff
```
to:
```python
from app.api.deps import StaffSession, require_staff_session
```

Change the router line:
```python
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff)])
```
to:
```python
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff_session)])
```

In `create_customer`, `list_customers`, and `update_customer_features`, replace every
`current: CurrentUser = Depends(require_staff)` (or `_current: CurrentUser = Depends(require_staff)`
in `list_customers`) with `current: StaffSession = Depends(require_staff_session)` (keeping the
`_current`/`current` name exactly as it already is in each function - only the type and dependency
change).

- [ ] **Step 2: Remove `is_staff` from `orgs.py`**

In `backend/app/api/routes/orgs.py`, remove the `is_staff: bool` field from `OrgResponse`, and
remove `is_staff=current.user.is_staff,` from both `OrgResponse(...)` construction call sites (in
`get_my_org` and `update_my_org`).

- [ ] **Step 3: Update `test_admin_customers.py`'s staff-token helper**

Replace:
```python
async def _register_login_and_make_staff(client, email="staff@evowerk.example", org_name="EvoWerk Staff"):
    token, user_id = await _register_and_login(client, email=email, org_name=org_name)
    async with db_session.async_session_factory() as db:
        user = (await db.execute(select(User).where(User.id == uuid.UUID(user_id)))).scalar_one()
        user.is_staff = True
        await db.commit()
    return token
```
with:
```python
async def _make_staff_token(client, employee_id="staff1", password="correct-horse-battery-staple1"):
    async with db_session.async_session_factory() as db:
        db.add(StaffAccount(employee_id=employee_id, hashed_password=hash_password(password)))
        await db.commit()
    login = await client.post("/staff/auth/login", json={"employee_id": employee_id, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]
```
Add the imports this needs at the top of the file:
```python
from app.core.security import hash_password
from app.models.staff import StaffAccount
```
Replace every call site `await _register_login_and_make_staff(client)` with
`await _make_staff_token(client)` (same call shape, drop the now-unused `staff_token = ` variable
name changes only if the original assigned differently - check each call site keeps its existing
variable name, e.g. `staff_token = await _make_staff_token(client)`).

Replace `test_non_staff_cannot_create_customer`'s body - it previously proved a non-staff *user*
gets 403; there's no such thing as a non-staff user anymore, so this becomes "no token at all gets
401, and a customer's own access token gets 401 too":
```python
async def test_no_token_cannot_create_customer(client):
    res = await client.post(
        "/admin/customers",
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert res.status_code == 401


async def test_customer_token_cannot_create_customer(client):
    customer_token, _user_id = await _register_and_login(client)
    res = await client.post(
        "/admin/customers", headers=_h(customer_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert res.status_code == 401
```
(FastAPI's `OAuth2PasswordBearer`-backed dependency raises 401, not 403, on a token that fails
`get_current_staff`'s checks - unlike the old `require_staff`, which 403'd an authenticated-but-
wrong-role *user*. There's no "authenticated as the wrong kind of principal" state anymore, only
"not a valid staff session," which is a 401.)

- [ ] **Step 4: Update `test_feature_gating.py`**

In `test_orgs_me_exposes_enabled_features_and_is_staff`, rename it to
`test_orgs_me_exposes_enabled_features` and remove the `assert body["is_staff"] is False` line
(keep the `enabled_features` assertion).

- [ ] **Step 5: Update `test_org_features_migration.py`**

Replace:
```python
async def test_new_org_and_user_default_to_no_entitlements(client):
    _token, org_id, user_id = await _register_and_login(client)

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == uuid.UUID(org_id)))).scalar_one()
        user = (await db.execute(select(User).where(User.id == uuid.UUID(user_id)))).scalar_one()

    assert org.enabled_features == []
    assert user.is_staff is False
```
with:
```python
async def test_new_org_defaults_to_no_entitlements(client):
    _token, org_id, _user_id = await _register_and_login(client)

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == uuid.UUID(org_id)))).scalar_one()

    assert org.enabled_features == []
```
Remove the now-unused `from app.models.user import User` import from this file if nothing else in
it references `User`.

- [ ] **Step 6: Run the full backend test suite**

Run: `python -m pytest -q`
Expected: All tests pass, 0 failures.

- [ ] **Step 7: Run ruff**

Run: `python -m ruff check app/api/routes/admin.py app/api/routes/orgs.py tests/test_admin_customers.py tests/test_feature_gating.py tests/test_org_features_migration.py`
Expected: All checks passed (catches any now-unused imports, e.g. `uuid`/`User`/`select` if a file
no longer needs them - fix any it flags).

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routes/admin.py backend/app/api/routes/orgs.py backend/tests/test_admin_customers.py backend/tests/test_feature_gating.py backend/tests/test_org_features_migration.py
git commit -m "feat: gate /admin/* behind staff sessions instead of User.is_staff"
```

---

### Task 5: Staff account seeding script

**Files:**
- Create: `backend/scripts/create_staff_account.py`
- Create: `backend/scripts/__init__.py` (empty, makes it an importable package for the test below)
- Test: `backend/tests/test_create_staff_account_script.py`

**Interfaces:**
- Consumes: `StaffAccount` (Task 1), `hash_password` (existing, `app.core.security`).
- Produces: `scripts.create_staff_account.create_staff_account(db, employee_id, password) -> StaffAccount` (the testable core, wrapped by a `main()` that handles CLI args/`getpass`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_create_staff_account_script.py
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.security import verify_password
from app.models.staff import StaffAccount
from scripts.create_staff_account import create_staff_account

pytestmark = pytest.mark.asyncio


async def test_create_staff_account_inserts_a_row_with_a_verifiable_password(db_session):
    await create_staff_account(db_session, employee_id="new.hire", password="correct-horse-battery-staple1")

    row = (
        await db_session.execute(select(StaffAccount).where(StaffAccount.employee_id == "new.hire"))
    ).scalar_one()
    assert verify_password("correct-horse-battery-staple1", row.hashed_password)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_create_staff_account_script.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts'`

- [ ] **Step 3: Create the package + script**

```python
# backend/scripts/__init__.py
```

```python
# backend/scripts/create_staff_account.py
from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.session import async_session_factory
from app.models.staff import StaffAccount


async def create_staff_account(db: AsyncSession, *, employee_id: str, password: str) -> StaffAccount:
    staff = StaffAccount(employee_id=employee_id, hashed_password=hash_password(password))
    db.add(staff)
    await db.commit()
    return staff


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed one EvoWerk staff account.")
    parser.add_argument("employee_id")
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords did not match.")

    async with async_session_factory() as db:
        await create_staff_account(db, employee_id=args.employee_id, password=password)
    print(f"Created staff account for {args.employee_id!r}.")


if __name__ == "__main__":
    asyncio.run(_main())
```

Check `backend/app/db/session.py` for the exact exported name of the session factory used
elsewhere (e.g. `db_session.async_session_factory` is referenced in
`backend/tests/test_staff_auth.py` above via `from app.db import session as db_session` -
`async_session_factory` is the confirmed existing name; import it directly here instead).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_create_staff_account_script.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/__init__.py backend/scripts/create_staff_account.py backend/tests/test_create_staff_account_script.py
git commit -m "feat: add CLI script to seed staff accounts"
```

---

### Task 6: `Switch` UI primitive (frontend)

**Files:**
- Create: `frontend/src/components/ui/switch.tsx`

**Interfaces:**
- Produces: `Switch` component, props `{ checked: boolean; onCheckedChange: (checked: boolean) => void; className?: string }`. Task 9 uses this.

- [ ] **Step 1: Create the component**

```tsx
// frontend/src/components/ui/switch.tsx
import { cn } from "@/lib/utils";

export function Switch({
  checked,
  onCheckedChange,
  className,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
        checked ? "bg-primary" : "bg-input",
        className,
      )}
    >
      <span
        className={cn(
          "inline-block size-4 transform rounded-full bg-background shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}
```

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/switch.tsx
git commit -m "feat: add Switch UI primitive"
```

---

### Task 7: Staff auth store + staff API client (frontend)

**Files:**
- Create: `frontend/src/store/staffAuthStore.ts`
- Create: `frontend/src/api/staffClient.ts`

**Interfaces:**
- Produces: `useStaffAuthStore` (Zustand store, `{ accessToken: string | null; setToken: (t: string) => void; clear: () => void }`), `useStaffAuthHydrated(): boolean`, `staffApiFetch(path, init?): Promise<Response>`. Tasks 8-10 consume all three.

- [ ] **Step 1: Create the staff auth store**

Mirrors `frontend/src/store/authStore.ts` exactly, minus refresh tokens (see spec Non-goals - no
refresh flow for staff v1):

```typescript
// frontend/src/store/staffAuthStore.ts
import { useEffect, useState } from "react";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface StaffAuthState {
  accessToken: string | null;
  setToken: (accessToken: string) => void;
  clear: () => void;
}

// sessionStorage, same rationale as the customer authStore: ends when the tab
// closes, survives a refresh within the session.
export const useStaffAuthStore = create<StaffAuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      setToken: (accessToken) => set({ accessToken }),
      clear: () => set({ accessToken: null }),
    }),
    {
      name: "staff-auth",
      storage: createJSONStorage(() => sessionStorage),
    },
  ),
);

export function useStaffAuthHydrated(): boolean {
  const [hydrated, setHydrated] = useState(useStaffAuthStore.persist.hasHydrated());
  useEffect(() => {
    setHydrated(useStaffAuthStore.persist.hasHydrated());
    return useStaffAuthStore.persist.onFinishHydration(() => setHydrated(true));
  }, []);
  return hydrated;
}
```

- [ ] **Step 2: Create the staff API client**

No refresh-and-retry logic (no refresh token exists for staff) - on a 401, clear the token so the
reactive `useStaffAuthStore` subscriber in `RequireStaffSession` (Task 9) redirects to `/login` on
its own, the same way `RequireAuth` reacts to `useAuthStore`'s `accessToken` going `null`:

```typescript
// frontend/src/api/staffClient.ts
import { useStaffAuthStore } from "@/store/staffAuthStore";

export async function staffApiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { accessToken } = useStaffAuthStore.getState();
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) useStaffAuthStore.getState().clear();
  return res;
}
```

- [ ] **Step 3: Typecheck**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/store/staffAuthStore.ts frontend/src/api/staffClient.ts
git commit -m "feat: add separate staff auth store and API client"
```

---

### Task 8: "Staff Login" entry point on the public login page

**Files:**
- Modify: `frontend/src/pages/Login.tsx`

**Interfaces:**
- Consumes: `useStaffAuthStore` (Task 7), `staffApiFetch` (Task 7).

- [ ] **Step 1: Add staff-mode state and a form-swap link**

In `frontend/src/pages/Login.tsx`, add these imports:

```typescript
import { useStaffAuthStore } from "@/store/staffAuthStore";
import { staffApiFetch } from "@/api/staffClient";
```

Add state alongside the existing `email`/`password` state:

```typescript
const [staffMode, setStaffMode] = useState(false);
const [employeeId, setEmployeeId] = useState("");
const setStaffToken = useStaffAuthStore((s) => s.setToken);
```

Add a staff-login submit handler alongside `onSubmit`:

```typescript
async function onStaffSubmit(e: FormEvent) {
  e.preventDefault();
  setError(null);
  setSubmitting(true);
  try {
    const res = await staffApiFetch("/staff/auth/login", {
      method: "POST",
      body: JSON.stringify({ employee_id: employeeId, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(errorMessage(body, "Login failed"));
      return;
    }
    const data = await res.json();
    setStaffToken(data.access_token);
    navigate("/staff");
  } finally {
    setSubmitting(false);
  }
}
```

- [ ] **Step 2: Swap the rendered form based on `staffMode`**

Replace the `<form onSubmit={onSubmit} ...>` block's opening so it branches, and add the toggle
link right after the closing `</form>` (before the existing "Resend verification email" block,
which should only show in customer mode):

```tsx
{staffMode ? (
  <form onSubmit={onStaffSubmit} className="space-y-4">
    <Field label="Employee ID" htmlFor="employee-id">
      <Input id="employee-id" value={employeeId} onChange={(e) => setEmployeeId(e.target.value)} required />
    </Field>
    <Field label="Password" htmlFor="staff-password">
      <Input id="staff-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
    </Field>
    {error && <Alert variant="destructive">{error}</Alert>}
    <Button type="submit" className="w-full" loading={submitting}>
      Log in
    </Button>
  </form>
) : (
  <form onSubmit={onSubmit} className="space-y-4">
    {/* ...unchanged existing customer form body... */}
  </form>
)}
{!staffMode && (
  resendState === "sent" ? (
    <Alert variant="success" className="mt-4">
      Check your email - if that account exists and isn't verified yet, a new verification link is on its way.
    </Alert>
  ) : (
    <button
      type="button"
      onClick={onResendVerification}
      disabled={resendState === "sending"}
      className="mt-4 block w-full text-center text-xs font-medium text-primary hover:underline disabled:opacity-50"
    >
      Resend verification email
    </button>
  )
)}
<button
  type="button"
  onClick={() => { setStaffMode((s) => !s); setError(null); }}
  className="mt-6 block w-full text-center text-xs font-medium text-muted-foreground hover:underline"
>
  {staffMode ? "Back to customer login" : "Staff Login"}
</button>
```

Wrap the existing OAuth-providers block (`{(providers.google || providers.microsoft) && (...)}`) in
`{!staffMode && ...}` too, since Google/Microsoft OAuth is a customer-only concern.

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Login.tsx
git commit -m "feat: add Staff Login entry point to the login page"
```

---

### Task 9: Staff Portal page + route wiring

**Files:**
- Create: `frontend/src/pages/staff/StaffPortal.tsx` (replaces `frontend/src/pages/admin/Customers.tsx`)
- Delete: `frontend/src/pages/admin/Customers.tsx`
- Modify: `frontend/src/App.tsx` (add `RequireStaffSession`, `/staff` route, remove `RequireStaff`/`/admin/customers`)

**Interfaces:**
- Consumes: `useStaffAuthStore`/`useStaffAuthHydrated` (Task 7), `staffApiFetch` (Task 7), `Switch` (Task 6).

- [ ] **Step 1: Create the Staff Portal page**

This moves `Customers.tsx`'s content into a single self-contained page (no separate shell file - a
standalone portal is one page, not a nested route tree) with its own minimal header instead of
`AppShell`, `staffApiFetch` instead of `apiFetch`, and `FeatureToggles` (using `Switch`) instead of
`FeatureCheckboxes`:

```tsx
// frontend/src/pages/staff/StaffPortal.tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { staffApiFetch } from "@/api/staffClient";
import { useStaffAuthStore } from "@/store/staffAuthStore";
import { BrandMark } from "@/components/BrandMark";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { CORE_FEATURES, FEATURES, PREMIUM_FEATURES } from "@/lib/features";

interface Customer {
  org_id: string;
  org_name: string;
  owner_email: string;
  enabled_features: string[];
}

function FeatureToggles({
  selected,
  onChange,
}: {
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  function toggle(key: string) {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(next);
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => onChange(new Set(CORE_FEATURES))}>
          Core
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => onChange(new Set(PREMIUM_FEATURES))}>
          Core + Premium
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {Object.entries(FEATURES).map(([key, def]) => (
          <label key={key} className="flex items-center gap-2 text-sm">
            <Switch checked={selected.has(key)} onCheckedChange={() => toggle(key)} />
            {def.label}
          </label>
        ))}
      </div>
    </div>
  );
}

function NewCustomerForm({ onCreated }: { onCreated: () => void }) {
  const [orgName, setOrgName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [features, setFeatures] = useState<Set<string>>(new Set(CORE_FEATURES));

  const create = useMutation({
    mutationFn: async () => {
      const res = await staffApiFetch("/admin/customers", {
        method: "POST",
        body: JSON.stringify({ org_name: orgName, owner_email: ownerEmail, enabled_features: Array.from(features) }),
      });
      if (!res.ok) throw new Error("Failed to create customer");
      return res.json();
    },
    onSuccess: () => {
      setOrgName("");
      setOwnerEmail("");
      setFeatures(new Set(CORE_FEATURES));
      onCreated();
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>New customer</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="Organization name">
          <Input value={orgName} onChange={(e) => setOrgName(e.target.value)} />
        </Field>
        <Field label="Owner email">
          <Input type="email" value={ownerEmail} onChange={(e) => setOwnerEmail(e.target.value)} />
        </Field>
        <FeatureToggles selected={features} onChange={setFeatures} />
        <Button
          disabled={!orgName || !ownerEmail}
          loading={create.isPending}
          onClick={() => create.mutate()}
        >
          Create customer
        </Button>
      </CardContent>
    </Card>
  );
}

function EditCustomerRow({ customer, onSaved }: { customer: Customer; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [features, setFeatures] = useState<Set<string>>(new Set(customer.enabled_features));

  const update = useMutation({
    mutationFn: async () => {
      const res = await staffApiFetch(`/admin/customers/${customer.org_id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled_features: Array.from(features) }),
      });
      if (!res.ok) throw new Error("Failed to update customer");
      return res.json();
    },
    onSuccess: () => {
      setEditing(false);
      onSaved();
    },
  });

  if (!editing) {
    return (
      <TR>
        <TD className="font-medium">{customer.org_name}</TD>
        <TD>{customer.owner_email}</TD>
        <TD>
          {customer.enabled_features.length === 0 ? (
            <span className="text-muted-foreground">None</span>
          ) : (
            customer.enabled_features.map((k) => (
              <Badge key={k} variant="neutral" className="mr-1">
                {FEATURES[k]?.label ?? k}
              </Badge>
            ))
          )}
        </TD>
        <TD>
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            Edit features
          </Button>
        </TD>
      </TR>
    );
  }

  return (
    <TR>
      <TD colSpan={4}>
        <div className="space-y-3 py-2">
          <div className="font-medium">{customer.org_name}</div>
          <FeatureToggles selected={features} onChange={setFeatures} />
          <div className="flex gap-2">
            <Button size="sm" loading={update.isPending} onClick={() => update.mutate()}>
              Save
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      </TD>
    </TR>
  );
}

export function StaffPortal() {
  const navigate = useNavigate();
  const clearStaffAuth = useStaffAuthStore((s) => s.clear);
  const qc = useQueryClient();
  const customers = useQuery<Customer[]>({
    queryKey: ["admin-customers"],
    queryFn: async () => {
      const res = await staffApiFetch("/admin/customers");
      if (!res.ok) throw new Error("Failed to load customers");
      return res.json();
    },
  });

  function refresh() {
    qc.invalidateQueries({ queryKey: ["admin-customers"] });
  }

  function logout() {
    clearStaffAuth();
    navigate("/login");
  }

  return (
    <div className="min-h-screen">
      <header className="flex h-14 items-center justify-between border-b border-border/70 px-5">
        <BrandMark className="size-8 text-foreground" title="Evowerk" />
        <Button variant="ghost" size="icon" onClick={logout} aria-label="Log out" title="Log out">
          <LogOut />
        </Button>
      </header>
      <main className="mx-auto w-full max-w-4xl space-y-6 px-6 py-8">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Customers</h1>
          <p className="text-sm text-muted-foreground">Onboard new customers and manage existing customers' feature access.</p>
        </div>
        <NewCustomerForm onCreated={refresh} />
        <Card>
          <CardHeader>
            <CardTitle>Existing customers</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Organization</TH>
                    <TH>Owner</TH>
                    <TH>Features</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {customers.data?.map((c) => (
                    <EditCustomerRow key={c.org_id} customer={c} onSaved={refresh} />
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Delete the old file**

```bash
rm frontend/src/pages/admin/Customers.tsx
```

- [ ] **Step 3: Wire the route in `App.tsx`**

Remove the `Customers` lazy import:
```typescript
const Customers = lazyPage(() => import("./pages/admin/Customers"), "Customers");
```
Add instead:
```typescript
const StaffPortal = lazyPage(() => import("./pages/staff/StaffPortal"), "StaffPortal");
```

Remove the `RequireStaff` component entirely:
```typescript
export function RequireStaff({ children }: { children: JSX.Element }) {
  const org = useCurrentUser();
  if (org.isLoading) return null;
  if (!org.data?.is_staff) return <Navigate to="/" replace />;
  return children;
}
```
Replace it with (uses the Task 7 store directly - no server round-trip needed just to gate the
route, since `/admin/*` calls themselves 401 on an invalid/expired token regardless):
```typescript
function RequireStaffSession({ children }: { children: JSX.Element }) {
  const accessToken = useStaffAuthStore((s) => s.accessToken);
  const hydrated = useStaffAuthHydrated();
  if (!hydrated) return null;
  if (!accessToken) return <Navigate to="/login" replace />;
  return children;
}
```
Add its imports near the top of `App.tsx`:
```typescript
import { useStaffAuthHydrated, useStaffAuthStore } from "@/store/staffAuthStore";
```

Remove the old route:
```tsx
<Route path="/admin/customers" element={<RequireStaff><Customers /></RequireStaff>} />
```
Add a new top-level route (sibling to `/login`, not nested under the `AppShell`-wrapping `<Route>`
- see the routes right after `<Route path="/login" element={<Login />} />`):
```tsx
<Route path="/staff" element={<RequireStaffSession><StaffPortal /></RequireStaffSession>} />
```

- [ ] **Step 4: Typecheck**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/staff/StaffPortal.tsx frontend/src/App.tsx
git rm frontend/src/pages/admin/Customers.tsx
git commit -m "feat: add standalone Staff Portal, remove old admin/customers route"
```

---

### Task 10: Remove remaining `is_staff` traces from the customer app

**Files:**
- Modify: `frontend/src/lib/currentUser.ts` (remove `is_staff`)
- Modify: `frontend/src/lib/nav.ts` (remove "Staff" section, `requiresStaff`)
- Modify: `frontend/src/components/AppShell.tsx` (remove `isStaff` from nav gating)

**Interfaces:**
- None - this task only deletes now-dead code introduced by the earlier feature-entitlements work; nothing later depends on what's removed here.

- [ ] **Step 1: Remove `is_staff` from `CurrentUser`**

In `frontend/src/lib/currentUser.ts`, remove the `is_staff: boolean;` line from the `CurrentUser`
interface.

- [ ] **Step 2: Remove the "Staff" nav section and `requiresStaff`**

In `frontend/src/lib/nav.ts`:
- Remove the `requiresStaff?: boolean;` field (and its doc comment) from the `NavItem` interface.
- Remove the entire `{ label: "Staff", items: [...] }` section from `NAV_SECTIONS`.
- In `isNavItemVisible`, remove the `requiresStaff`/`isStaff` check:
```typescript
export function isNavItemVisible(
  item: Pick<NavItem, "featureKey" | "requiresStaff">,
  { enabledFeatures, isStaff }: { enabledFeatures: Set<string>; isStaff: boolean },
): boolean {
  if (item.requiresStaff && !isStaff) return false;
  if (item.featureKey && !enabledFeatures.has(item.featureKey)) return false;
  return true;
}
```
becomes:
```typescript
export function isNavItemVisible(
  item: Pick<NavItem, "featureKey">,
  { enabledFeatures }: { enabledFeatures: Set<string> },
): boolean {
  if (item.featureKey && !enabledFeatures.has(item.featureKey)) return false;
  return true;
}
```

- [ ] **Step 3: Update `AppShell.tsx`'s call sites**

Remove:
```typescript
const isStaff = org.data?.is_staff ?? false;
const navGate = { enabledFeatures, isStaff };
```
Replace with:
```typescript
const navGate = { enabledFeatures };
```

- [ ] **Step 4: Typecheck**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: No errors (this will surface any other `isNavItemVisible`/`NavItem` caller this plan
missed - fix any it flags the same way).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/currentUser.ts frontend/src/lib/nav.ts frontend/src/components/AppShell.tsx
git commit -m "chore: remove is_staff traces from the customer-facing app"
```

---

### Task 11: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run (from `backend/`): `python -m pytest -q`
Expected: All tests pass, 0 failures (staff-related tests from Tasks 1-5, plus every pre-existing
test, since Task 4 already fixed the ones that referenced `is_staff`).

- [ ] **Step 2: Run backend ruff on every touched file**

Run: `python -m ruff check app/models/staff.py app/models/user.py app/core/security.py app/api/deps.py app/api/routes/staff_auth.py app/api/routes/admin.py app/api/routes/orgs.py app/main.py scripts/create_staff_account.py`
Expected: All checks passed.

- [ ] **Step 3: Run frontend typecheck**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: No errors.

- [ ] **Step 4: Manual smoke test**

Start both dev servers (`backend-dev` and `frontend` from `.claude/launch.json`), then:
1. Seed a staff account: `python -m scripts.create_staff_account demo.staff` (from `backend/`,
   enter a password when prompted).
2. Open the login page, click "Staff Login", log in with `demo.staff` and that password - confirm
   you land on `/staff` and see the Customers list/create form.
3. Create a customer with only `oee` enabled. Confirm the response shows `enabled_features: ["oee"]`.
4. In a private/incognito window, register that new customer's owner account is not directly
   possible (no self-registration link) - instead confirm the created owner can complete
   "forgot password" using the email `create_customer` triggers, then log in and see only the OEE
   nav item under Plant Analytics, no "Customers" link anywhere (sidebar, mobile bar, or Ctrl+K).
5. Log out of the staff portal, confirm `/staff` redirects to `/login` when visited directly
   afterward.

- [ ] **Step 5: Final commit (if anything changed during verification)**

```bash
git add -A
git commit -m "test: verify staff portal end to end"
```
