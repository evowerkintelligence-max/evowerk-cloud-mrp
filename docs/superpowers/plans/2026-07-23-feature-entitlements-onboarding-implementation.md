# Feature Entitlements & Staff Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every org a per-feature entitlement list that backend routes and frontend nav/routes
enforce, plus a staff-only page to onboard new customers (creating their account and setting their
initial features) and edit existing customers' features later.

**Architecture:** Two additive DB columns (`Org.enabled_features`, `User.is_staff`), a shared
feature-key registry (Python + a hand-kept TS mirror), a `require_feature`/`require_staff`
dependency pair applied once per router at `main.py`'s existing `include_router()` calls (no
individual route files touched), three new staff-only `/admin/customers` endpoints reusing the
existing org-creation and password-reset-email machinery, and matching frontend nav filtering +
route guards mirroring the existing `RequireAuth` pattern.

**Tech Stack:** FastAPI/SQLAlchemy (async)/Alembic, React/TypeScript/TanStack Query/React Router,
pytest. No new dependencies.

**Full design reference:** `docs/superpowers/specs/2026-07-23-feature-entitlements-onboarding-design.md`

## Global Constraints

- No new tables - `Org.enabled_features` and `User.is_staff` are additive columns on existing models.
- `POST /auth/register` is NOT deleted or gated - only its frontend route/link is removed (per the
  spec's Scope decision: ~90 test files depend on it for fixtures).
- Backend feature keys and their frontend mirror must stay in sync - enforced by a test, not
  documentation alone.
- `enabled_features` is the only source of truth for what an org can access - no separate `plan`
  label is stored anywhere.
- `is_staff` is never settable through any endpoint in this plan - it's a DB-only flag.

---

### Task 1: Data model - `Org.enabled_features`, `User.is_staff`

**Files:**
- Modify: `backend/app/models/tenant.py` (add `enabled_features` to `Org`)
- Modify: `backend/app/models/user.py` (add `is_staff` to `User`)
- Create: `backend/alembic/versions/b2e9f4a71c83_org_features_user_staff.py`
- Test: `backend/tests/test_org_features_migration.py`

**Interfaces:**
- Produces (consumed by Task 2+): `Org.enabled_features: list[str]` (JSON column, default `[]`,
  never null), `User.is_staff: bool` (default `False`, never null).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_org_features_migration.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.tenant import Org
from app.models.user import User

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"], reg.json()["org_id"], reg.json()["user_id"]


async def test_new_org_and_user_default_to_no_entitlements(client):
    _token, org_id, user_id = await _register_and_login(client)

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one()
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()

    assert org.enabled_features == []
    assert user.is_staff is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `backend/`): `pytest tests/test_org_features_migration.py -v`
