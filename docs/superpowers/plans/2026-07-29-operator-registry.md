# Operator Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the product an `Operator` entity — an org-scoped roster with fuzzy-duplicate detection, a merge operation, and a management UI — so operator-level features have a real identity to attribute data to instead of free text.

**Architecture:** Mirror the existing `Machine` registry pattern end to end: a model with `(org_id, normalized_name)` uniqueness, a `resolve_or_create_operator` sibling to `resolve_or_create_machine` in `registry_matching.py`, CRUD + merge routes under `/operators`, and an `Operators` tab in `Production.tsx` alongside Work Centers/Machines/Routings.

**Tech Stack:** FastAPI + SQLAlchemy async ORM + Alembic (backend), React + TanStack Query + Vitest render-tests (frontend).

## Global Constraints

- Spec source: `docs/superpowers/specs/2026-07-29-operator-registry-design.md`. Follow it exactly for model shape, resolution algorithm, and API surface.
- `FUZZY_MATCH_THRESHOLD = 0.8` (already defined in `backend/app/services/registry_matching.py`) is reused as-is — do not introduce a second threshold.
- **Scope note on merge/delete:** the spec's Merge and Testing sections describe repointing "every capture event and every `WorkOrder.assigned_operator_id`" and blocking delete with a 409 when either references the operator. Neither `CaptureEvent` nor `WorkOrder.assigned_operator_id` exist in this codebase yet — they belong to `2026-07-29-operator-identity-capture-design.md`, which this spec is an explicit prerequisite for and has not been built. Building repointing/blocking logic against tables that don't exist would be dead code. This plan implements merge (delete source, keep target) and delete (unconditional) against what exists today, with a one-line `ponytail:` comment at each spot marking where the identity-capture spec adds the reference checks when it lands its own tables. Do not invent placeholder tables to satisfy the letter of the spec.
- No new dependencies. No feature flag/gate on the `/operators` router — `Machine`'s router (`app.include_router(machines.router)`) has none either, and Operator is core registry infrastructure the same way.
- Follow existing code style: no docstrings beyond one-line rationale comments where non-obvious, `from __future__ import annotations` at the top of every backend file, org-scoped queries via `current.org_id`.

---

### Task 1: `Operator` model + migration

**Files:**
- Create: `backend/app/models/operator.py`
- Create: `backend/alembic/versions/b3f8a2d914c7_operators.py`
- Modify: `backend/alembic/env.py` (register the model with `Base.metadata`)
- Modify: `backend/tests/conftest.py` (register the model for the sqlite test DB)
- Test: `backend/tests/test_operator_model.py`

**Interfaces:**
- Produces: `app.models.operator.Operator` with fields `id, org_id, name, normalized_name, code, badge_id, status, needs_review, created_at`. Later tasks import this class.

- [ ] **Step 1: Write the model**

