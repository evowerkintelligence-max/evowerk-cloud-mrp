# Live Downtime Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live multi-machine downtime board (mark a machine down with a reason + shift, mark it back up) that continuously re-materializes closed incidents into one auto-updating `Dataset` per org, so the existing Downtime and Shifts analytics pages work on live-captured data with zero changes to either page.

**Architecture:** Two new tables (`DowntimeIncident`, `DowntimeLiveDataset`) plus a service/route pair mirroring the existing `pace_tracking` module. Every incident close rebuilds a CSV of all closed incidents for the org and atomically overwrites (or, on the very first close, creates) that org's one live `Dataset` row via the same `datasets.create_dataset`/new `overwrite_dataset_file` path a CSV upload uses.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + DuckDB (backend), React + TanStack Query + Tailwind (frontend). Same libraries already used by `pace_tracking` and `downtime` — no new dependencies.

## Global Constraints

- Every new/modified backend model, service function, and route must follow the exact patterns in `backend/app/models/pace_tracking.py`, `backend/app/services/pace_tracking.py`, and `backend/app/api/routes/pace_tracking.py` — org-scoped queries, `get_or_404` for single-row lookups, `CurrentUser`/`get_current_user` for auth.
- `pragma: allowlist secret` must stay on any literal that looks like a secret/hash in test files (matches existing test files' `PASSWORD = "..."  # pragma: allowlist secret`).
- No new third-party dependencies.
- Frontend has no automated test runner — frontend tasks end with `tsc -b` typecheck + a manual in-browser verification pass, not automated tests (matches the precedent in `docs/superpowers/specs/2026-07-15-pace-tracking-design.md`).

---

### Task 1: Data model + migration

**Files:**
- Create: `backend/app/models/downtime_tracking.py`
- Create: `backend/alembic/versions/a1c8e4f92d67_downtime_tracking.py`
- Modify: `backend/tests/conftest.py` (register the new model module so `Base.metadata.create_all` creates its tables in the in-memory test DB)
- Test: `backend/tests/test_downtime_tracking.py` (new file)

**Interfaces:**
- Consumes: `app.db.base.Base` (existing declarative base).
- Produces:
  - `DowntimeIncident(id: uuid.UUID, org_id: uuid.UUID, started_by: uuid.UUID, machine: str, reason: str, shift: str, started_at: datetime, ended_at: datetime | None)`
  - `DowntimeLiveDataset(org_id: uuid.UUID [pk], dataset_id: uuid.UUID)`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_downtime_tracking.py`:

```python
# backend/tests/test_downtime_tracking.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register(client, email: str = "dt@example.com", org_name: str = "DtOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    body = reg.json()
    return uuid.UUID(str(body["org_id"])), uuid.UUID(str(body["user_id"]))


async def test_downtime_incident_model_roundtrip(client, test_engine):
    from app.models.downtime_tracking import DowntimeIncident

    org_id, user_id = await _register(client)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        incident = DowntimeIncident(
            org_id=org_id, started_by=user_id, machine="Line 1", reason="breakdown", shift="A"
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        assert incident.id is not None
        assert incident.started_at is not None
        assert incident.ended_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_downtime_tracking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.downtime_tracking'`

- [ ] **Step 3: Create the model file**

Create `backend/app/models/downtime_tracking.py`:

```python
# backend/app/models/downtime_tracking.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DowntimeIncident(Base):
    """One machine-down-to-machine-up event, reported live from the downtime
    board. Closing an incident (setting ended_at) re-materializes every
    closed incident for the org into that org's DowntimeLiveDataset - see
    app.services.downtime_tracking.end_incident - so the existing Downtime
    and Shifts analytics pages work on live-captured data with zero changes.
    """

    __tablename__ = "downtime_incidents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    # breakdown | changeover | material_shortage | planned_maintenance | quality_hold | other
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    shift: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DowntimeLiveDataset(Base):
    """One row per org - points at the perpetually-updated Dataset that
    end_incident() keeps overwriting with every closed incident to date."""

    __tablename__ = "downtime_live_datasets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
```

- [ ] **Step 4: Register the model module in conftest.py so its tables get created**

In `backend/tests/conftest.py`, the import block (currently):

```python
from app.models import (  # noqa: F401 - register models
    alert,
    audit,
    automl_run,
    bom,
    dashboard,
    dataset,
    forecast,
```

Change the `dataset,` / `forecast,` lines to insert the new module alphabetically:

```python
from app.models import (  # noqa: F401 - register models
    alert,
    audit,
    automl_run,
    bom,
    dashboard,
    dataset,
    downtime_tracking,
    forecast,
```

- [ ] **Step 5: Run test to verify it fails again (migration file still missing, but that's OK - tests use metadata.create_all, not alembic)**

Run: `cd backend && python -m pytest tests/test_downtime_tracking.py -v`
Expected: PASS (conftest's `Base.metadata.create_all` already creates the tables from the model definitions; the Alembic migration below is for the real dev/prod database, not exercised by pytest).

- [ ] **Step 6: Create the Alembic migration for the real database**

Create `backend/alembic/versions/a1c8e4f92d67_downtime_tracking.py`:

```python
# backend/alembic/versions/a1c8e4f92d67_downtime_tracking.py
"""downtime tracking incidents + live dataset pointer

Revision ID: a1c8e4f92d67
Revises: d4f7a1c92b56
Create Date: 2026-07-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c8e4f92d67"  # pragma: allowlist secret
down_revision = "d4f7a1c92b56"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "downtime_incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("started_by", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("shift", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["started_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_downtime_incidents_org_id", "downtime_incidents", ["org_id"])

    op.create_table(
        "downtime_live_datasets",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("org_id"),
    )


def downgrade() -> None:
    op.drop_table("downtime_live_datasets")
    op.drop_index("ix_downtime_incidents_org_id", table_name="downtime_incidents")
    op.drop_table("downtime_incidents")
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/downtime_tracking.py backend/alembic/versions/a1c8e4f92d67_downtime_tracking.py backend/tests/conftest.py backend/tests/test_downtime_tracking.py
git commit -m "feat: add downtime-tracking data model and migration"
```

---

### Task 2: Atomic dataset overwrite helper

**Files:**
- Modify: `backend/app/services/storage.py`
- Modify: `backend/app/services/datasets.py`
- Test: `backend/tests/test_dataset_overwrite.py` (new file)

**Interfaces:**
- Consumes: existing `app.models.dataset.Dataset`, existing `app.services.datasets._sniff(path: Path) -> tuple[list[dict], int]`, existing `app.services.storage.dataset_path(org_id, dataset_id, ext) -> Path`.
- Produces:
  - `app.services.storage.replace_dataset_file(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str, data: bytes) -> Path`
  - `app.services.datasets.overwrite_dataset_file(db: AsyncSession, dataset: Dataset, *, data: bytes) -> Dataset` (async)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_dataset_overwrite.py`:

```python
# backend/tests/test_dataset_overwrite.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import datasets as ds_service

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def test_overwrite_dataset_file_updates_content_and_keeps_id(client, test_engine):
    reg = await client.post(
        "/auth/register",
        json={"org_name": "OverwriteOrg", "email": "overwrite@example.com", "password": PASSWORD},
    )
    assert reg.status_code == 201
    body = reg.json()
    org_id = uuid.UUID(str(body["org_id"]))
    user_id = uuid.UUID(str(body["user_id"]))

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = await ds_service.create_dataset(
            db, org_id=org_id, created_by=user_id, name="Original",
            filename="original.csv", data=b"a,b\n1,2\n",
        )
        await db.commit()
        original_id = dataset.id
        assert dataset.row_count == 1

        new_bytes = b"a,b\n1,2\n3,4\n5,6\n"
        await ds_service.overwrite_dataset_file(db, dataset, data=new_bytes)
        await db.commit()

        assert dataset.id == original_id
        assert dataset.row_count == 3
        assert dataset.size_bytes == len(new_bytes)

        con = ds_service.open_con()
        try:
            rel = ds_service.dataset_relation(dataset)
            count = con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]  # nosec B608
        finally:
            con.close()
        assert count == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_dataset_overwrite.py -v`
Expected: FAIL with `AttributeError: module 'app.services.datasets' has no attribute 'overwrite_dataset_file'`

- [ ] **Step 3: Add the atomic file-replace function to storage.py**

In `backend/app/services/storage.py`, add `import os` to the imports and add a new function after `save_dataset_file`:

```python
"""Dataset file storage.

Local-filesystem implementation. Files live at
``{data_dir}/datasets/{org_id}/{dataset_id}.{ext}`` - the path is derived
entirely from our own UUIDs, so a client-supplied filename can never influence
where a file lands (traversal-safe). Swapping in S3/object storage later means
replacing the bodies of these five functions, nothing above them.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.core.config import get_settings


def _root() -> Path:
    p = Path(get_settings().data_dir)
    return (p if p.is_absolute() else (Path.cwd() / p)).resolve()


def _org_dir(org_id: uuid.UUID) -> Path:
    d = _root() / "datasets" / str(org_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def dataset_path(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str) -> Path:
    return _org_dir(org_id) / f"{dataset_id}.{ext.lstrip('.')}"


def save_dataset_file(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str, data: bytes) -> Path:
    path = dataset_path(org_id, dataset_id, ext)
    path.write_bytes(data)
    return path


def replace_dataset_file(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str, data: bytes) -> Path:
    """Rewrite an existing dataset file atomically (temp file + os.replace).

    Unlike save_dataset_file (used exactly once, before the Dataset row
    exists and before any reader could be mid-request against it), this is
    called repeatedly against a file that may be read concurrently by the
    analytics engines - a plain write_bytes could hand a reader a
    half-written CSV. os.replace is atomic on the same filesystem on both
    POSIX and Windows.
    """
    path = dataset_path(org_id, dataset_id, ext)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def delete_dataset_file(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str) -> None:
    try:
        dataset_path(org_id, dataset_id, ext).unlink()
    except FileNotFoundError:
        pass
```

- [ ] **Step 4: Add overwrite_dataset_file to datasets.py**

In `backend/app/services/datasets.py`, add this function after `create_dataset` (before `list_datasets`):

```python
async def overwrite_dataset_file(db: AsyncSession, dataset: Dataset, *, data: bytes) -> Dataset:
    """Rewrite an existing, already-created dataset's file in place and
    re-sniff its schema/row count. Used by live-capture features (the
    downtime-tracking board) whose Dataset keeps growing instead of being
    created once - see storage.replace_dataset_file for the atomicity note."""
    storage.replace_dataset_file(dataset.org_id, dataset.id, dataset.storage_ext, data)
    columns, row_count = _sniff(storage.dataset_path(dataset.org_id, dataset.id, dataset.storage_ext))
    dataset.columns = columns
    dataset.row_count = row_count
    dataset.size_bytes = len(data)
    await db.flush()
    return dataset
```

- [ ] **Step 5: Update the Dataset model's docstring to note the exception**

In `backend/app/models/dataset.py`, change:

```python
    """A tenant-scoped uploaded tabular dataset (production/machine data) that the
    analytics engines run over. The file itself lives on the storage backend
    (see app/services/storage.py); this row is the metadata + sniffed schema.

    Datasets are read-only after upload — analytics compute is stateless over
    them, so there is no mutable state here beyond deletion.
    """
```

to:

```python
    """A tenant-scoped uploaded tabular dataset (production/machine data) that the
    analytics engines run over. The file itself lives on the storage backend
    (see app/services/storage.py); this row is the metadata + sniffed schema.

    Datasets are read-only after upload for every ingestion path except one:
    app.services.downtime_tracking's live board repeatedly rewrites its one
    per-org Dataset via app.services.datasets.overwrite_dataset_file as new
    incidents close, keeping the same id. Every other feature still treats
    a Dataset as immutable-after-create.
    """
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_dataset_overwrite.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/storage.py backend/app/services/datasets.py backend/app/models/dataset.py backend/tests/test_dataset_overwrite.py
git commit -m "feat: add atomic dataset-file overwrite for live-updating datasets"
```

---

### Task 3: Downtime-tracking service + API routes

**Files:**
- Create: `backend/app/services/downtime_tracking.py`
- Create: `backend/app/api/routes/downtime_tracking.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_downtime_tracking.py` (append to the file from Task 1)

**Interfaces:**
- Consumes: Task 1's `DowntimeIncident`/`DowntimeLiveDataset`; Task 2's `datasets.overwrite_dataset_file(db, dataset, *, data: bytes) -> Dataset`; existing `datasets.create_dataset(db, *, org_id, created_by, name, filename, data) -> Dataset`; existing `datasets.resolve_dataset(db, org_id, dataset_id: str) -> Dataset`; existing `app.api.deps.get_or_404`, `CurrentUser`, `get_current_user`; existing `app.middleware.audit.log_event`.
- Produces (in `app.services.downtime_tracking`):
  - `start_incident(db, *, org_id, started_by, machine, reason, shift) -> DowntimeIncident`
  - `list_open(db, org_id) -> list[DowntimeIncident]`
  - `list_machines(db, org_id) -> list[str]`
  - `list_shifts(db, org_id) -> list[str]`
  - `end_incident(db, incident, *, org_id, created_by) -> uuid.UUID` (returns the live dataset id)
  - `discard_incident(db, incident) -> None`
  - `get_live_dataset_id(db, org_id) -> uuid.UUID | None`
  - Router `app.api.routes.downtime_tracking.router` at prefix `/downtime-tracking`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_downtime_tracking.py` (after the existing `test_downtime_incident_model_roundtrip`):

```python
async def _register_and_login(client, email: str, org_name: str = "DtOrg") -> str:
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def test_start_incident_then_list_open(client):
    token = await _register_and_login(client, "dtstart@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["machine"] == "Line 1"
    assert body["reason"] == "breakdown"
    assert body["shift"] == "A"
    assert body["ended_at"] is None

    listed = await client.get("/downtime-tracking/incidents?open=true", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["id"]


async def test_double_start_same_machine_rejected(client):
    token = await _register_and_login(client, "dtdouble@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    res = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "other", "shift": "A"},
    )
    assert res.status_code == 409


async def test_invalid_reason_rejected(client):
    token = await _register_and_login(client, "dtreason@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "nope", "shift": "A"},
    )
    assert res.status_code == 400


async def test_end_incident_creates_live_dataset(client):
    token = await _register_and_login(client, "dtend@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    start = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]

    ended = await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers)
    assert ended.status_code == 200
    body = ended.json()
    assert body["incident"]["ended_at"] is not None
    assert body["live_dataset_id"]

    open_list = await client.get("/downtime-tracking/incidents?open=true", headers=headers)
    assert open_list.json() == []

    live = await client.get("/downtime-tracking/live-dataset", headers=headers)
    assert live.json()["dataset_id"] == body["live_dataset_id"]


async def test_machines_and_shifts_autocomplete(client):
    token = await _register_and_login(client, "dtauto@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 2", "reason": "other", "shift": "B"},
    )
    machines = await client.get("/downtime-tracking/machines", headers=headers)
    assert set(machines.json()) == {"Line 1", "Line 2"}
    shifts = await client.get("/downtime-tracking/shifts", headers=headers)
    assert set(shifts.json()) == {"A", "B"}


async def test_downtime_tracking_tenant_isolation(client):
    token_a = await _register_and_login(client, "dta@example.com", "OrgA2")
    token_b = await _register_and_login(client, "dtb@example.com", "OrgB2")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    start = await client.post(
        "/downtime-tracking/incidents", headers=headers_a,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]

    res = await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers_b)
    assert res.status_code == 404

    open_b = await client.get("/downtime-tracking/incidents?open=true", headers=headers_b)
    assert open_b.json() == []


async def test_discard_allowed_for_creator_not_other_members(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "dtdiscardowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    start = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]

    session_factory = _asm(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="dtdiscardmember@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": "dtdiscardmember@example.com", "password": PASSWORD}
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.delete(f"/downtime-tracking/incidents/{incident_id}", headers=member_headers)
    assert res.status_code == 403

    res = await client.delete(f"/downtime-tracking/incidents/{incident_id}", headers=headers)
    assert res.status_code == 204


async def test_discard_allowed_for_owner_deleting_someone_elses_incident(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "dt2owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = _asm(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="dt2member@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post("/auth/login", json={"email": "dt2member@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    start = await client.post(
        "/downtime-tracking/incidents", headers=member_headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]

    # the org owner can discard a different member's incident.
    res = await client.delete(f"/downtime-tracking/incidents/{incident_id}", headers=headers)
    assert res.status_code == 204


async def test_discard_rejected_once_closed(client):
    token = await _register_and_login(client, "dtdiscardclosed@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    start = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]
    await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers)

    res = await client.delete(f"/downtime-tracking/incidents/{incident_id}", headers=headers)
    assert res.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downtime_tracking.py -v`
Expected: FAIL — `404 Not Found` for all `/downtime-tracking/*` calls (router doesn't exist yet).

- [ ] **Step 3: Write the service layer**

Create `backend/app/services/downtime_tracking.py`:

```python
# backend/app/services/downtime_tracking.py
"""Live downtime-board lifecycle: start/end/discard an incident, plus
continuous materialization of every closed incident for an org into that
org's one DowntimeLiveDataset - see end_incident/_materialize.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import ensure_utc
from app.models.downtime_tracking import DowntimeIncident, DowntimeLiveDataset
from app.services import datasets as ds_service

REASON_PLANNED = {
    "breakdown": False,
    "changeover": True,
    "material_shortage": False,
    "planned_maintenance": True,
    "quality_hold": False,
    "other": False,
}


async def start_incident(
    db: AsyncSession, *, org_id: uuid.UUID, started_by: uuid.UUID, machine: str, reason: str, shift: str
) -> DowntimeIncident:
    existing = (
        await db.execute(
            select(DowntimeIncident).where(
                DowntimeIncident.org_id == org_id,
                DowntimeIncident.machine == machine,
                DowntimeIncident.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"'{machine}' already has an open incident")
    incident = DowntimeIncident(org_id=org_id, started_by=started_by, machine=machine, reason=reason, shift=shift)
    db.add(incident)
    await db.flush()
    return incident


async def list_open(db: AsyncSession, org_id: uuid.UUID) -> list[DowntimeIncident]:
    rows = (
        await db.execute(
            select(DowntimeIncident)
            .where(DowntimeIncident.org_id == org_id, DowntimeIncident.ended_at.is_(None))
            .order_by(DowntimeIncident.started_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def list_machines(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    rows = (
        await db.execute(select(DowntimeIncident.machine).where(DowntimeIncident.org_id == org_id).distinct())
    ).scalars().all()
    return sorted(set(rows))


async def list_shifts(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    rows = (
        await db.execute(select(DowntimeIncident.shift).where(DowntimeIncident.org_id == org_id).distinct())
    ).scalars().all()
    return sorted(set(rows))


async def get_live_dataset_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID | None:
    live = (
        await db.execute(select(DowntimeLiveDataset).where(DowntimeLiveDataset.org_id == org_id))
    ).scalar_one_or_none()
    return live.dataset_id if live else None


async def _materialize(db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID:
    rows = (
        await db.execute(
            select(DowntimeIncident)
            .where(DowntimeIncident.org_id == org_id, DowntimeIncident.ended_at.isnot(None))
            .order_by(DowntimeIncident.started_at)
        )
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["machine", "reason", "planned", "shift", "duration_min", "started_at", "ended_at"])
    for r in rows:
        duration_min = (ensure_utc(r.ended_at) - ensure_utc(r.started_at)).total_seconds() / 60.0
        writer.writerow([
            r.machine, r.reason, REASON_PLANNED[r.reason], r.shift,
            round(duration_min, 4), r.started_at.isoformat(), r.ended_at.isoformat(),
        ])
    csv_bytes = buf.getvalue().encode("utf-8")

    live = (
        await db.execute(select(DowntimeLiveDataset).where(DowntimeLiveDataset.org_id == org_id))
    ).scalar_one_or_none()

    if live is None:
        dataset = await ds_service.create_dataset(
            db, org_id=org_id, created_by=created_by,
            name="Live Downtime Board", filename="downtime_board.csv", data=csv_bytes,
        )
        db.add(DowntimeLiveDataset(org_id=org_id, dataset_id=dataset.id))
        await db.flush()
        return dataset.id

    dataset = await ds_service.resolve_dataset(db, org_id, str(live.dataset_id))
    await ds_service.overwrite_dataset_file(db, dataset, data=csv_bytes)
    return dataset.id


async def end_incident(
    db: AsyncSession, incident: DowntimeIncident, *, org_id: uuid.UUID, created_by: uuid.UUID
) -> uuid.UUID:
    if incident.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Incident is already ended")
    incident.ended_at = datetime.now(timezone.utc)
    await db.flush()
    return await _materialize(db, org_id=org_id, created_by=created_by)


async def discard_incident(db: AsyncSession, incident: DowntimeIncident) -> None:
    if incident.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot discard an incident that has already ended")
    await db.delete(incident)
    await db.flush()
```

- [ ] **Step 4: Write the API routes**

Create `backend/app/api/routes/downtime_tracking.py`:

```python
# backend/app/api/routes/downtime_tracking.py
"""Live downtime-board endpoints - see app.services.downtime_tracking for
the lifecycle and materialization logic."""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.downtime_tracking import DowntimeIncident
from app.services import downtime_tracking as dt_service

router = APIRouter(prefix="/downtime-tracking", tags=["downtime-tracking"])

_REASONS = {"breakdown", "changeover", "material_shortage", "planned_maintenance", "quality_hold", "other"}


class IncidentStartRequest(BaseModel):
    machine: str = Field(..., min_length=1)
    reason: str
    shift: str = Field(..., min_length=1)


class IncidentResponse(BaseModel):
    id: str
    machine: str
    reason: str
    shift: str
    started_at: str
    ended_at: Optional[str]


class IncidentEndResponse(BaseModel):
    incident: IncidentResponse
    live_dataset_id: str


class LiveDatasetResponse(BaseModel):
    dataset_id: Optional[str]


def _to_response(incident: DowntimeIncident) -> IncidentResponse:
    return IncidentResponse(
        id=str(incident.id),
        machine=incident.machine,
        reason=incident.reason,
        shift=incident.shift,
        started_at=incident.started_at.isoformat(),
        ended_at=incident.ended_at.isoformat() if incident.ended_at else None,
    )


async def _get_incident(db: AsyncSession, current: CurrentUser, incident_id: uuid.UUID) -> DowntimeIncident:
    return await get_or_404(db, DowntimeIncident, current.org_id, incident_id, "Incident not found")


@router.post("/incidents", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
async def start_incident_route(
    req: IncidentStartRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.reason not in _REASONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reason must be one of {sorted(_REASONS)}")
    incident = await dt_service.start_incident(
        db, org_id=current.org_id, started_by=current.id, machine=req.machine, reason=req.reason, shift=req.shift,
    )
    await db.commit()
    await db.refresh(incident)
    return _to_response(incident)


@router.get("/incidents", response_model=List[IncidentResponse])
async def list_open_incidents_route(
    open_: bool = Query(True, alias="open"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not open_:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only open=true is supported")
    rows = await dt_service.list_open(db, current.org_id)
    return [_to_response(r) for r in rows]


@router.post("/incidents/{incident_id}/end", response_model=IncidentEndResponse)
async def end_incident_route(
    incident_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident(db, current, incident_id)
    dataset_id = await dt_service.end_incident(db, incident, org_id=current.org_id, created_by=current.id)
    await db.commit()
    await db.refresh(incident)
    return IncidentEndResponse(incident=_to_response(incident), live_dataset_id=str(dataset_id))


@router.delete("/incidents/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_incident_route(
    incident_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident(db, current, incident_id)
    if incident.started_by != current.id and current.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the incident's reporter or an owner can discard it")
    await dt_service.discard_incident(db, incident)
    await log_event(
        db, action="downtime_incident.discarded", org_id=current.org_id,
        actor_user_id=current.id, target=str(incident_id),
    )
    await db.commit()


@router.get("/machines", response_model=List[str])
async def list_machines_route(
    current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await dt_service.list_machines(db, current.org_id)


@router.get("/shifts", response_model=List[str])
async def list_shifts_route(
    current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await dt_service.list_shifts(db, current.org_id)


@router.get("/live-dataset", response_model=LiveDatasetResponse)
async def live_dataset_route(
    current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    dataset_id = await dt_service.get_live_dataset_id(db, current.org_id)
    return LiveDatasetResponse(dataset_id=str(dataset_id) if dataset_id else None)
```

- [ ] **Step 5: Register the router in main.py**

In `backend/app/main.py`, add `downtime_tracking,` to the import block (alphabetically, between `downtime,` and `forecasts,`):

```python
from app.api.routes import (
    ai_insights,
    alerts,
    automl,
    auth,
    bi,
    boms,
    computer_vision,
    data_chat,
    datasets,
    downtime,
    downtime_tracking,
    forecasts,
```

And add the router registration right after `app.include_router(downtime.router)`:

```python
app.include_router(downtime.router)
app.include_router(downtime_tracking.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_downtime_tracking.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/downtime_tracking.py backend/app/api/routes/downtime_tracking.py backend/app/main.py backend/tests/test_downtime_tracking.py
git commit -m "feat: add live downtime-tracking service and API"
```

---

### Task 4: End-to-end materialization + analytics integration tests

**Files:**
- Test: `backend/tests/test_downtime_tracking.py` (append)

**Interfaces:**
- Consumes: Task 3's `/downtime-tracking/*` endpoints; existing `POST /analytics/downtime` (`backend/app/api/routes/downtime.py`); existing `POST /analytics/shifts` (`backend/app/api/routes/shifts.py`, requires `dataset_id` + `shift_col`, everything else optional); existing `GET /datasets/{dataset_id}` (`backend/app/api/routes/datasets.py`, returns `row_count`).
- Produces: no new production code — this task is pure verification that Tasks 1-3 actually deliver on the spec's core promise (zero changes needed to the existing Downtime/Shifts pages).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_downtime_tracking.py`. Add `import asyncio` to the top of the file first (alongside the existing `import uuid`), then add:

```python
async def test_materialization_accumulates_and_feeds_downtime_analytics(client):
    token = await _register_and_login(client, "dte2e@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    async def _cycle(machine: str, reason: str, shift: str) -> dict:
        start = await client.post(
            "/downtime-tracking/incidents", headers=headers,
            json={"machine": machine, "reason": reason, "shift": shift},
        )
        incident_id = start.json()["id"]
        await asyncio.sleep(0.05)
        ended = await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers)
        return ended.json()

    first = await _cycle("Line 1", "breakdown", "A")
    dataset_id = first["live_dataset_id"]

    second = await _cycle("Line 2", "changeover", "B")
    assert second["live_dataset_id"] == dataset_id  # same dataset, not a new one

    third = await _cycle("Line 1", "breakdown", "A")
    assert third["live_dataset_id"] == dataset_id

    live = await client.get("/downtime-tracking/live-dataset", headers=headers)
    assert live.json()["dataset_id"] == dataset_id

    downtime_res = await client.post(
        "/analytics/downtime", headers=headers,
        json={
            "dataset_id": dataset_id,
            "duration_col": "duration_min",
            "category_col": "reason",
            "machine_col": "machine",
            "timestamp_col": "started_at",
        },
    )
    assert downtime_res.status_code == 200, downtime_res.text
    body = downtime_res.json()
    assert body["total_incidents"] == 3
    cats = {c["category"]: c for c in body["by_category"]}
    assert cats["breakdown"]["incidents"] == 2
    assert cats["changeover"]["incidents"] == 1
    machines = {m["machine"] for m in body["by_machine"]}
    assert machines == {"Line 1", "Line 2"}


async def test_planned_column_lets_downtime_engine_pareto_by_planned(client):
    token = await _register_and_login(client, "dtplanned@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    for machine, reason in [("Line 1", "breakdown"), ("Line 2", "changeover"), ("Line 3", "material_shortage")]:
        start = await client.post(
            "/downtime-tracking/incidents", headers=headers,
            json={"machine": machine, "reason": reason, "shift": "A"},
        )
        await client.post(f"/downtime-tracking/incidents/{start.json()['id']}/end", headers=headers)

    live = await client.get("/downtime-tracking/live-dataset", headers=headers)
    dataset_id = live.json()["dataset_id"]

    res = await client.post(
        "/analytics/downtime", headers=headers,
        json={"dataset_id": dataset_id, "duration_col": "duration_min", "category_col": "planned"},
    )
    assert res.status_code == 200, res.text
    cats = {c["category"] for c in res.json()["by_category"]}
    assert cats == {"True", "False"}


async def test_shift_column_feeds_shifts_analytics(client):
    token = await _register_and_login(client, "dtshift@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    for machine, shift in [("Line 1", "A"), ("Line 2", "B")]:
        start = await client.post(
            "/downtime-tracking/incidents", headers=headers,
            json={"machine": machine, "reason": "breakdown", "shift": shift},
        )
        await client.post(f"/downtime-tracking/incidents/{start.json()['id']}/end", headers=headers)

    live = await client.get("/downtime-tracking/live-dataset", headers=headers)
    dataset_id = live.json()["dataset_id"]

    res = await client.post(
        "/analytics/shifts", headers=headers,
        json={
            "dataset_id": dataset_id, "shift_col": "shift",
            "downtime_col": "duration_min", "downtime_category_col": "reason",
        },
    )
    assert res.status_code == 200, res.text

    # Prove the two incidents actually landed under distinct shift values (not
    # just that the endpoint didn't error) by re-grouping the same dataset's
    # Pareto breakdown with "shift" as the category column.
    by_shift = await client.post(
        "/analytics/downtime", headers=headers,
        json={"dataset_id": dataset_id, "duration_col": "duration_min", "category_col": "shift"},
    )
    assert by_shift.status_code == 200, by_shift.text
    shift_cats = {c["category"]: c["incidents"] for c in by_shift.json()["by_category"]}
    assert shift_cats == {"A": 1, "B": 1}


async def test_repeated_overwrite_produces_correct_row_count(client):
    token = await _register_and_login(client, "dtoverwrite@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    dataset_id = None
    for i in range(5):
        start = await client.post(
            "/downtime-tracking/incidents", headers=headers,
            json={"machine": f"Line {i}", "reason": "breakdown", "shift": "A"},
        )
        ended = await client.post(f"/downtime-tracking/incidents/{start.json()['id']}/end", headers=headers)
        dataset_id = ended.json()["live_dataset_id"]

    ds = await client.get(f"/datasets/{dataset_id}", headers=headers)
    assert ds.status_code == 200
    assert ds.json()["row_count"] == 5
```

- [ ] **Step 2: Run tests to verify they fail (or pass, if Tasks 1-3 are already fully correct)**

Run: `cd backend && python -m pytest tests/test_downtime_tracking.py -v`
Expected: If Tasks 1-3 were implemented exactly as specified, these should already PASS — this task is a verification checkpoint, not new production code. If anything fails, fix the bug in the Task 1-3 files (not by loosening these assertions), following systematic debugging: reproduce, find root cause, fix, re-run.

- [ ] **Step 3: Run the full backend test suite to check for regressions**

Run: `cd backend && python -m pytest -v`
Expected: PASS (all existing tests, plus every test added in Tasks 1-4)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_downtime_tracking.py
git commit -m "test: verify downtime-tracking materialization feeds Downtime/Shifts analytics"
```

---

### Task 5: Extract shared elapsed-timer helpers

**Files:**
- Modify: `frontend/src/pages/analytics/shared.tsx`
- Modify: `frontend/src/pages/analytics/PaceTracking.tsx`

**Interfaces:**
- Produces: `useElapsed(startedAt: string | undefined): string` and `asUtcMs(iso: string): number`, both exported from `frontend/src/pages/analytics/shared.tsx`.
- Consumes (by Task 6): nothing new — Task 6 imports `useElapsed` from `./shared`.

- [ ] **Step 1: Add the helpers to shared.tsx**

In `frontend/src/pages/analytics/shared.tsx`, add this after the existing imports (the file already imports `useEffect, useMemo, useRef, useState` from `"react"`, so no new import is needed):

```typescript
// ponytail: naive (no "Z"/offset) ISO string under SQLite (aiosqlite drops
// tzinfo on DateTime(timezone=True) columns even though the value is UTC;
// Postgres in prod keeps the offset) - new Date(...) would otherwise parse a
// naive string as local time and throw the elapsed clock off by the
// browser's UTC offset. Force UTC when no offset is present.
export function asUtcMs(iso: string): number {
  return new Date(/Z$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
}

export function useElapsed(startedAt: string | undefined): string {
  const [elapsed, setElapsed] = useState("00:00:00");
  useEffect(() => {
    if (!startedAt) return;
    const started = asUtcMs(startedAt);
    const tick = () => {
      const s = Math.max(0, Math.floor((Date.now() - started) / 1000));
      const h = String(Math.floor(s / 3600)).padStart(2, "0");
      const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
      const sec = String(s % 60).padStart(2, "0");
      setElapsed(`${h}:${m}:${sec}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return elapsed;
}
```

- [ ] **Step 2: Remove the local copies from PaceTracking.tsx and import the shared ones**

In `frontend/src/pages/analytics/PaceTracking.tsx`, delete these lines (the local `asUtcMs` and `useElapsed` definitions, including their comment):

```typescript
// ponytail: the API returns a naive (no "Z"/offset) ISO string under SQLite
// (aiosqlite drops tzinfo on DateTime(timezone=True) columns even though the
// value is UTC; Postgres in prod keeps the offset) - `new Date(...)` would
// otherwise parse a naive string as local time and throw the elapsed clock
// off by the browser's UTC offset. Force UTC when no offset is present.
function asUtcMs(iso: string): number {
  return new Date(/Z$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
}

function useElapsed(startedAt: string | undefined) {
  const [elapsed, setElapsed] = useState("00:00:00");
  useEffect(() => {
    if (!startedAt) return;
    const started = asUtcMs(startedAt);
    const tick = () => {
      const s = Math.max(0, Math.floor((Date.now() - started) / 1000));
      const h = String(Math.floor(s / 3600)).padStart(2, "0");
      const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
      const sec = String(s % 60).padStart(2, "0");
      setElapsed(`${h}:${m}:${sec}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return elapsed;
}
```

Then add an import of the shared version. Change:

```typescript
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
```

to:

```typescript
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useElapsed } from "./shared";
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors. `PaceTracking.tsx` still calls `useElapsed(session.data?.started_at)` exactly as before — only where the function is defined changed, not the call site.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/analytics/shared.tsx frontend/src/pages/analytics/PaceTracking.tsx
git commit -m "refactor: extract useElapsed/asUtcMs into shared.tsx"
```

---

### Task 6: Downtime Tracking frontend page

**Files:**
- Create: `frontend/src/pages/analytics/DowntimeTracking.tsx`

**Interfaces:**
- Consumes: Task 5's `useElapsed` from `./shared`; Task 3's endpoints (`GET/POST /downtime-tracking/incidents`, `POST /downtime-tracking/incidents/{id}/end`, `DELETE /downtime-tracking/incidents/{id}`, `GET /downtime-tracking/machines`, `GET /downtime-tracking/shifts`, `GET /downtime-tracking/live-dataset`); existing `apiFetch` from `"../../api/client"`.
- Produces: `DowntimeTracking` component, exported from this file, for Task 7 to route to.

- [ ] **Step 1: Create the page**

Create `frontend/src/pages/analytics/DowntimeTracking.tsx`:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useElapsed } from "./shared";

const SHIFT_STORAGE_KEY = "downtime-tracking-last-shift";
const POLL_MS = 5000;

interface DowntimeIncident {
  id: string;
  machine: string;
  reason: string;
  shift: string;
  started_at: string;
  ended_at: string | null;
}

const REASON_LABELS: Record<string, string> = {
  breakdown: "Breakdown",
  changeover: "Changeover / Setup",
  material_shortage: "Material Shortage",
  planned_maintenance: "Planned Maintenance",
  quality_hold: "Quality Hold",
  other: "Other",
};
const REASONS = Object.keys(REASON_LABELS);

function IncidentCard({
  incident,
  onEnd,
  onDiscard,
  ending,
  discarding,
}: {
  incident: DowntimeIncident;
  onEnd: () => void;
  onDiscard: () => void;
  ending: boolean;
  discarding: boolean;
}) {
  const elapsed = useElapsed(incident.started_at);
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
        <div>
          <p className="font-medium">{incident.machine}</p>
          <p className="text-sm text-muted-foreground">
            {REASON_LABELS[incident.reason] ?? incident.reason} · Shift {incident.shift} · {elapsed}
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" loading={ending} onClick={onEnd}>
            Mark up
          </Button>
          <Button size="sm" variant="destructive" loading={discarding} onClick={onDiscard}>
            Discard
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function DowntimeTracking() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [machine, setMachine] = useState("");
  const [reason, setReason] = useState(REASONS[0]);
  const [shift, setShift] = useState(() => localStorage.getItem(SHIFT_STORAGE_KEY) ?? "");
  const [error, setError] = useState<string | null>(null);

  const openIncidents = useQuery<DowntimeIncident[]>({
    queryKey: ["downtime-tracking", "open"],
    queryFn: async () => {
      const res = await apiFetch("/downtime-tracking/incidents?open=true");
      if (!res.ok) throw new Error("Failed to load open incidents");
      return res.json();
    },
    refetchInterval: POLL_MS,
  });

  const machines = useQuery<string[]>({
    queryKey: ["downtime-tracking", "machines"],
    queryFn: async () => {
      const res = await apiFetch("/downtime-tracking/machines");
      if (!res.ok) throw new Error("Failed to load machines");
      return res.json();
    },
  });

  const shifts = useQuery<string[]>({
    queryKey: ["downtime-tracking", "shifts"],
    queryFn: async () => {
      const res = await apiFetch("/downtime-tracking/shifts");
      if (!res.ok) throw new Error("Failed to load shifts");
      return res.json();
    },
  });

  const liveDataset = useQuery<{ dataset_id: string | null }>({
    queryKey: ["downtime-tracking", "live-dataset"],
    queryFn: async () => {
      const res = await apiFetch("/downtime-tracking/live-dataset");
      if (!res.ok) throw new Error("Failed to load live dataset");
      return res.json();
    },
  });

  const start = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/downtime-tracking/incidents", {
        method: "POST",
        body: JSON.stringify({ machine, reason, shift }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to mark down");
      return res.json();
    },
    onSuccess: () => {
      setError(null);
      localStorage.setItem(SHIFT_STORAGE_KEY, shift);
      setMachine("");
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "open"] });
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "machines"] });
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "shifts"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const end = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/downtime-tracking/incidents/${id}/end`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to mark up");
      return res.json();
    },
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "open"] });
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "live-dataset"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const discard = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/downtime-tracking/incidents/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to discard");
    },
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "open"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const incidents = openIncidents.data ?? [];
  const liveDatasetId = liveDataset.data?.dataset_id ?? null;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Downtime Tracking"
        description="Live board — mark a machine down with a reason, mark it back up when it's fixed. Every closed incident feeds the Downtime analytics page automatically."
        actions={
          liveDatasetId ? (
            <Button onClick={() => navigate(`/analytics/downtime?dataset_id=${liveDatasetId}`)}>
              View live analytics
            </Button>
          ) : undefined
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Report downtime</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Machine">
              <Input
                list="downtime-machine-options"
                value={machine}
                onChange={(e) => setMachine(e.target.value)}
                placeholder="Line 3 - Press 2"
              />
              <datalist id="downtime-machine-options">
                {(machines.data ?? []).map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </Field>
            <Field label="Reason">
              <Select value={reason} onChange={(e) => setReason(e.target.value)}>
                {REASONS.map((r) => (
                  <option key={r} value={r}>
                    {REASON_LABELS[r]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Shift">
              <Input
                list="downtime-shift-options"
                value={shift}
                onChange={(e) => setShift(e.target.value)}
                placeholder="A"
              />
              <datalist id="downtime-shift-options">
                {(shifts.data ?? []).map((s) => (
                  <option key={s} value={s} />
                ))}
              </datalist>
            </Field>
          </div>
          {error && <Alert variant="destructive">{error}</Alert>}
          <Button disabled={!machine || !reason || !shift} loading={start.isPending} onClick={() => start.mutate()}>
            Mark down
          </Button>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">Currently down</h2>
          <Badge variant={incidents.length > 0 ? "warning" : "success"}>{incidents.length}</Badge>
        </div>
        {incidents.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing's down right now.</p>
        )}
        {incidents.map((incident) => (
          <IncidentCard
            key={incident.id}
            incident={incident}
            ending={end.isPending && end.variables === incident.id}
            discarding={discard.isPending && discard.variables === incident.id}
            onEnd={() => end.mutate(incident.id)}
            onDiscard={() => discard.mutate(incident.id)}
          />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/analytics/DowntimeTracking.tsx
git commit -m "feat: add Downtime Tracking live board page"
```

---

### Task 7: Nav entry, route, and manual verification

**Files:**
- Modify: `frontend/src/lib/nav.ts`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: Task 6's `DowntimeTracking` export from `./pages/analytics/DowntimeTracking`.
- Produces: route `/analytics/downtime-tracking` reachable from the sidebar nav.

- [ ] **Step 1: Add the nav entry**

In `frontend/src/lib/nav.ts`, add `AlertTriangle` to the `lucide-react` import list (alphabetically, between `Activity` and `BarChart3`):

```typescript
import {
  Activity,
  AlertTriangle,
  BarChart3,
```

Then add the nav item in the `"Plant Analytics"` section, right after the existing Downtime entry:

```typescript
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff },
      { to: "/analytics/downtime-tracking", label: "Downtime Tracking", icon: AlertTriangle },
```

- [ ] **Step 2: Add the route**

In `frontend/src/App.tsx`, add the import (alphabetically, near the existing `Downtime` import):

```typescript
import { Downtime } from "./pages/analytics/Downtime";
import { DowntimeTracking } from "./pages/analytics/DowntimeTracking";
```

Then add the route right after the existing Downtime route:

```tsx
<Route path="/analytics/downtime" element={<Downtime />} />
<Route path="/analytics/downtime-tracking" element={<DowntimeTracking />} />
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Run the full backend test suite one more time**

Run: `cd backend && python -m pytest -v`
Expected: PASS (confirms nothing in the frontend-only changes broke backend wiring — it shouldn't, but this is the last checkpoint before manual verification).

- [ ] **Step 5: Manual in-browser verification**

Start the frontend and backend dev servers, then in the browser:
1. Navigate to `/analytics/downtime-tracking`. Confirm the page loads with an empty "Currently down" list.
2. Mark "Press 1" down with reason "Breakdown" and shift "A". Confirm it appears in the "Currently down" list with a live-updating elapsed timer.
3. Mark "Press 1" down again (same name) — confirm it's rejected (the Mark Down button's request fails with a 409, surfaced via the error `Alert`).
4. Mark "Press 1" back up. Confirm it disappears from "Currently down" and a "View live analytics" button appears in the page header.
5. Repeat steps 2-4 for a second machine ("Conveyor 2", reason "Material Shortage", shift "B").
6. Click "View live analytics". Confirm it navigates to `/analytics/downtime?dataset_id=...` and that page's Pareto chart, machine table, and stat cards reflect both incidents just entered.
7. Reload the Downtime Tracking page. Confirm the machine/shift `<datalist>` autocompletes now suggest "Press 1"/"Conveyor 2" and "A"/"B" from prior entries.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/nav.ts frontend/src/App.tsx
git commit -m "feat: wire up Downtime Tracking route and nav entry"
```