Expected: FAIL - `AttributeError: 'Org' object has no attribute 'enabled_features'` (the column
doesn't exist yet).

- [ ] **Step 3: Add the columns to the models**

In `backend/app/models/tenant.py`, add the import and column. Change:

```python
from sqlalchemy import DateTime, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
```

to:

```python
from sqlalchemy import JSON, DateTime, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
```

Then add this column to `Org`, directly after `adjust_approval_threshold`:

```python
    # Single source of truth for what this org can access - no separate "plan"
    # label is stored anywhere, so the two can never drift apart. See
    # backend/app/core/features.py for the valid keys.
    enabled_features: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
```

In `backend/app/models/user.py`, add this column to `User`, directly after `is_verified`:

```python
    # EvoWerk employee who can manage any org via /admin/customers - distinct
    # from `role` above, which only ever governs permissions within one's own
    # org. Never set through any endpoint - flipped directly in the DB.
    is_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

(`Boolean` is already imported in `user.py` - no import change needed there.)

- [ ] **Step 4: Create the migration**

Create `backend/alembic/versions/b2e9f4a71c83_org_features_user_staff.py`:

```python
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


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("enabled_features", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "users",
        sa.Column("is_staff", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_staff")
    op.drop_column("orgs", "enabled_features")
```

- [ ] **Step 5: Run the migration**

Run (from `backend/`): `.venv\Scripts\python.exe -m alembic upgrade head`
Expected: no errors, ends at revision `b2e9f4a71c83`.

- [ ] **Step 6: Run the new test to verify it passes**

Run (from `backend/`): `pytest tests/test_org_features_migration.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full backend suite to check for regressions**

Run (from `backend/`): `pytest -q`
Expected: same pass count as before this change, plus the one new test - 0 new failures.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/tenant.py backend/app/models/user.py backend/alembic/versions/b2e9f4a71c83_org_features_user_staff.py backend/tests/test_org_features_migration.py
git commit -m "feat: add Org.enabled_features and User.is_staff columns"
```

---

### Task 2: Feature registry + `require_feature`/`require_staff` + router wiring

**Files:**
- Create: `backend/app/core/features.py`
- Modify: `backend/app/api/deps.py` (add `require_feature`, `require_staff`)
- Modify: `backend/app/main.py` (add `dependencies=[Depends(require_feature(...))]` to gated routers)
- Modify: `backend/app/api/routes/orgs.py` (expose `enabled_features` + `is_staff` on `GET/PATCH /orgs/me`)
- Test: `backend/tests/test_feature_gating.py`

**Interfaces:**
- Produces (consumed by Task 3, Task 4, Task 5): `FEATURES: dict[str, tuple[str, str]]`,
  `CORE_FEATURES: list[str]`, `PREMIUM_FEATURES: list[str]` in `backend/app/core/features.py`.
  `require_feature(key: str)` and `require_staff` in `backend/app/api/deps.py`, same call shape as
  the existing `require_role(*roles)`. `OrgResponse.enabled_features: list[str]` and
  `OrgResponse.is_staff: bool` on both `/orgs/me` endpoints.
- Note on scope: `predictive.router` and `cbm.router` are gated by the **same** key, `"predictive"`
  - both back the single `/predictive` page (`Predictive.tsx` calls both `/predictive/*` and
  `/cbm/*`), so gating them under two different keys would let an org see half a working page.
  This is a deliberate correction of the design spec's registry table, which listed them as two
  separate keys without tracing through that both routers serve one frontend route - see this
  plan's Self-Review Notes.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_feature_gating.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.tenant import Org

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"], reg.json()["org_id"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_gated_route_403s_without_entitlement_then_200s_with_it(client):
    token, org_id = await _register_and_login(client)

    res = await client.get("/boms", headers=_h(token))
    assert res.status_code == 403, res.text

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one()
        org.enabled_features = ["boms"]
        await db.commit()

    res = await client.get("/boms", headers=_h(token))
    assert res.status_code == 200, res.text


async def test_ungated_route_ignores_entitlements(client):
    token, _org_id = await _register_and_login(client)

    # /datasets is deliberately never gated - Datasets is plumbing every
    # feature depends on, not a sellable feature itself (see the design spec's
    # Scope decision on Data & Reports).
    res = await client.get("/datasets", headers=_h(token))
    assert res.status_code == 200, res.text


async def test_orgs_me_exposes_enabled_features_and_is_staff(client):
    token, org_id = await _register_and_login(client)

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one()
        org.enabled_features = ["boms", "oee"]
        await db.commit()

    res = await client.get("/orgs/me", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled_features"] == ["boms", "oee"]
    assert body["is_staff"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `backend/`): `pytest tests/test_feature_gating.py -v`
Expected: FAIL - `GET /boms` returns 200 with no gating (test 1), and `/orgs/me`'s response has no
`enabled_features`/`is_staff` keys (test 3, `KeyError`).

- [ ] **Step 3: Create the feature registry**

Create `backend/app/core/features.py`:

```python
"""Per-org feature entitlements. See docs/superpowers/specs/2026-07-23-feature-entitlements-onboarding-design.md
for the full rationale, including why System-section and Data-exploration routers
(datasets, bi, integrations, realtime, workflows, alerts, quality, process_mining,
ai_insights, and the MRP master-data routers items/suppliers/locations/machines/
work_centers/routings) are absent from this registry - they're always-on plumbing,
not sellable features."""

# key -> (label, tier). "predictive" gates two routers (predictive.router AND
# cbm.router) since both back the single Predictive Maintenance page - see
# backend/app/main.py's include_router calls for both.
FEATURES: dict[str, tuple[str, str]] = {
    "boms": ("Bills of Materials", "core"),
    "procurement": ("Procurement", "core"),
    "lots": ("Lots", "core"),
    "warehouse": ("Warehouse", "core"),
    "planning": ("Planning", "core"),
    "plant_summary": ("Plant Summary", "core"),
    "oee": ("OEE", "core"),
    "pace_tracking": ("Pace Tracking", "core"),
    "downtime": ("Downtime", "core"),
    "downtime_tracking": ("Downtime Tracking", "core"),
    "throughput": ("Throughput", "core"),
    "scrap": ("Scrap & Quality", "core"),
    "shifts": ("Shifts", "core"),
    "spc": ("SPC", "core"),
    "kpis": ("KPIs", "core"),
    "kpi_dashboards": ("KPI Dashboards", "core"),
    "reports": ("Report Builder", "core"),
    "data_chat": ("Ask (AI data chat)", "core"),
    "automl": ("AutoML", "premium"),
    "predictive": ("Predictive Maintenance", "premium"),
    "simulation": ("Simulation", "premium"),
    "rca_cases": ("Root Cause Analysis", "premium"),
    "mfg_forecast": ("Manufacturing Forecast", "premium"),
    "computer_vision": ("Computer Vision", "premium"),
}

CORE_FEATURES: list[str] = [k for k, (_, tier) in FEATURES.items() if tier == "core"]
PREMIUM_FEATURES: list[str] = list(FEATURES.keys())  # Core + Premium = everything
```

- [ ] **Step 4: Add `require_feature` and `require_staff` to `deps.py`**

In `backend/app/api/deps.py`, add directly after `require_role` (after its closing `return _check`
line):

```python
def require_feature(key: str):
    async def _check(
        current: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        org_features = (
            await db.execute(select(Org.enabled_features).where(Org.id == current.org_id))
        ).scalar_one()
        if key not in org_features:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not included in your plan")
        return current

    return _check


async def require_staff(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.user.is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff only")
    return current
```

Add the missing import at the top of `deps.py` - change:

```python
from app.models.user import User
```

to:

```python
from app.models.tenant import Org
from app.models.user import User
```

(`get_db` is already imported; `select` is already imported.)

- [ ] **Step 5: Wire `require_feature` into `main.py`**

In `backend/app/main.py`, add the import. Change:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
```

to:

```python
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import require_feature
```

Then replace the entire router-registration block (from `app.include_router(auth.router)` through
`app.include_router(cbm.router)`, i.e. lines 99–147) with:

```python
app.include_router(auth.router)
if settings.google_oauth_client_id or settings.microsoft_oauth_client_id:
    app.include_router(oauth.router)
app.include_router(orgs.router)
app.include_router(items.router)
app.include_router(boms.router, dependencies=[Depends(require_feature("boms"))])
app.include_router(suppliers.router, dependencies=[Depends(require_feature("procurement"))])
app.include_router(purchase_orders.router, dependencies=[Depends(require_feature("procurement"))])
app.include_router(lots.router, dependencies=[Depends(require_feature("lots"))])
app.include_router(locations.router)
app.include_router(machines.router)
# Must be registered before inventory.router: /inventory/analytics is a
# static path that would otherwise be shadowed by inventory.router's
# /inventory/{item_id} (Starlette matches path shape before UUID validation).
app.include_router(inventory_analytics.router, dependencies=[Depends(require_feature("warehouse"))])
app.include_router(inventory.router, dependencies=[Depends(require_feature("warehouse"))])
app.include_router(forecasts.router, dependencies=[Depends(require_feature("planning"))])
app.include_router(forecasts.mrp_router, dependencies=[Depends(require_feature("planning"))])
app.include_router(datasets.router)
app.include_router(oee.router, dependencies=[Depends(require_feature("oee"))])
app.include_router(pace_tracking.router, dependencies=[Depends(require_feature("pace_tracking"))])
app.include_router(plant_summary.router, dependencies=[Depends(require_feature("plant_summary"))])
app.include_router(downtime.router, dependencies=[Depends(require_feature("downtime"))])
app.include_router(downtime_tracking.router, dependencies=[Depends(require_feature("downtime_tracking"))])
app.include_router(throughput.router, dependencies=[Depends(require_feature("throughput"))])
app.include_router(scrap.router, dependencies=[Depends(require_feature("scrap"))])
app.include_router(shifts.router, dependencies=[Depends(require_feature("shifts"))])
app.include_router(simulation.router, dependencies=[Depends(require_feature("simulation"))])
app.include_router(spc.router, dependencies=[Depends(require_feature("spc"))])
app.include_router(quality.router)
app.include_router(process_mining.router)
app.include_router(ai_insights.router)
app.include_router(reports.router, dependencies=[Depends(require_feature("reports"))])
app.include_router(routings.router)
app.include_router(bi.router)
app.include_router(workflows.router)
app.include_router(automl.router, dependencies=[Depends(require_feature("automl"))])
app.include_router(kpis.router, dependencies=[Depends(require_feature("kpis"))])
app.include_router(kpi_dashboards.router, dependencies=[Depends(require_feature("kpi_dashboards"))])
app.include_router(integrations.router)
app.include_router(realtime.router)
app.include_router(rca_cases.router, dependencies=[Depends(require_feature("rca_cases"))])
app.include_router(predictive.router, dependencies=[Depends(require_feature("predictive"))])
app.include_router(mfg_forecast.router, dependencies=[Depends(require_feature("mfg_forecast"))])
app.include_router(work_centers.router)
app.include_router(computer_vision.router, dependencies=[Depends(require_feature("computer_vision"))])
app.include_router(data_chat.router, dependencies=[Depends(require_feature("data_chat"))])
app.include_router(alerts.router)
app.include_router(cbm.router, dependencies=[Depends(require_feature("predictive"))])
```

- [ ] **Step 6: Expose `enabled_features` and `is_staff` on `/orgs/me`**

In `backend/app/api/routes/orgs.py`, change `OrgResponse`:

```python
class OrgResponse(BaseModel):
    id: str
    name: str
    role: str
    user_id: str
    adjust_approval_threshold: Decimal
```

to:

```python
class OrgResponse(BaseModel):
    id: str
    name: str
    role: str
    user_id: str
    adjust_approval_threshold: Decimal
    enabled_features: list[str]
    is_staff: bool
```

Then update both `OrgResponse(...)` constructions (in `get_my_org` and `update_my_org`) to add the
two new fields - in `get_my_org`, change:

```python
    return OrgResponse(
        id=str(org.id),
        name=org.name,
        role=current.role,
        user_id=str(current.id),
        adjust_approval_threshold=org.adjust_approval_threshold,
    )
```

to:

```python
    return OrgResponse(
        id=str(org.id),
        name=org.name,
        role=current.role,
        user_id=str(current.id),
        adjust_approval_threshold=org.adjust_approval_threshold,
        enabled_features=org.enabled_features,
        is_staff=current.user.is_staff,
    )
```

And the identical change to the `return OrgResponse(...)` at the end of `update_my_org`.

- [ ] **Step 7: Run the new tests to verify they pass**

Run (from `backend/`): `pytest tests/test_feature_gating.py -v`
Expected: PASS - 3 passed.

- [ ] **Step 8: Run the full backend suite to check for regressions**

Run (from `backend/`): `pytest -q`
Expected: this is the step most likely to surface breakage - every existing test that registers an
org and then calls a now-gated route (e.g. any BOM/OEE/downtime/etc. test) will start failing with
403s, since fresh orgs default to `enabled_features=[]`. **If you see widespread 403 failures here,
this is expected and must be fixed, not worked around**: the fix is a single addition to the
shared test fixture pattern, not touching each failing test file individually. Add a helper to
whichever conftest/shared fixture module those `_register_and_login()` copies commonly sit near
(check `backend/tests/conftest.py` first), or - if no shared conftest hook exists - grant the
`client` fixture's underlying test org every feature by default. Concretely: after confirming which
tests fail, the most likely correct fix is granting `PREMIUM_FEATURES` (from
`app.core.features`) to every org created during tests, either via a `conftest.py` fixture that
runs after each `_register_and_login()`-style helper, or by changing the test database setup to
default new orgs to full entitlements in the test environment only (never in production code).
Do not hand-edit `enabled_features` in dozens of individual test files - find the one shared
place and fix it there.

- [ ] **Step 9: Commit**

```bash
git add backend/app/core/features.py backend/app/api/deps.py backend/app/main.py backend/app/api/routes/orgs.py backend/tests/test_feature_gating.py
git commit -m "feat: add require_feature/require_staff and gate feature routers"
```

If Step 8 required a conftest/fixture fix, include those files in this commit too - it's part of
making this task's change land without breaking the suite, not a separate task.

---

### Task 3: Staff admin endpoints (`POST`/`GET`/`PATCH /admin/customers`)

**Files:**
- Create: `backend/app/api/routes/admin.py`
- Modify: `backend/app/main.py` (import + include `admin.router`)
- Test: `backend/tests/test_admin_customers.py`

**Interfaces:**
- Consumes: `require_staff` from `backend/app/api/deps.py` (Task 2). `UserCreate`, `UserManager`,
  `get_user_manager` from `backend/app/core/users.py` (pre-existing - same imports `auth.py`
  already uses for `/auth/register`).
- Produces: nothing consumed by a later backend task - Task 4/5/6 are frontend-only and just call
  these three endpoints by URL/shape.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_admin_customers.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.tenant import Org
from app.models.user import User

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"], reg.json()["user_id"]


async def _register_login_and_make_staff(client, email="staff@evowerk.example", org_name="EvoWerk Staff"):
    token, user_id = await _register_and_login(client, email=email, org_name=org_name)
    async with db_session.async_session_factory() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        user.is_staff = True
        await db.commit()
    return token


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_non_staff_cannot_create_customer(client):
    token, _user_id = await _register_and_login(client)
    res = await client.post(
        "/admin/customers", headers=_h(token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert res.status_code == 403


async def test_staff_creates_customer_with_features(client):
    staff_token = await _register_login_and_make_staff(client)

    res = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee", "downtime"]},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["org_name"] == "New Co"
    assert body["owner_email"] == "owner@newco.example"
    assert sorted(body["enabled_features"]) == ["downtime", "oee"]

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == body["org_id"]))).scalar_one()
        owner = (await db.execute(select(User).where(User.email == "owner@newco.example"))).scalar_one()
    assert sorted(org.enabled_features) == ["downtime", "oee"]
    assert owner.role == "owner"
    assert owner.org_id == org.id


async def test_staff_lists_and_edits_customer_features(client):
    staff_token = await _register_login_and_make_staff(client)
    create = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    org_id = create.json()["org_id"]

    listed = await client.get("/admin/customers", headers=_h(staff_token))
    assert listed.status_code == 200, listed.text
    assert any(c["org_id"] == org_id and c["enabled_features"] == ["oee"] for c in listed.json())

    updated = await client.patch(
        f"/admin/customers/{org_id}", headers=_h(staff_token),
        json={"enabled_features": ["oee", "automl"]},
    )
    assert updated.status_code == 200, updated.text
    assert sorted(updated.json()["enabled_features"]) == ["automl", "oee"]

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one()
    assert sorted(org.enabled_features) == ["automl", "oee"]


async def test_patch_unknown_customer_404s(client):
    staff_token = await _register_login_and_make_staff(client)
    res = await client.patch(
        "/admin/customers/00000000-0000-0000-0000-000000000000", headers=_h(staff_token),
        json={"enabled_features": ["oee"]},
    )
    assert res.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `backend/`): `pytest tests/test_admin_customers.py -v`
Expected: FAIL - `404 Not Found` on every request (no `/admin/*` routes exist yet).

- [ ] **Step 3: Create `admin.py`**

Create `backend/app/api/routes/admin.py`:

```python
from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_staff
from app.core.users import UserCreate, UserManager, get_user_manager
from app.db.session import get_db
from app.models.tenant import Org
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff)])


class CustomerCreateRequest(BaseModel):
    org_name: str
    owner_email: EmailStr
    enabled_features: list[str]


class CustomerResponse(BaseModel):
    org_id: str
    org_name: str
    owner_email: str
    enabled_features: list[str]


class CustomerFeaturesUpdate(BaseModel):
    enabled_features: list[str]


@router.post("/customers", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_manager: UserManager = Depends(get_user_manager),
    _current: CurrentUser = Depends(require_staff),
):
    # Same Org+Location+owner-role creation transaction /auth/register uses
    # (UserManager.create, backend/app/core/users.py) - just with a throwaway
    # password the customer never sees, since forgot_password() below sends
    # them a real "set your password" link instead.
    throwaway_password = secrets.token_urlsafe(24)
    created_user = await user_manager.create(
        UserCreate(email=body.owner_email, password=throwaway_password, org_name=body.org_name),
        safe=True,
        request=request,
    )

    org = (await db.execute(select(Org).where(Org.id == created_user.org_id))).scalar_one()
    org.enabled_features = body.enabled_features
    await db.commit()

    # Same flow /auth/forgot-password uses (on_after_forgot_password sends the
    # email via SES/logging - app/core/users.py, app/core/email.py).
    await user_manager.forgot_password(created_user, request)

    return CustomerResponse(
        org_id=str(org.id), org_name=org.name,
        owner_email=body.owner_email, enabled_features=org.enabled_features,
    )


@router.get("/customers", response_model=list[CustomerResponse])
async def list_customers(
    db: AsyncSession = Depends(get_db),
    _current: CurrentUser = Depends(require_staff),
):
    orgs = (await db.execute(select(Org))).scalars().all()
    out: list[CustomerResponse] = []
    for org in orgs:
        owner = (
            await db.execute(select(User).where(User.org_id == org.id, User.role == "owner"))
        ).scalar_one_or_none()
        out.append(
            CustomerResponse(
                org_id=str(org.id), org_name=org.name,
                owner_email=owner.email if owner else "", enabled_features=org.enabled_features,
            )
        )
    return out


@router.patch("/customers/{org_id}", response_model=CustomerResponse)
async def update_customer_features(
    org_id: uuid.UUID,
    body: CustomerFeaturesUpdate,
    db: AsyncSession = Depends(get_db),
    _current: CurrentUser = Depends(require_staff),
):
    # Not app.api.deps.get_or_404: that helper filters on model.org_id, which
    # Org itself doesn't have (Org is the tenant root, not a child row) - this
    # is a direct cross-org lookup by id, which is exactly what a staff-only
    # customer-management endpoint is for.
    org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    org.enabled_features = body.enabled_features
    await db.commit()

    owner = (
        await db.execute(select(User).where(User.org_id == org.id, User.role == "owner"))
    ).scalar_one_or_none()
    return CustomerResponse(
        org_id=str(org.id), org_name=org.name,
        owner_email=owner.email if owner else "", enabled_features=org.enabled_features,
    )
```

- [ ] **Step 4: Wire `admin.router` into `main.py`**

In `backend/app/main.py`, add `admin` to the existing `from app.api.routes import (...)` block -
alphabetically, directly after `ai_insights,`:

```python
from app.api.routes import (
    ai_insights,
    admin,
    alerts,
```

Then add its registration directly after `app.include_router(orgs.router)`:

```python
app.include_router(orgs.router)
app.include_router(admin.router)
```

(Its own `dependencies=[Depends(require_staff)]`, set on the `APIRouter` itself in Step 3, is what
actually gates every route under it - nothing extra needed at the `include_router()` call site.)

- [ ] **Step 5: Run the new tests to verify they pass**

Run (from `backend/`): `pytest tests/test_admin_customers.py -v`
Expected: PASS - 4 passed.

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run (from `backend/`): `pytest -q`
Expected: same pass count as after Task 2's Step 8, plus these 4 new tests - 0 new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/admin.py backend/app/main.py backend/tests/test_admin_customers.py
git commit -m "feat: add staff-only /admin/customers endpoints"
```

---

### Task 4: Frontend feature registry + nav filtering

**Files:**
- Create: `frontend/src/lib/features.ts`
- Modify: `frontend/src/lib/nav.ts` (add `featureKey` to `NavItem` and to gated entries)
- Modify: `frontend/src/components/AppShell.tsx` (filter `NAV_SECTIONS` by entitlement)
- Test: `backend/tests/test_feature_registry_sync.py` (cross-checks the two registries stay in sync
  - lives in `backend/tests` since it's the enforcement mechanism the design spec calls for, and
  this repo's Python test suite is what CI already runs on every change to either file)

**Interfaces:**
- Produces (consumed by Task 5, Task 6): `FEATURES` (same shape as the backend's, key ->
  `{label, tier}`), `CORE_FEATURES: string[]`, `PREMIUM_FEATURES: string[]` exported from
  `frontend/src/lib/features.ts`.

- [ ] **Step 1: Create the frontend registry**

Create `frontend/src/lib/features.ts`:

```ts
/** Mirrors backend/app/core/features.py - keep these two files' keys and tiers
 * identical. backend/tests/test_feature_registry_sync.py fails the build if
 * they drift; there is no automated codegen between the Python and TS files. */

export interface FeatureDef {
  label: string;
  tier: "core" | "premium";
}

export const FEATURES: Record<string, FeatureDef> = {
  boms: { label: "Bills of Materials", tier: "core" },
  procurement: { label: "Procurement", tier: "core" },
  lots: { label: "Lots", tier: "core" },
  warehouse: { label: "Warehouse", tier: "core" },
  planning: { label: "Planning", tier: "core" },
  plant_summary: { label: "Plant Summary", tier: "core" },
  oee: { label: "OEE", tier: "core" },
  pace_tracking: { label: "Pace Tracking", tier: "core" },
  downtime: { label: "Downtime", tier: "core" },
  downtime_tracking: { label: "Downtime Tracking", tier: "core" },
  throughput: { label: "Throughput", tier: "core" },
  scrap: { label: "Scrap & Quality", tier: "core" },
  shifts: { label: "Shifts", tier: "core" },
  spc: { label: "SPC", tier: "core" },
  kpis: { label: "KPIs", tier: "core" },
  kpi_dashboards: { label: "KPI Dashboards", tier: "core" },
  reports: { label: "Report Builder", tier: "core" },
  data_chat: { label: "Ask (AI data chat)", tier: "core" },
  automl: { label: "AutoML", tier: "premium" },
  predictive: { label: "Predictive Maintenance", tier: "premium" },
  simulation: { label: "Simulation", tier: "premium" },
  rca_cases: { label: "Root Cause Analysis", tier: "premium" },
  mfg_forecast: { label: "Manufacturing Forecast", tier: "premium" },
  computer_vision: { label: "Computer Vision", tier: "premium" },
};

export const CORE_FEATURES: string[] = Object.entries(FEATURES)
  .filter(([, v]) => v.tier === "core")
  .map(([k]) => k);

export const PREMIUM_FEATURES: string[] = Object.keys(FEATURES);
```

- [ ] **Step 2: Add `featureKey` to `NavItem` and tag gated entries**

In `frontend/src/lib/nav.ts`, change the `NavItem` interface:

```ts
export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  /** Synonyms/intent terms so the command palette matches more than the literal label (e.g. "inventory" -> Warehouse, "defect" -> Scrap & Quality). */
  keywords?: string[];
}
```

to:

```ts
export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  /** Synonyms/intent terms so the command palette matches more than the literal label (e.g. "inventory" -> Warehouse, "defect" -> Scrap & Quality). */
  keywords?: string[];
  /** Gates this item to orgs with this key in their enabled_features - see frontend/src/lib/features.ts. Absent = always visible (System pages, Datasets, SQL Editor, BI Dashboards). */
  featureKey?: string;
}
```

Then add `featureKey` to every gated entry. Replace the `"Operations"` section:

```ts
  {
    label: "Operations",
    items: [
      { to: "/boms", label: "Bills of Materials", icon: Layers, keywords: ["bom", "recipe", "components"] },
      { to: "/procurement", label: "Procurement", icon: ShoppingCart, keywords: ["suppliers", "purchase orders", "po", "vendors"] },
      { to: "/lots", label: "Lots", icon: Boxes, keywords: ["batch", "traceability"] },
      { to: "/warehouse", label: "Warehouse", icon: Warehouse, keywords: ["inventory", "stock", "on-hand", "on hand"] },
      { to: "/planning", label: "Planning", icon: CalendarRange, keywords: ["mrp", "forecast", "demand"] },
    ],
  },
```

with:

```ts
  {
    label: "Operations",
    items: [
      { to: "/boms", label: "Bills of Materials", icon: Layers, keywords: ["bom", "recipe", "components"], featureKey: "boms" },
      { to: "/procurement", label: "Procurement", icon: ShoppingCart, keywords: ["suppliers", "purchase orders", "po", "vendors"], featureKey: "procurement" },
      { to: "/lots", label: "Lots", icon: Boxes, keywords: ["batch", "traceability"], featureKey: "lots" },
      { to: "/warehouse", label: "Warehouse", icon: Warehouse, keywords: ["inventory", "stock", "on-hand", "on hand"], featureKey: "warehouse" },
      { to: "/planning", label: "Planning", icon: CalendarRange, keywords: ["mrp", "forecast", "demand"], featureKey: "planning" },
    ],
  },
```

Replace the `"Plant Analytics"` section:

```ts
  {
    label: "Plant Analytics",
    items: [
      { to: "/analytics/plant-summary", label: "Plant Summary", icon: Factory, keywords: ["overview", "rollup"] },
      { to: "/analytics/oee", label: "OEE", icon: Gauge, keywords: ["overall equipment effectiveness", "availability", "performance"] },
      { to: "/analytics/pace-tracking", label: "Pace Tracking", icon: Timer, keywords: ["manual entry", "tap", "operator"] },
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff, keywords: ["stoppage", "downtime analysis"] },
      { to: "/analytics/downtime-tracking", label: "Downtime Tracking", icon: AlertTriangle, keywords: ["live downtime", "andon"] },
      { to: "/analytics/throughput", label: "Throughput", icon: BarChart3, keywords: ["units per hour", "rate", "production rate"] },
      { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX, keywords: ["defect", "reject", "quality", "fpq", "first pass"] },
      { to: "/analytics/shifts", label: "Shifts", icon: Users, keywords: ["shift schedule"] },
      { to: "/analytics/spc", label: "SPC", icon: Activity, keywords: ["statistical process control", "control chart"] },
      { to: "/kpis", label: "KPIs", icon: Target, keywords: ["metrics", "targets"] },
      { to: "/kpis/dashboards", label: "KPI Dashboards", icon: Rows3, keywords: ["kpi wall", "tv mode"] },
    ],
  },