```python
# backend/app/models/operator.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Operator(Base):
    """An org-scoped roster of shop-floor operators, mirroring Machine.
    needs_review is set True when a row was created as a fuzzy-close match to
    an existing name (see registry_matching.resolve_or_create_operator) - a
    cleanup queue, not a blocker. code and badge_id are nullable because most
    rows are created by dataset auto-discovery or a QR tap, neither of which
    has an employee ID or badge to record."""

    __tablename__ = "operators"
    __table_args__ = (
        UniqueConstraint("org_id", "normalized_name", name="uq_operators_org_normalized_name"),
        # Partial: badge_id is nullable and most rows have none, so a plain
        # unique constraint would let only one badge-less row exist per org.
        Index(
            "uq_operators_org_badge",
            "org_id",
            "badge_id",
            unique=True,
            postgresql_where=text("badge_id IS NOT NULL"),
            sqlite_where=text("badge_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    badge_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 2: Register the model so `Base.metadata` (and therefore both real migrations and the in-memory sqlite test DB) knows about it**

In `backend/alembic/env.py`, add `operator` to the import list (alphabetical, between `maintenance_schedule` and `procurement`):

```python
from app.models import (  # noqa: F401 - registers models with Base.metadata
    alert,
    audit,
    bom,
    dataset,
    device_token,
    forecast,
    inventory,
    inventory_snapshot,
    integration,
    kpi,
    location,
    lot,
    machine,
    maintenance_schedule,
    operator,
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

In `backend/tests/conftest.py`, add `operator` to its import list (alphabetical, between `maintenance_schedule` and `pace_tracking`):

```python
from app.models import (  # noqa: F401 - register models
    alert,
    audit,
    automl_live_binding,
    automl_run,
    bom,
    dashboard,
    dataset,
    device_token,
    downtime_tracking,
    forecast,
    inventory,
    inventory_snapshot,
    integration,
    kpi,
    location,
    lot,
    machine,
    maintenance_schedule,
    operator,
    pace_tracking,
    procurement,
    rca_case,
    realtime,
    report_spec,
    routing,
    sales,
    sensor,
    shift_forecast,
    shift_schedule,
    simulation,
    staff,
    telemetry,
    tenant,
    user,
    work_center,
    work_order,
    workflow,
)
```

- [ ] **Step 3: Write the failing model tests**

```python
# backend/tests/test_operator_model.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.operator import Operator

pytestmark = pytest.mark.asyncio


async def test_duplicate_normalized_name_same_org_rejected(db_session):
    org_id = uuid.uuid4()
    db_session.add(Operator(org_id=org_id, name="J Kim", normalized_name="j kim"))
    await db_session.flush()
    db_session.add(Operator(org_id=org_id, name="j kim", normalized_name="j kim"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_same_normalized_name_different_org_allowed(db_session):
    db_session.add(Operator(org_id=uuid.uuid4(), name="J Kim", normalized_name="j kim"))
    db_session.add(Operator(org_id=uuid.uuid4(), name="J Kim", normalized_name="j kim"))
    await db_session.flush()  # no error


async def test_duplicate_badge_id_same_org_rejected(db_session):
    org_id = uuid.uuid4()
    db_session.add(Operator(org_id=org_id, name="A", normalized_name="a", badge_id="B1"))
    await db_session.flush()
    db_session.add(Operator(org_id=org_id, name="B", normalized_name="b", badge_id="B1"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_same_badge_id_different_org_allowed(db_session):
    db_session.add(Operator(org_id=uuid.uuid4(), name="A", normalized_name="a", badge_id="B1"))
    db_session.add(Operator(org_id=uuid.uuid4(), name="B", normalized_name="b", badge_id="B1"))
    await db_session.flush()  # no error


async def test_multiple_null_badge_ids_same_org_allowed(db_session):
    org_id = uuid.uuid4()
    db_session.add(Operator(org_id=org_id, name="A", normalized_name="a"))
    db_session.add(Operator(org_id=org_id, name="B", normalized_name="b"))
    await db_session.flush()  # no error - the partial index only guards non-null badge_id
```

- [ ] **Step 4: Run the tests to verify they fail (model doesn't exist yet / import error)**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_operator_model.py -v`
Expected: FAIL (or ERROR) — `ModuleNotFoundError: No module named 'app.models.operator'` if Step 1 wasn't picked up in this run order, otherwise these should already pass once Steps 1-2 land, since there's no separate "make it pass" step for a pure model. Confirm instead by running immediately after Steps 1-2 and checking all 5 pass.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_operator_model.py -v`
Expected: `5 passed`

- [ ] **Step 6: Write the migration**

```python
# backend/alembic/versions/b3f8a2d914c7_operators.py
"""operator registry

Revision ID: b3f8a2d914c7
Revises: 7d1a9e4c2b60
Create Date: 2026-07-29

"""
from alembic import op
import sqlalchemy as sa

revision = "b3f8a2d914c7"  # pragma: allowlist secret
down_revision = "7d1a9e4c2b60"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operators",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("badge_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operators_org_id", "operators", ["org_id"])
    op.create_unique_constraint(
        "uq_operators_org_normalized_name", "operators", ["org_id", "normalized_name"]
    )
    op.create_index(
        "uq_operators_org_badge",
        "operators",
        ["org_id", "badge_id"],
        unique=True,
        postgresql_where=sa.text("badge_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("operators")
```

- [ ] **Step 7: Verify the migration applies cleanly against the dev database**

Run: `cd backend && .venv/Scripts/python.exe -m alembic upgrade head`
Expected: no errors, ends on `b3f8a2d914c7`. Then confirm: `.venv/Scripts/python.exe -m alembic current` shows `b3f8a2d914c7 (head)`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/operator.py backend/alembic/versions/b3f8a2d914c7_operators.py backend/alembic/env.py backend/tests/conftest.py backend/tests/test_operator_model.py
git commit -m "feat: add Operator registry model and migration"
```

---

### Task 2: `resolve_or_create_operator`

**Files:**
- Modify: `backend/app/services/registry_matching.py`
- Test: `backend/tests/test_registry_matching.py`

**Interfaces:**
- Consumes: `Operator` (Task 1), `normalized_similarity` and `FUZZY_MATCH_THRESHOLD` (already in this file).
- Produces: `resolve_or_create_operator(db: AsyncSession, org_id: uuid.UUID, raw_name: str) -> Operator`. No later task in this plan calls it directly (the API layer doesn't need fuzzy resolution — see Task 3), but it's the hook future dataset-analysis and identity-capture work call into, per the spec.

- [ ] **Step 1: Write the failing tests**

```python
# add to backend/tests/test_registry_matching.py
import uuid

from app.models.operator import Operator
from app.services.registry_matching import resolve_or_create_operator


async def test_resolve_or_create_operator_exact_match_reuses_row(db_session):
    org_id = uuid.uuid4()
    first = await resolve_or_create_operator(db_session, org_id, "J Kim")
    await db_session.flush()

    second = await resolve_or_create_operator(db_session, org_id, "  j kim ")
    await db_session.flush()

    assert second.id == first.id
    count = (
        await db_session.execute(select(Operator).where(Operator.org_id == org_id))
    ).scalars().all()
    assert len(count) == 1


async def test_resolve_or_create_operator_near_duplicate_flags_needs_review(db_session):
    org_id = uuid.uuid4()
    await resolve_or_create_operator(db_session, org_id, "Kim")
    await db_session.flush()

    close = await resolve_or_create_operator(db_session, org_id, "Kin")
    await db_session.flush()

    assert close.needs_review is True


async def test_resolve_or_create_operator_unrelated_name_not_flagged(db_session):
    org_id = uuid.uuid4()
    await resolve_or_create_operator(db_session, org_id, "Kim")
    await db_session.flush()

    unrelated = await resolve_or_create_operator(db_session, org_id, "Alex Rodriguez")
    await db_session.flush()

    assert unrelated.needs_review is False


async def test_resolve_or_create_operator_scoped_per_org(db_session):
    name = "J Kim"
    a = await resolve_or_create_operator(db_session, uuid.uuid4(), name)
    b = await resolve_or_create_operator(db_session, uuid.uuid4(), name)
    await db_session.flush()

    assert a.id != b.id
```

Also add the missing import at the top of the test file:

```python
from sqlalchemy import select
```

(the file currently only imports `normalized_similarity`; the async db-session tests need `select` and `uuid` too — add `import uuid` alongside it.)

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_registry_matching.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_or_create_operator'`

- [ ] **Step 3: Implement `resolve_or_create_operator`**

Append to `backend/app/services/registry_matching.py`:

```python
from app.models.operator import Operator


async def resolve_or_create_operator(db: AsyncSession, org_id: uuid.UUID, raw_name: str) -> Operator:
    """Exact match on (org_id, normalized_name) - one indexed lookup, the
    overwhelmingly common path. On a miss, create a new row, flagged
    needs_review=True only when it's a fuzzy-close near-duplicate of an
    existing operator's name (a likely typo, worth a human glance)."""
    target = raw_name.strip()
    normalized = target.casefold()

    existing_match = (
        await db.execute(
            select(Operator).where(Operator.org_id == org_id, Operator.normalized_name == normalized)
        )
    ).scalar_one_or_none()
    if existing_match is not None:
        return existing_match

    others = (await db.execute(select(Operator).where(Operator.org_id == org_id))).scalars().all()
    needs_review = any(normalized_similarity(target, o.name) >= FUZZY_MATCH_THRESHOLD for o in others)

    operator = Operator(org_id=org_id, name=target, normalized_name=normalized, needs_review=needs_review)
    db.add(operator)
    await db.flush()
    return operator
```

Note: `registry_matching.py` already imports `select` and `AsyncSession` at the top for `resolve_or_create_machine` — no new imports needed there beyond the `Operator` model.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_registry_matching.py -v`
Expected: all tests pass (3 original + 4 new = 7)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/registry_matching.py backend/tests/test_registry_matching.py
git commit -m "feat: add resolve_or_create_operator alongside the machine resolver"
```

---

### Task 3: `/operators` API

**Files:**
- Create: `backend/app/api/routes/operators.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_operators.py`

**Interfaces:**
- Consumes: `Operator` (Task 1), `CurrentUser`/`get_current_user`/`require_role`/`get_or_404` (`app.api.deps`), `log_event` (`app.middleware.audit`).
- Produces: `GET/POST /operators`, `PATCH/DELETE /operators/{id}`, `POST /operators/{id}/merge`. Response shape `OperatorResponse`: `id, name, code, badge_id, status, needs_review` (all strings except `needs_review: bool`; `code`/`badge_id` nullable). This is what Task 4's frontend consumes.

- [ ] **Step 1: Write the failing API tests**

```python
# backend/tests/test_operators.py
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def test_create_operator(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/operators", json={"name": "J Kim", "code": "E-100"}, headers=headers)
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "J Kim"
    assert body["code"] == "E-100"
    assert body["badge_id"] is None
    assert body["status"] == "active"
    assert body["needs_review"] is False


async def test_create_duplicate_name_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "J Kim"}, headers=headers)
    dup = await client.post("/operators", json={"name": "j kim"}, headers=headers)
    assert dup.status_code == 409


async def test_create_duplicate_badge_id_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "A", "badge_id": "B1"}, headers=headers)
    dup = await client.post("/operators", json={"name": "B", "badge_id": "B1"}, headers=headers)
    assert dup.status_code == 409


async def test_list_needs_review_filter(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "Kim"}, headers=headers)
    close = (await client.post("/operators", json={"name": "Kin"}, headers=headers)).json()

    flagged = await client.get("/operators?needs_review=true", headers=headers)
    assert len(flagged.json()) == 1
    assert flagged.json()[0]["id"] == close["id"]


async def test_patch_operator_clears_needs_review(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "Kim"}, headers=headers)
    close = (await client.post("/operators", json={"name": "Kin"}, headers=headers)).json()
    assert close["needs_review"] is True

    patched = await client.patch(
        f"/operators/{close['id']}", json={"needs_review": False, "name": "Kin"}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["needs_review"] is False


async def test_merge_deletes_source_and_keeps_target(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    target = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    source = (await client.post("/operators", json={"name": "J. Kim"}, headers=headers)).json()

    merged = await client.post(
        f"/operators/{source['id']}/merge", json={"target_operator_id": target["id"]}, headers=headers
    )
    assert merged.status_code == 200
    assert merged.json()["id"] == target["id"]

    listed = await client.get("/operators", headers=headers)
    ids = {o["id"] for o in listed.json()}
    assert target["id"] in ids
    assert source["id"] not in ids


async def test_merge_into_self_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    res = await client.post(f"/operators/{op['id']}/merge", json={"target_operator_id": op["id"]}, headers=headers)
    assert res.status_code == 400


async def test_delete_operator(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    res = await client.delete(f"/operators/{op['id']}", headers=headers)
    assert res.status_code == 204
    listed = await client.get("/operators", headers=headers)
    assert listed.json() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_operators.py -v`
Expected: FAIL — `404 Not Found` on every request (router doesn't exist yet)

- [ ] **Step 3: Write the routes**

```python
# backend/app/api/routes/operators.py
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.operator import Operator

router = APIRouter(prefix="/operators", tags=["operators"])

_STATUSES = {"active", "inactive"}


class OperatorCreateRequest(BaseModel):
    name: str
    code: str | None = None
    badge_id: str | None = None


class OperatorUpdateRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    badge_id: str | None = None
    status: str | None = None
    needs_review: bool | None = None


class OperatorMergeRequest(BaseModel):
    target_operator_id: uuid.UUID


class OperatorResponse(BaseModel):
    id: str
    name: str
    code: str | None
    badge_id: str | None
    status: str
    needs_review: bool


def _to_response(o: Operator) -> OperatorResponse:
    return OperatorResponse(
        id=str(o.id), name=o.name, code=o.code, badge_id=o.badge_id,
        status=o.status, needs_review=o.needs_review,
    )


@router.get("", response_model=list[OperatorResponse])
async def list_operators(
    needs_review: bool | None = Query(None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Operator).where(Operator.org_id == current.org_id)
    if needs_review is not None:
        query = query.where(Operator.needs_review == needs_review)
    result = await db.execute(query.order_by(Operator.name))
    return [_to_response(o) for o in result.scalars().all()]


@router.post("", response_model=OperatorResponse, status_code=status.HTTP_201_CREATED)
async def create_operator(
    body: OperatorCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    target = body.name.strip()
    o = Operator(
        org_id=current.org_id, name=target, normalized_name=target.casefold(),
        code=body.code, badge_id=body.badge_id,
    )
    db.add(o)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "name or badge_id already exists in this org") from None

    await log_event(
        db, action="operator.created", org_id=current.org_id, actor_user_id=current.id,
        target=str(o.id), metadata={"name": o.name},
    )
    await db.commit()
    return _to_response(o)


@router.patch("/{operator_id}", response_model=OperatorResponse)
async def update_operator(
    operator_id: uuid.UUID,
    body: OperatorUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    o = await get_or_404(db, Operator, current.org_id, operator_id, "Operator not found")
    if body.status is not None and body.status not in _STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {sorted(_STATUSES)}")

    fields: dict[str, Any] = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        fields["normalized_name"] = fields["name"].strip().casefold()
        fields["name"] = fields["name"].strip()
    for key, value in fields.items():
        setattr(o, key, value)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "name or badge_id already exists in this org") from None

    await log_event(
        db, action="operator.updated", org_id=current.org_id, actor_user_id=current.id,
        target=str(o.id), metadata=fields,
    )
    await db.commit()
    await db.refresh(o)
    return _to_response(o)


@router.post("/{operator_id}/merge", response_model=OperatorResponse)
async def merge_operator(
    operator_id: uuid.UUID,
    body: OperatorMergeRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if operator_id == body.target_operator_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot merge an operator into itself")

    source = await get_or_404(db, Operator, current.org_id, operator_id, "Operator not found")
    target = await get_or_404(db, Operator, current.org_id, body.target_operator_id, "Target operator not found")

    # ponytail: no CaptureEvent/WorkOrder.assigned_operator_id exist yet to
    # repoint - identity-capture spec adds that repointing here once those
    # tables land.
    await db.delete(source)
    await log_event(
        db, action="operator.merged", org_id=current.org_id, actor_user_id=current.id,
        target=str(target.id), metadata={"source_id": str(source.id)},
    )
    await db.commit()
    await db.refresh(target)
    return _to_response(target)


@router.delete("/{operator_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_operator(
    operator_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    o = await get_or_404(db, Operator, current.org_id, operator_id, "Operator not found")
    # ponytail: no CaptureEvent/WorkOrder.assigned_operator_id exist yet to
    # block on - identity-capture spec adds the 409 guard here once those
    # tables land.
    await db.delete(o)
    await log_event(
        db, action="operator.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(o.id),
    )
    await db.commit()
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, add the import near `machines` (alphabetical in the existing import block) and the include_router call right after machines':

```python
    machines,
    operators,
```

```python
app.include_router(machines.router)
app.include_router(operators.router)
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_operators.py -v`
Expected: `9 passed`

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass, no new failures

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/operators.py backend/app/main.py backend/tests/test_operators.py
git commit -m "feat: add /operators CRUD, merge, and delete routes"
```

---

### Task 4: Operators tab in Production

**Files:**
- Modify: `frontend/src/pages/Production.tsx`
- Modify: `frontend/src/pages/Production.render.test.tsx`

**Interfaces:**
- Consumes: `GET/POST /operators`, `PATCH /operators/{id}`, `POST /operators/{id}/merge` (Task 3). `OperatorResponse` shape: `{ id, name, code, badge_id, status, needs_review }`.

- [ ] **Step 1: Write the failing render tests**

Add to `frontend/src/pages/Production.render.test.tsx`, inside the existing `describe("Production (setup tabs)", ...)` block (and extend the tab-loop list):

```typescript
// change the existing loop line to:
    for (const tab of ["work-centers", "machines", "routings", "operators"]) {
```

Then add these `it` blocks at the end of the same `describe`:

```typescript
  it("operators tab shows the create form and lists rows", () => {
    const html = renderSetup("operators", (qc) =>
      qc.setQueryData(
        ["operators"],
        [{ id: "op-1", name: "J Kim", code: "E-100", badge_id: null, status: "active", needs_review: false }],
      ),
    );
    expect(html).toContain("Add an operator");
    expect(html).toContain("J Kim");
    expect(html).toContain("E-100");
    expect(html).toContain("Active");
  });

  it("operators tab shows the empty state when there are none", () => {
    const html = renderSetup("operators", (qc) => qc.setQueryData(["operators"], []));
    expect(html).toContain("No operators yet");
  });

  it("operators tab surfaces a needs-review row with a merge picker", () => {
    const html = renderSetup("operators", (qc) =>
      qc.setQueryData(
        ["operators"],
        [
          { id: "op-1", name: "Kim", code: null, badge_id: null, status: "active", needs_review: false },
          { id: "op-2", name: "Kin", code: null, badge_id: null, status: "active", needs_review: true },
        ],
      ),
    );
    expect(html).toContain("Needs review");
    expect(html).toContain("Merge into");
    expect(html).toContain("Keep both");
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm run test -- Production.render`
Expected: FAIL — `tab === "operators"` renders nothing, "Add an operator" / "Operators" not found

- [ ] **Step 3: Add the `OperatorResponse` interface and `Users` icon import**

In `frontend/src/pages/Production.tsx`, update the lucide-react import (line 3) to add `Users`:

```typescript
import { ChevronDown, ChevronRight, Cog, Route as RouteIcon, Users, Wrench } from "lucide-react";
```

Add the response interface near the other `*Response` interfaces (after `MachineResponse`):

```typescript
interface OperatorResponse {
  id: string;
  name: string;
  code: string | null;
  badge_id: string | null;
  status: string;
  needs_review: boolean;
}
```

- [ ] **Step 4: Add the `OperatorRow` component (mirrors `RoutingRow`'s pattern of a per-row component so each row + its conditional banner share one key)**

Insert after `MachinesPanel` (after line 415, before the `RoutingRow` comment):

```typescript
function OperatorRow({
  operator,
  others,
  onError,
}: {
  operator: OperatorResponse;
  others: OperatorResponse[];
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [mergeTarget, setMergeTarget] = useState("");

  const merge = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/operators/${operator.id}/merge`, {
        method: "POST",
        body: JSON.stringify({ target_operator_id: mergeTarget }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to merge operators");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operators"] }),
    onError: (e: Error) => onError(e.message),
  });

  const keepBoth = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/operators/${operator.id}`, {
        method: "PATCH",
        body: JSON.stringify({ needs_review: false }),
      });
      if (!res.ok) throw new Error("Failed to update operator");
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operators"] }),
    onError: (e: Error) => onError(e.message),
  });

  return (
    <>
      <TR>
        <TD className="font-medium">{operator.name}</TD>
        <TD className="text-muted-foreground">{operator.code ?? "-"}</TD>
        <TD className="text-muted-foreground">{operator.badge_id ? "Assigned" : "-"}</TD>
        <TD>
          <Badge variant={statusVariant(operator.status)}>{humanize(operator.status)}</Badge>
        </TD>
        <TD>{operator.needs_review ? <Badge variant="warning">Needs review</Badge> : ""}</TD>
      </TR>
      {operator.needs_review && (
        <TR>
          <TD colSpan={5}>
            <div className="flex flex-wrap items-center gap-3 rounded-md bg-amber-50 p-3 text-sm dark:bg-amber-950">
              <span>Possible duplicate of an existing operator.</span>
              <Select value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)}>
                <option value="" disabled>
                  Merge into...
                </option>
                {others.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </Select>
              <Button size="sm" disabled={!mergeTarget} loading={merge.isPending} onClick={() => merge.mutate()}>
                Merge
              </Button>
              <Button size="sm" variant="secondary" loading={keepBoth.isPending} onClick={() => keepBoth.mutate()}>
                Keep both
              </Button>
            </div>
          </TD>
        </TR>
      )}
    </>
  );
}
```

- [ ] **Step 5: Add `OperatorsPanel`**

Insert directly after the `OperatorRow` component (still before the `RoutingRow` comment):

```typescript
function OperatorsPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [badgeId, setBadgeId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const operators = useQuery<OperatorResponse[]>({
    queryKey: ["operators"],
    queryFn: async () => {
      const res = await apiFetch("/operators");
      if (!res.ok) throw new Error("Failed to load operators");
      return res.json();
    },
  });

  const createOperator = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/operators", {
        method: "POST",
        body: JSON.stringify({ name, code: code.trim() || null, badge_id: badgeId.trim() || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create operator");
      }
      return res.json();
    },
    onSuccess: () => {
      setName("");
      setCode("");
      setBadgeId("");
      queryClient.invalidateQueries({ queryKey: ["operators"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createOperator.mutate();
  }

  const sorted = [...(operators.data ?? [])].sort((a, b) => Number(b.needs_review) - Number(a.needs_review));

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add an operator</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Name" htmlFor="operator-name">
              <Input id="operator-name" value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <Field label="Code" htmlFor="operator-code" hint="Optional">
              <Input id="operator-code" value={code} onChange={(e) => setCode(e.target.value)} />
            </Field>
            <Field label="Badge ID" htmlFor="operator-badge" hint="Optional">
              <Input id="operator-badge" value={badgeId} onChange={(e) => setBadgeId(e.target.value)} />
            </Field>
            <div className="flex items-end">
              <Button type="submit" className="w-full" loading={createOperator.isPending}>
                Add operator
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {operators.data && operators.data.length === 0 ? (
        <EmptyState
          icon={<Users />}
          title="No operators yet"
          description="The roster people are attributed against on production data."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Code</TH>
                <TH>Badge</TH>
                <TH>Status</TH>
                <TH>Review</TH>
              </TR>
            </THead>
            <TBody>
              {sorted.map((o) => (
                <OperatorRow
                  key={o.id}
                  operator={o}
                  others={sorted.filter((other) => other.id !== o.id)}
                  onError={setError}
                />
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Wire up the tab**

Update `SETUP_TABS` (around line 719):

```typescript
const SETUP_TABS = [
  { value: "work-centers", label: "Work Centers" },
  { value: "machines", label: "Machines" },
  { value: "operators", label: "Operators" },
  { value: "routings", label: "Routings" },
] as const;
```

Add the render branch in `Production` (around line 757, right after the machines branch):

```typescript
        {tab === "machines" && <MachinesPanel />}
        {tab === "operators" && <OperatorsPanel />}
        {tab === "routings" && <RoutingsPanel />}
```

- [ ] **Step 7: Run to verify pass**

Run: `cd frontend && npm run test -- Production.render`
Expected: all tests pass

- [ ] **Step 8: Run the full frontend test suite to check for regressions**

Run: `cd frontend && npm run test`
Expected: all tests pass

- [ ] **Step 9: Manually verify in the browser**

Start the dev servers (backend + frontend), open Production, click the Operators tab, add an operator, add a near-duplicate name to confirm the "Needs review" banner and merge picker appear, and confirm Merge/Keep both both clear the row correctly. Take a screenshot as proof.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/pages/Production.tsx frontend/src/pages/Production.render.test.tsx
git commit -m "feat: add Operators tab to Production with merge/keep-both cleanup UI"
```

---

## Self-Review

**Spec coverage:**
- Model fields, uniqueness constraints, `needs_review` semantics → Task 1. ✓
- Resolution algorithm (indexed lookup, fuzzy-flag on miss) → Task 2. ✓
- API surface (`GET/POST /operators`, `PATCH/DELETE /operators/{id}`, `POST /operators/{id}/merge`) → Task 3. ✓
- Migration creating `operators` with `badge_id` from day one → Task 1, Step 6. ✓
- Frontend Operators tab, needs-review-first sort, Merge/Keep both banner → Task 4. ✓
- "Dataset analysis auto-discovers" and "Live capture must resolve to the roster" (Population section) → explicitly out of this plan's scope; that's dataset-analysis and identity-capture spec work respectively, not this registry. `resolve_or_create_operator` is the hook both will call, and it's built and tested in Task 2.
- Merge/delete repointing capture events / `WorkOrder.assigned_operator_id` → deliberately deferred per the Global Constraints scope note (those tables don't exist yet); marked with `ponytail:` comments at the exact spots the identity-capture spec will extend.

**Placeholder scan:** no TBD/TODO, every step has runnable code and exact commands.

**Type consistency:** `OperatorResponse` fields (`id, name, code, badge_id, status, needs_review`) match across Task 3's Pydantic model and Task 4's TypeScript interface. `resolve_or_create_operator`'s signature in Task 2 matches its only current test callers.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-29-operator-registry.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