```

with:

```ts
  {
    label: "Plant Analytics",
    items: [
      { to: "/analytics/plant-summary", label: "Plant Summary", icon: Factory, keywords: ["overview", "rollup"], featureKey: "plant_summary" },
      { to: "/analytics/oee", label: "OEE", icon: Gauge, keywords: ["overall equipment effectiveness", "availability", "performance"], featureKey: "oee" },
      { to: "/analytics/pace-tracking", label: "Pace Tracking", icon: Timer, keywords: ["manual entry", "tap", "operator"], featureKey: "pace_tracking" },
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff, keywords: ["stoppage", "downtime analysis"], featureKey: "downtime" },
      { to: "/analytics/downtime-tracking", label: "Downtime Tracking", icon: AlertTriangle, keywords: ["live downtime", "andon"], featureKey: "downtime_tracking" },
      { to: "/analytics/throughput", label: "Throughput", icon: BarChart3, keywords: ["units per hour", "rate", "production rate"], featureKey: "throughput" },
      { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX, keywords: ["defect", "reject", "quality", "fpq", "first pass"], featureKey: "scrap" },
      { to: "/analytics/shifts", label: "Shifts", icon: Users, keywords: ["shift schedule"], featureKey: "shifts" },
      { to: "/analytics/spc", label: "SPC", icon: Activity, keywords: ["statistical process control", "control chart"], featureKey: "spc" },
      { to: "/kpis", label: "KPIs", icon: Target, keywords: ["metrics", "targets"], featureKey: "kpis" },
      { to: "/kpis/dashboards", label: "KPI Dashboards", icon: Rows3, keywords: ["kpi wall", "tv mode"], featureKey: "kpi_dashboards" },
    ],
  },
```

In the `"Data & Reports"` section, only `Report Builder` and `Ask` get a `featureKey` (Datasets,
SQL Editor, BI Dashboards stay always-on). Replace:

```ts
      { to: "/report-builder", label: "Report Builder", icon: FileStack, keywords: ["reports", "export", "pdf"] },
      { to: "/ask", label: "Ask", icon: Sparkles, keywords: ["chat", "natural language", "q&a", "assistant"] },
```

with:

```ts
      { to: "/report-builder", label: "Report Builder", icon: FileStack, keywords: ["reports", "export", "pdf"], featureKey: "reports" },
      { to: "/ask", label: "Ask", icon: Sparkles, keywords: ["chat", "natural language", "q&a", "assistant"], featureKey: "data_chat" },
```

Replace the `"Intelligence"` section:

```ts
  {
    label: "Intelligence",
    items: [
      { to: "/automl", label: "AutoML", icon: Brain, keywords: ["machine learning", "model training"] },
      { to: "/simulation", label: "Simulation", icon: FlaskConical, keywords: ["what-if", "scenario"] },
      { to: "/rca-cases", label: "Root Cause Analysis", icon: Search, keywords: ["rca", "5 why", "fishbone"] },
      { to: "/predictive", label: "Predictive Maintenance", icon: Wrench, keywords: ["failure prediction", "maintenance"] },
      { to: "/mfg-forecast", label: "Manufacturing Forecast", icon: TrendingUp, keywords: ["production forecast"] },
      { to: "/computer-vision", label: "Computer Vision", icon: Camera, keywords: ["image", "defect detection", "camera"] },
    ],
  },
```

with:

```ts
  {
    label: "Intelligence",
    items: [
      { to: "/automl", label: "AutoML", icon: Brain, keywords: ["machine learning", "model training"], featureKey: "automl" },
      { to: "/simulation", label: "Simulation", icon: FlaskConical, keywords: ["what-if", "scenario"], featureKey: "simulation" },
      { to: "/rca-cases", label: "Root Cause Analysis", icon: Search, keywords: ["rca", "5 why", "fishbone"], featureKey: "rca_cases" },
      { to: "/predictive", label: "Predictive Maintenance", icon: Wrench, keywords: ["failure prediction", "maintenance"], featureKey: "predictive" },
      { to: "/mfg-forecast", label: "Manufacturing Forecast", icon: TrendingUp, keywords: ["production forecast"], featureKey: "mfg_forecast" },
      { to: "/computer-vision", label: "Computer Vision", icon: Camera, keywords: ["image", "defect detection", "camera"], featureKey: "computer_vision" },
    ],
  },
```

- [ ] **Step 3: Filter the sidebar by entitlement in `AppShell.tsx`**

In `frontend/src/components/AppShell.tsx`, change the local `OrgResponse` interface:

```ts
interface OrgResponse {
  id: string;
  name: string;
}
```

to:

```ts
interface OrgResponse {
  id: string;
  name: string;
  enabled_features: string[];
  is_staff: boolean;
}
```

Then, directly after the `const match = findNavMatch(location.pathname);` line, add:

```ts
  const enabledFeatures = new Set(org.data?.enabled_features ?? []);
  const visibleSections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => !item.featureKey || enabledFeatures.has(item.featureKey)),
  })).filter((section) => section.items.length > 0);
```

Then change the two `NAV_SECTIONS.map((section, idx) => {` occurrences - there's one in the render
(the sidebar `<nav>` block) - to `visibleSections.map((section, idx) => {`. (`ALL_NAV_ITEMS` and
`findNavMatch`, used elsewhere in this file for the command palette and breadcrumb, are left
unfiltered on purpose - Task 6 revisits whether the command palette needs the same filter; out of
scope for this task, which only covers the sidebar.)

- [ ] **Step 4: Write the registry-sync test**

Create `backend/tests/test_feature_registry_sync.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

from app.core.features import FEATURES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TS_FILE = _REPO_ROOT / "frontend" / "src" / "lib" / "features.ts"

# Matches `key: { label: "...", tier: "core" | "premium" },` lines - good enough
# to catch drift between the two registries without a real TS parser.
_ENTRY_RE = re.compile(r'^\s*(\w+):\s*\{\s*label:\s*"[^"]*",\s*tier:\s*"(core|premium)"\s*\}', re.MULTILINE)


def test_frontend_registry_matches_backend_registry():
    ts_source = _TS_FILE.read_text(encoding="utf-8")
    ts_entries = {key: tier for key, tier in _ENTRY_RE.findall(ts_source)}
    py_entries = {key: tier for key, (_, tier) in FEATURES.items()}
    assert ts_entries == py_entries
```

- [ ] **Step 5: Run the test to verify it passes**

Run (from `backend/`): `pytest tests/test_feature_registry_sync.py -v`
Expected: PASS.

- [ ] **Step 6: Type-check the frontend**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/features.ts frontend/src/lib/nav.ts frontend/src/components/AppShell.tsx backend/tests/test_feature_registry_sync.py
git commit -m "feat: add frontend feature registry and gate the sidebar by entitlement"
```

---

### Task 5: Frontend route guards + remove public registration

**Files:**
- Modify: `frontend/src/App.tsx` (add `RequireFeature`/`RequireStaff`, wrap gated routes, remove
  `/register` route and its import)
- Modify: `frontend/src/pages/Login.tsx` (remove the "sign up" link, if one exists)
- Test: none (no component-render harness - manual verification, per repo convention)

**Interfaces:**
- Consumes: `NavItem.featureKey` pattern and the entitlement data shape from Task 4 (this task
  re-fetches `/orgs/me` itself, independent of `AppShell.tsx`'s query, since route guards must work
  before `AppShell` even mounts).
- Produces (consumed by Task 6): the `RequireStaff` component, reused by the new admin page's route.

- [ ] **Step 1: Add `RequireFeature` and `RequireStaff`, remove the register route**

In `frontend/src/App.tsx`, remove the `Register` import (line 8):

```ts
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { ForgotPassword } from "./pages/ForgotPassword";
```

becomes:

```ts
import { Login } from "./pages/Login";
import { ForgotPassword } from "./pages/ForgotPassword";
```

Add imports needed for the new guards, directly after the existing `useAuthHydrated, useAuthStore`
import:

```ts
import { useAuthHydrated, useAuthStore } from "./store/authStore";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
```

Add the two new guard components directly after the existing `RequireAuth` function:

```ts
interface EntitlementResponse {
  enabled_features: string[];
  is_staff: boolean;
}

function useEntitlements() {
  return useQuery<EntitlementResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load org");
      return res.json();
    },
  });
}

function RequireFeature({ feature, children }: { feature: string; children: JSX.Element }) {
  const org = useEntitlements();
  if (org.isLoading) return null;
  if (!org.data?.enabled_features.includes(feature)) {
    return (
      <div className="p-8">
        <EmptyState title="Not included in your plan" description="Contact EvoWerk to add this feature to your account." />
      </div>
    );
  }
  return children;
}

function RequireStaff({ children }: { children: JSX.Element }) {
  const org = useEntitlements();
  if (org.isLoading) return null;
  if (!org.data?.is_staff) return <Navigate to="/" replace />;
  return children;
}
```

(This duplicates the `["org-me"]` query key from `AppShell.tsx` - TanStack Query dedupes identical
concurrent queries by key automatically, so this is a second consumer of the same cached fetch, not
a second network request.)

- [ ] **Step 2: Remove the `/register` route and wrap gated routes**

Remove this line:

```ts
          <Route path="/register" element={<Register />} />
```

Then wrap each gated route's `element` in `RequireFeature`. Replace the routes block from
`<Route path="/boms" ...>` through `<Route path="/computer-vision" ...>` (i.e. every route this
plan's registry gates - System routes `/integrations`, `/realtime`, `/runs`, `/alerts`, and the
always-on `/items`, `/datasets*`, `/sql-editor`, `/bi*`, `/workflows*` stay exactly as they are)
with:

```tsx
            <Route path="/items" element={<Items />} />
            <Route path="/boms" element={<RequireFeature feature="boms"><Boms /></RequireFeature>} />
            <Route path="/procurement" element={<RequireFeature feature="procurement"><Procurement /></RequireFeature>} />
            <Route path="/lots" element={<RequireFeature feature="lots"><Lots /></RequireFeature>} />
            <Route path="/warehouse" element={<RequireFeature feature="warehouse"><Warehouse /></RequireFeature>} />
            <Route path="/planning" element={<RequireFeature feature="planning"><Planning /></RequireFeature>} />
            <Route path="/analytics" element={<Navigate to="/analytics/oee" replace />} />
            <Route path="/analytics/oee" element={<RequireFeature feature="oee"><Oee /></RequireFeature>} />
            <Route path="/analytics/pace-tracking" element={<RequireFeature feature="pace_tracking"><PaceTracking /></RequireFeature>} />
            <Route path="/analytics/plant-summary" element={<RequireFeature feature="plant_summary"><PlantSummary /></RequireFeature>} />
            <Route path="/analytics/downtime" element={<RequireFeature feature="downtime"><Downtime /></RequireFeature>} />
            <Route path="/analytics/downtime-tracking" element={<RequireFeature feature="downtime_tracking"><DowntimeTracking /></RequireFeature>} />
            <Route path="/analytics/throughput" element={<RequireFeature feature="throughput"><Throughput /></RequireFeature>} />
            <Route path="/analytics/scrap" element={<RequireFeature feature="scrap"><Scrap /></RequireFeature>} />
            <Route path="/analytics/shifts" element={<RequireFeature feature="shifts"><Shifts /></RequireFeature>} />
            <Route path="/analytics/spc" element={<RequireFeature feature="spc"><Spc /></RequireFeature>} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/datasets/:id" element={<DatasetDetail />} />
            <Route path="/sql-editor" element={<SqlEditor />} />
            <Route path="/simulation" element={<RequireFeature feature="simulation"><Simulation /></RequireFeature>} />
            <Route path="/ask" element={<RequireFeature feature="data_chat"><Ask /></RequireFeature>} />
            <Route path="/bi" element={<BiDashboards />} />
            <Route path="/bi/new" element={<ChartBuilder />} />
            <Route path="/bi/:id" element={<BiDashboardDetail />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/workflows/:id" element={<WorkflowBuilder />} />
            <Route path="/runs" element={<RunHistory />} />
            <Route path="/report-builder" element={<RequireFeature feature="reports"><ReportBuilder /></RequireFeature>} />
            <Route path="/automl" element={<RequireFeature feature="automl"><TemplatesHome /></RequireFeature>} />
            <Route path="/automl/runs/:id" element={<RequireFeature feature="automl"><AutoMLTemplateResultsView /></RequireFeature>} />
            <Route path="/automl/advanced" element={<RequireFeature feature="automl"><AutoML /></RequireFeature>} />
            <Route path="/automl/advanced/new" element={<RequireFeature feature="automl"><AutoMLRun /></RequireFeature>} />
            <Route path="/automl/advanced/:id" element={<RequireFeature feature="automl"><AutoMLRun /></RequireFeature>} />
            <Route path="/kpis" element={<RequireFeature feature="kpis"><Kpis /></RequireFeature>} />
            <Route path="/kpis/dashboards" element={<RequireFeature feature="kpi_dashboards"><KpiDashboards /></RequireFeature>} />
            <Route path="/integrations" element={<Integrations />} />
            <Route path="/realtime" element={<Realtime />} />
            <Route path="/rca-cases" element={<RequireFeature feature="rca_cases"><RcaCases /></RequireFeature>} />
            <Route path="/predictive" element={<RequireFeature feature="predictive"><Predictive /></RequireFeature>} />
            <Route path="/mfg-forecast" element={<RequireFeature feature="mfg_forecast"><MfgForecast /></RequireFeature>} />
            <Route path="/computer-vision" element={<RequireFeature feature="computer_vision"><ComputerVision /></RequireFeature>} />
            <Route path="/alerts" element={<Alerts />} />
```

- [ ] **Step 3: Remove the sign-up link from the Login page**

In `frontend/src/pages/Login.tsx`, remove the `footer` prop's `Link` to `/register`. Change:

```tsx
      footer={
        <>
          Need an account?{" "}
          <Link to="/register" className="font-medium text-primary hover:underline">
            Register
          </Link>
        </>
      }
```

to:

```tsx
      footer={null}
```

If `Link` is no longer used elsewhere in this file after this change, remove its import too (check
with `npx tsc -b --noEmit` in the next step - an unused import shows up as a build error, not a
silent leftover).

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: no new errors. If `Register` or any now-unused import triggers an "unused variable"
lint/type error, remove that import too.

- [ ] **Step 5: Manual smoke test in the browser**

1. Start both dev servers (`.claude/launch.json` configurations `backend-dev` and `frontend`).
2. Register a throwaway account directly via the backend (`POST /auth/register` - e.g. with
   `curl` or a REST client, since the UI no longer exposes signup), confirming it still works.
3. Log in as that account. Confirm `/register` no longer has a link from the Login page, and that
   navigating to `/register` directly redirects or 404s per React Router's default (no matching
   `<Route>`).
4. With `enabled_features: []` (the default), confirm the sidebar shows only always-on items
   (Dashboard, Items, Datasets, SQL Editor, BI Dashboards, Workflows, Run History, Report
   Builder... wait, Report Builder is gated - confirm it does NOT show), and navigating directly to
   `/analytics/oee` shows the "Not included in your plan" state instead of the OEE page.
5. Grant `oee` via direct DB update to `enabled_features`, refresh, confirm the OEE nav item now
   appears and `/analytics/oee` renders normally.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/pages/Login.tsx
git commit -m "feat: gate routes behind RequireFeature, remove public registration"
```

---

### Task 6: Staff admin page (`/admin/customers`)

**Files:**
- Create: `frontend/src/pages/admin/Customers.tsx`
- Modify: `frontend/src/App.tsx` (lazy-import + route, wrapped in `RequireStaff`)
- Modify: `frontend/src/lib/nav.ts` (add a staff-only nav entry)
- Modify: `frontend/src/components/AppShell.tsx` (only show that nav entry when `is_staff`)
- Test: none (no component-render harness - manual verification)

**Interfaces:**
- Consumes: `POST/GET/PATCH /admin/customers` (Task 3), `FEATURES`/`CORE_FEATURES`/
  `PREMIUM_FEATURES` (Task 4's `frontend/src/lib/features.ts`), `RequireStaff` (Task 5).

- [ ] **Step 1: Add the nav entry**

In `frontend/src/lib/nav.ts`, add a new section at the end of `NAV_SECTIONS` (after `"System"`):

```ts
  {
    label: "Staff",
    items: [
      { to: "/admin/customers", label: "Customers", icon: Users, keywords: ["onboard", "admin", "plan", "features"] },
    ],
  },
```

(`Users` is already imported for the Shifts nav icon - no new icon import needed.)

- [ ] **Step 2: Hide the Staff section for non-staff users**

In `frontend/src/components/AppShell.tsx`, change the `visibleSections` computation from Task 4:

```ts
  const enabledFeatures = new Set(org.data?.enabled_features ?? []);
  const visibleSections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => !item.featureKey || enabledFeatures.has(item.featureKey)),
  })).filter((section) => section.items.length > 0);
```

to:

```ts
  const enabledFeatures = new Set(org.data?.enabled_features ?? []);
  const visibleSections = NAV_SECTIONS
    .filter((section) => section.label !== "Staff" || org.data?.is_staff)
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => !item.featureKey || enabledFeatures.has(item.featureKey)),
    }))
    .filter((section) => section.items.length > 0);
```

- [ ] **Step 3: Build the admin page**

Create `frontend/src/pages/admin/Customers.tsx`:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { CORE_FEATURES, FEATURES, PREMIUM_FEATURES } from "@/lib/features";

interface Customer {
  org_id: string;
  org_name: string;
  owner_email: string;
  enabled_features: string[];
}

function FeatureCheckboxes({
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
      <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
        {Object.entries(FEATURES).map(([key, def]) => (
          <label key={key} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={selected.has(key)} onChange={() => toggle(key)} />
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
      const res = await apiFetch("/admin/customers", {
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
        <FeatureCheckboxes selected={features} onChange={setFeatures} />
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
      const res = await apiFetch(`/admin/customers/${customer.org_id}`, {
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
          <FeatureCheckboxes selected={features} onChange={setFeatures} />
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

export function Customers() {
  const qc = useQueryClient();
  const customers = useQuery<Customer[]>({
    queryKey: ["admin-customers"],
    queryFn: async () => {
      const res = await apiFetch("/admin/customers");
      if (!res.ok) throw new Error("Failed to load customers");
      return res.json();
    },
  });

  function refresh() {
    qc.invalidateQueries({ queryKey: ["admin-customers"] });
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Customers" description="Onboard new customers and manage existing customers' feature access." />
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
    </div>
  );
}
```

- [ ] **Step 4: Wire the route**

In `frontend/src/App.tsx`, add the lazy import directly after the `Alerts` lazy import:

```ts
const Alerts = lazyPage(() => import("./pages/Alerts"), "Alerts");
const Customers = lazyPage(() => import("./pages/admin/Customers"), "Customers");
```

Add the route directly after `/alerts`, still inside the `RequireAuth`-wrapped `<AppShell />`
route group:

```tsx
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/admin/customers" element={<RequireStaff><Customers /></RequireStaff>} />
```

- [ ] **Step 5: Type-check**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: no new errors.

- [ ] **Step 6: Manual smoke test in the browser**

1. Start both dev servers.
2. Log in as a non-staff user, confirm no "Staff" section appears in the sidebar and navigating
   directly to `/admin/customers` redirects to `/` instead of showing the page.
3. Flip `is_staff = true` directly in the DB for your test user, log in again (or refresh - the
   `["org-me"]` query should pick up the change on next fetch), confirm the "Staff" section and
   "Customers" link now appear.
4. On the Customers page, create a new customer with only `oee` checked. Confirm it appears in the
   "Existing customers" table with an "OEE" badge and nothing else.
5. Click "Edit features" on that row, check `automl` too, save. Confirm the badge list updates to
   show both OEE and AutoML without a page reload.
6. Log in as the new customer's owner account (its email/password reset link would have been
   logged server-side, since `ses_from_email` is unset in dev - check backend logs for the token,
   or query the DB for the user and use `/auth/reset-password` directly with a manually-crafted
   token if easier) and confirm their sidebar shows exactly OEE and AutoML, nothing else.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/admin/Customers.tsx frontend/src/App.tsx frontend/src/lib/nav.ts frontend/src/components/AppShell.tsx
git commit -m "feat: add staff-only Customers onboarding/management page"
```

---

## Self-Review Notes

- **Spec coverage:** Data model section → Task 1. Feature registry section → Task 2 (backend) and
  Task 4 (frontend). Backend enforcement section → Task 2. New admin endpoints section → Task 3.
  Frontend nav & route gating section → Task 4 (nav filtering) and Task 5 (route guards).
  `/admin/customers` page section → Task 6. Testing section → each task's own test step, plus
  Task 4's registry-sync test covering the spec's explicit call for one.
- **Deliberate deviation from the spec:** the spec's registry table listed `predictive` and `cbm`
  as two separate feature keys. Tracing the actual frontend routes (`App.tsx` has exactly one
  `/predictive` route, no separate `/cbm` route - `Predictive.tsx` calls both APIs from the same
  page) showed two keys would let an org's entitlement state disagree with itself (e.g. `cbm`
  granted but `predictive` not, showing half a broken page). This plan merges them into one
  `"predictive"` key gating both `predictive.router` and `cbm.router` - noted inline in Task 2.
  This is a plan-level correction, not an implementation shortcut - flagging per the requirement
  that deviations from the spec be explicit.
- **Test-suite regression risk:** Task 2 Step 8 is the plan's highest-risk step - gating ~24
  routers by default-empty entitlements will 403 every existing test that exercises a gated route
  through the standard `_register_and_login()` fixture pattern. The step includes explicit guidance
  (fix the shared fixture, not each failing file) rather than leaving this as a surprise; there was
  no way to know the exact fix location without running the suite, so it's written as a diagnostic
  step rather than a scripted one.
- **Placeholder scan:** none - every step has complete, runnable code. Task 5 Step 3 (Login page
  edit) was initially written as a descriptive find-and-remove instruction before `Login.tsx` had
  been read; fixed during self-review to a literal before/after diff against the file's actual
  content.
- **Type consistency:** `EntitlementResponse`/`OrgResponse` shapes match across Task 2 (backend
  `OrgResponse` gains `enabled_features`/`is_staff`), Task 4 (`AppShell.tsx`'s local `OrgResponse`
  gains the same two fields), and Task 5 (`App.tsx`'s `EntitlementResponse` - a separate, smaller
  local interface, since `App.tsx` only needs those two fields, not the full org shape). `Customer`
  (Task 6) matches `CustomerResponse` (Task 3) field-for-field. `FEATURES`/`CORE_FEATURES`/
  `PREMIUM_FEATURES` names and shapes match between `backend/app/core/features.py` (Task 2) and
  `frontend/src/lib/features.ts` (Task 4), enforced by Task 4's sync test.
