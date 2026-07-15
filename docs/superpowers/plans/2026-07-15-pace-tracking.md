# Manual/Assembly-Line Pace Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an assembly line with no sensors/time-tracking system record production via operator taps (good/reject), and turn a finished session into a normal `Dataset` row so every existing OEE feature works on it unchanged.

**Architecture:** New `PaceSession`/`PaceTap` tables hold live session state and the raw tap log. A service layer (`app/services/pace_tracking.py`) owns the start/tap/pause/resume/stop/discard state machine, with an atomic-SQL-increment helper (`_finalize_pause`) so concurrent pause/resume calls can't lose an update. `stop_session` builds an in-memory CSV and calls the *exact same* `datasets.create_dataset` the upload endpoint uses — zero new code in `oee.py`/`oee_calc.py`. A new router (`app/api/routes/pace_tracking.py`) exposes 8 endpoints with org-scoped, no-creator-check RBAC for all lifecycle actions (shift handoff) except `DELETE` (discard), which is creator-or-owner only, matching `reports.py:delete_inbox_item`. A standalone frontend page (not `EngineScreen`) drives it with tap buttons and polls for state on reload.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, DuckDB (via existing `datasets` service), pytest + httpx `AsyncClient`, React + TanStack Query, Tailwind/shadcn-style UI kit already in the repo.

## Global Constraints

- Roles are `owner`/`member` only (`app/models/user.py`) — no new role.
- Every mutating pace endpoint checks `session.status` and 409s on an invalid transition; this is a status check, independent of the RBAC checks below.
- View and all lifecycle mutations (tap/pause/resume/stop) are org-scoped only, no creator check — this is the deliberate shift-handoff allowance.
- `DELETE` (discard) is creator-or-owner only, works regardless of session status, and is audit-logged via `log_event`.
- Service functions `flush()` but never `commit()` — the router commits once, matching every existing route in this codebase (see `app/api/routes/datasets.py`, `app/api/routes/reports.py`).
- New tables must be registered in `backend/tests/conftest.py`'s model-import list (it drives `Base.metadata.create_all()` for the in-memory test DB) — `backend/alembic/env.py`'s import list is for autogenerate only and is *not* required for a hand-written migration to run (the existing `report_spec` module is absent from it too — same precedent, don't fix that in this plan).

---

## Task 1: Data model, migration, service, router — full lifecycle (no pause yet in tests, but pause/resume code ships now since guard tests need it)

**Files:**
- Create: `backend/app/models/pace_tracking.py`
- Create: `backend/alembic/versions/d4f7a1c92b56_pace_tracking.py`
- Create: `backend/app/services/pace_tracking.py`
- Create: `backend/app/api/routes/pace_tracking.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_pace_tracking.py` (new file)

**Interfaces:**
- Produces: `PaceSession`, `PaceTap` models (`app.models.pace_tracking`); service functions `start_session`, `tap_counts`, `record_tap`, `pause_session`, `_finalize_pause`, `resume_session`, `stop_session`, `discard_session` (`app.services.pace_tracking`); router at prefix `/pace` with `PaceSessionResponse` shape `{id, line, operator, target_rate, status, pause_reason, started_at, stopped_at, dataset_id, good_count, reject_count, unplanned_dt_min, planned_dt_min, other_dt_min}`.
- Consumes: `app.services.datasets.create_dataset` (existing), `app.api.deps.get_or_404` / `CurrentUser` / `get_current_user` (existing), `app.middleware.audit.log_event` (existing).

- [ ] **Step 1: Create the model file**

```python
# backend/app/models/pace_tracking.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaceSession(Base):
    """A live tap-tracking session for a manual/assembly line with no sensors or
    time-tracking system - the only data source is an operator tapping a button
    per completed unit. On Stop, this materializes into a normal, immutable
    Dataset row via the same ingestion path a CSV upload uses (see
    app.services.pace_tracking.stop_session), so every OEE engine feature works
    on pace-tracking data with zero changes.
    """

    __tablename__ = "pace_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    line: Mapped[str] = mapped_column(String(255), nullable=False)
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    target_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # active | paused | stopped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # breakdown | setup | other - only meaningful while status == "paused"
    pause_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Accumulators, updated atomically in-SQL by
    # app.services.pace_tracking._finalize_pause so two near-simultaneous
    # pause/resume calls can't lose an update.
    unplanned_dt_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    planned_dt_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    other_dt_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id"), nullable=True
    )


class PaceTap(Base):
    """One tap event - the raw audit/debug log. The session row's accumulators
    plus COUNT(*)-by-kind on this table are what materialization actually reads."""

    __tablename__ = "pace_taps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pace_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # good | reject
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 2: Create the migration**

```python
# backend/alembic/versions/d4f7a1c92b56_pace_tracking.py
"""pace tracking sessions + taps

Revision ID: d4f7a1c92b56
Revises: f3a8b2e7c641
Create Date: 2026-07-15

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4f7a1c92b56"  # pragma: allowlist secret
down_revision = "f3a8b2e7c641"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pace_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("line", sa.String(length=255), nullable=False),
        sa.Column("operator", sa.String(length=255), nullable=False),
        sa.Column("target_rate", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pause_reason", sa.String(length=16), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unplanned_dt_min", sa.Float(), nullable=False),
        sa.Column("planned_dt_min", sa.Float(), nullable=False),
        sa.Column("other_dt_min", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pace_sessions_org_id", "pace_sessions", ["org_id"])

    op.create_table(
        "pace_taps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["pace_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pace_taps_session_id", "pace_taps", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_pace_taps_session_id", table_name="pace_taps")
    op.drop_table("pace_taps")
    op.drop_index("ix_pace_sessions_org_id", table_name="pace_sessions")
    op.drop_table("pace_sessions")
```

- [ ] **Step 3: Register the new model module in `backend/tests/conftest.py`**

In the `from app.models import (...)` block, insert `pace_tracking,` alphabetically between `maintenance_schedule,` and `procurement,`:

```python
from app.models import (  # noqa: F401 - register models
    alert,
    audit,
    automl_run,
    bom,
    dashboard,
    dataset,
    forecast,
    inventory,
    inventory_snapshot,
    integration,
    kpi,
    location,
    lot,
    maintenance_schedule,
    pace_tracking,
    procurement,
    rca_case,
    realtime,
    report_spec,
    scenario,
    telemetry,
    tenant,
    user,
    workflow,
)
```

- [ ] **Step 4: Create the service file**

```python
# backend/app/services/pace_tracking.py
"""Pace-tracking session lifecycle: start/tap/pause/resume/stop/discard for a
manual/assembly line with no sensors - the operator taps a button per
completed unit. Stop materializes the session into a normal, immutable
Dataset row via the same ingestion path a CSV upload uses
(datasets.create_dataset), so every OEE engine feature works on
pace-tracking data with zero changes.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pace_tracking import PaceSession, PaceTap
from app.services import datasets as ds_service

_PAUSE_FIELD = {"breakdown": "unplanned_dt_min", "setup": "planned_dt_min", "other": "other_dt_min"}


async def start_session(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    line: str,
    operator: str,
    target_rate: float | None,
) -> PaceSession:
    session = PaceSession(org_id=org_id, created_by=created_by, line=line, operator=operator, target_rate=target_rate)
    db.add(session)
    await db.flush()
    return session


async def tap_counts(db: AsyncSession, session_id: uuid.UUID) -> tuple[int, int]:
    rows = (
        await db.execute(
            select(PaceTap.kind, func.count()).where(PaceTap.session_id == session_id).group_by(PaceTap.kind)
        )
    ).all()
    counts = {kind: int(n) for kind, n in rows}
    return counts.get("good", 0), counts.get("reject", 0)


async def record_tap(db: AsyncSession, session: PaceSession, kind: str) -> PaceTap:
    if session.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is {session.status}, not active")
    tap = PaceTap(session_id=session.id, kind=kind)
    db.add(tap)
    await db.flush()
    return tap


async def pause_session(db: AsyncSession, session: PaceSession, reason: str) -> PaceSession:
    if session.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is {session.status}, not active")
    session.status = "paused"
    session.pause_reason = reason
    session.paused_at = datetime.now(timezone.utc)
    await db.flush()
    return session


async def _finalize_pause(db: AsyncSession, session: PaceSession, now: datetime) -> None:
    """Accumulate the just-ended pause window into whichever bucket matches
    pause_reason, via an atomic SQL increment (not a Python load-mutate-commit
    round trip) so two near-simultaneous requests can't lose an update. Called
    from both resume() and stop() (when stopping while paused) - one code path,
    so the two can't drift apart."""
    elapsed_min = (now - session.paused_at).total_seconds() / 60.0
    field = _PAUSE_FIELD[session.pause_reason]
    column = getattr(PaceSession, field)
    await db.execute(update(PaceSession).where(PaceSession.id == session.id).values(**{field: column + elapsed_min}))
    session.paused_at = None
    session.pause_reason = None
    await db.flush()
    await db.refresh(session)


async def resume_session(db: AsyncSession, session: PaceSession) -> PaceSession:
    if session.status != "paused":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is {session.status}, not paused")
    await _finalize_pause(db, session, datetime.now(timezone.utc))
    session.status = "active"
    await db.flush()
    return session


async def stop_session(
    db: AsyncSession, session: PaceSession, *, org_id: uuid.UUID, created_by: uuid.UUID
) -> PaceSession:
    if session.status == "stopped":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session is already stopped")
    now = datetime.now(timezone.utc)
    if session.status == "paused":
        await _finalize_pause(db, session, now)

    planned_min = (now - session.started_at).total_seconds() / 60.0
    operating_min = max(0.0, planned_min - session.unplanned_dt_min - session.planned_dt_min - session.other_dt_min)
    good_units, reject_units = await tap_counts(db, session.id)
    total_units = good_units + reject_units

    header = ["line", "operator", "planned_min", "operating_min", "total_units", "good_units",
              "unplanned_dt_min", "planned_dt_min"]
    row = [session.line, session.operator, planned_min, operating_min, total_units, good_units,
           session.unplanned_dt_min, session.planned_dt_min]
    if session.target_rate is not None:
        header.append("target_rate")
        row.append(session.target_rate)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerow(row)
    csv_bytes = buf.getvalue().encode("utf-8")

    dataset = await ds_service.create_dataset(
        db,
        org_id=org_id,
        created_by=created_by,
        name=f"Pace: {session.line} - {session.operator} ({now:%Y-%m-%d %H:%M})",
        filename="pace_session.csv",
        data=csv_bytes,
    )

    session.status = "stopped"
    session.stopped_at = now
    session.dataset_id = dataset.id
    await db.flush()
    return session


async def discard_session(db: AsyncSession, session: PaceSession) -> None:
    await db.delete(session)
    await db.flush()
```

- [ ] **Step 5: Create the router file**

```python
# backend/app/api/routes/pace_tracking.py
"""Live tap-tracking session lifecycle - see app.services.pace_tracking for
the state machine and materialization logic."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.pace_tracking import PaceSession
from app.services import pace_tracking as pace_service

router = APIRouter(prefix="/pace", tags=["pace-tracking"])

_TAP_KINDS = {"good", "reject"}
_PAUSE_REASONS = {"breakdown", "setup", "other"}


class PaceSessionCreateRequest(BaseModel):
    line: str = Field(..., min_length=1)
    operator: str = Field(..., min_length=1)
    target_rate: Optional[float] = Field(default=None, gt=0)


class PaceTapRequest(BaseModel):
    kind: str


class PacePauseRequest(BaseModel):
    reason: str


class PaceSessionResponse(BaseModel):
    id: str
    line: str
    operator: str
    target_rate: Optional[float]
    status: str
    pause_reason: Optional[str]
    started_at: str
    stopped_at: Optional[str]
    dataset_id: Optional[str]
    good_count: int
    reject_count: int
    unplanned_dt_min: float
    planned_dt_min: float
    other_dt_min: float


async def _to_response(db: AsyncSession, session: PaceSession) -> PaceSessionResponse:
    good, reject = await pace_service.tap_counts(db, session.id)
    return PaceSessionResponse(
        id=str(session.id),
        line=session.line,
        operator=session.operator,
        target_rate=session.target_rate,
        status=session.status,
        pause_reason=session.pause_reason,
        started_at=session.started_at.isoformat(),
        stopped_at=session.stopped_at.isoformat() if session.stopped_at else None,
        dataset_id=str(session.dataset_id) if session.dataset_id else None,
        good_count=good,
        reject_count=reject,
        unplanned_dt_min=session.unplanned_dt_min,
        planned_dt_min=session.planned_dt_min,
        other_dt_min=session.other_dt_min,
    )


async def _get_session(db: AsyncSession, current: CurrentUser, session_id: uuid.UUID) -> PaceSession:
    return await get_or_404(db, PaceSession, current.org_id, session_id, "Pace session not found")


@router.post("/sessions", response_model=PaceSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    req: PaceSessionCreateRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await pace_service.start_session(
        db, org_id=current.org_id, created_by=current.id,
        line=req.line, operator=req.operator, target_rate=req.target_rate,
    )
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.get("/sessions", response_model=list[PaceSessionResponse])
async def list_sessions(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(PaceSession).where(PaceSession.org_id == current.org_id).order_by(PaceSession.started_at.desc())
        )
    ).scalars().all()
    return [await _to_response(db, s) for s in rows]


@router.get("/sessions/{session_id}", response_model=PaceSessionResponse)
async def get_session(
    session_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_session(db, current, session_id)
    return await _to_response(db, session)


@router.post("/sessions/{session_id}/tap", response_model=PaceSessionResponse)
async def tap_session(
    session_id: uuid.UUID,
    req: PaceTapRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.kind not in _TAP_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"kind must be one of {sorted(_TAP_KINDS)}")
    session = await _get_session(db, current, session_id)
    await pace_service.record_tap(db, session, req.kind)
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.post("/sessions/{session_id}/pause", response_model=PaceSessionResponse)
async def pause_session_route(
    session_id: uuid.UUID,
    req: PacePauseRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.reason not in _PAUSE_REASONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reason must be one of {sorted(_PAUSE_REASONS)}")
    session = await _get_session(db, current, session_id)
    await pace_service.pause_session(db, session, req.reason)
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.post("/sessions/{session_id}/resume", response_model=PaceSessionResponse)
async def resume_session_route(
    session_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_session(db, current, session_id)
    await pace_service.resume_session(db, session)
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.post("/sessions/{session_id}/stop", response_model=PaceSessionResponse)
async def stop_session_route(
    session_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_session(db, current, session_id)
    await pace_service.stop_session(db, session, org_id=current.org_id, created_by=current.id)
    await log_event(
        db, action="pace_session.stopped", org_id=current.org_id, actor_user_id=current.id,
        target=str(session.id), metadata={"dataset_id": str(session.dataset_id)},
    )
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_session_route(
    session_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_session(db, current, session_id)
    if session.created_by != current.id and current.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the session's creator or an owner can discard it")
    await pace_service.discard_session(db, session)
    await log_event(
        db, action="pace_session.discarded", org_id=current.org_id, actor_user_id=current.id, target=str(session_id)
    )
    await db.commit()
```

- [ ] **Step 6: Register the router in `backend/app/main.py`**

In the `from app.api.routes import (...)` block, insert `pace_tracking,` alphabetically between `orgs,` and `plant_summary,`:

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
    forecasts,
    integrations,
    inventory,
    inventory_analytics,
    items,
    kpis,
    locations,
    lots,
    mfg_forecast,
    oee,
    orgs,
    pace_tracking,
    plant_summary,
    predictive,
    process_mining,
    purchase_orders,
    quality,
    rca_cases,
    realtime,
    reports,
    scrap,
    shifts,
    simulation,
    spc,
    suppliers,
    throughput,
    what_if_scenarios,
    workflows,
)
```

Then, right after `app.include_router(oee.router)`, add:

```python
app.include_router(oee.router)
app.include_router(pace_tracking.router)
app.include_router(plant_summary.router)
```

- [ ] **Step 7: Write the failing tests**

```python
# backend/tests/test_pace_tracking.py
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email: str, org_name: str = "PaceOrg") -> str:
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _start_session(client, headers, line: str = "Line 1", operator: str = "J. Alvarez") -> str:
    res = await client.post(
        "/pace/sessions", headers=headers, json={"line": line, "operator": operator, "target_rate": None}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_pace_full_lifecycle_no_pauses(client):
    token = await _register_and_login(client, "pacelife@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    session_id = await _start_session(client)
    session_id = await _start_session(client, headers)

    for _ in range(5):
        res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"})
        assert res.status_code == 200
    for _ in range(2):
        res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "reject"})
        assert res.status_code == 200

    stopped = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    assert stopped.status_code == 200
    body = stopped.json()
    assert body["status"] == "stopped"
    assert body["good_count"] == 5
    assert body["reject_count"] == 2
    assert body["dataset_id"]


async def test_pace_state_machine_guards(client):
    token = await _register_and_login(client, "paceguard@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    res = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)
    assert res.status_code == 409

    res = await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    assert res.status_code == 200

    res = await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "setup"})
    assert res.status_code == 409

    res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"})
    assert res.status_code == 409

    res = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)
    assert res.status_code == 200

    res = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    assert res.status_code == 200

    res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"})
    assert res.status_code == 409
    res = await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    assert res.status_code == 409
    res = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)
    assert res.status_code == 409
    res = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    assert res.status_code == 409
```

Note the deliberate copy/paste bug in `test_pace_full_lifecycle_no_pauses` (`session_id = await _start_session(client)` before the correct call) — Step 8 catches it.

- [ ] **Step 8: Run the tests, fix the copy/paste bug, and verify they pass**

Run: `cd backend && python -m pytest tests/test_pace_tracking.py -v`
Expected first run: FAIL — `_start_session(client)` is missing the required `headers` arg.

Fix in `test_pace_full_lifecycle_no_pauses`: delete the stray first line so it reads:

```python
async def test_pace_full_lifecycle_no_pauses(client):
    token = await _register_and_login(client, "pacelife@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    session_id = await _start_session(client, headers)
```

Run again: `cd backend && python -m pytest tests/test_pace_tracking.py -v`
Expected: 2 passed.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/pace_tracking.py backend/alembic/versions/d4f7a1c92b56_pace_tracking.py \
  backend/app/services/pace_tracking.py backend/app/api/routes/pace_tracking.py \
  backend/app/main.py backend/tests/conftest.py backend/tests/test_pace_tracking.py
git commit -m "feat: add pace-tracking session lifecycle (start/tap/pause/resume/stop/discard)"
```

---

## Task 2: Multi-cycle pause accounting, stop-while-paused, mixed reasons

**Files:**
- Modify: `backend/tests/test_pace_tracking.py`

**Interfaces:**
- Consumes: everything from Task 1 (no production code changes in this task — `_finalize_pause`'s summing behavior and `stop_session`'s paused-branch were already implemented in Task 1; this task proves it).

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_pace_tracking.py
import asyncio


async def test_pace_multi_cycle_pause_sums_not_overwrites(client):
    token = await _register_and_login(client, "pacecycle@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    await asyncio.sleep(0.3)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    await asyncio.sleep(0.1)
    resumed = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    unplanned = resumed.json()["unplanned_dt_min"]
    expected_min = (0.3 + 0.1) / 60
    # Both windows counted (not just the last ~0.1s one) - proves the SQL
    # increment sums across pause/resume cycles instead of overwriting.
    assert unplanned > expected_min * 0.5
    assert unplanned < expected_min * 3


async def test_pace_stop_while_paused_counts_open_pause_window(client):
    token = await _register_and_login(client, "pacestopwhile@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "setup"})
    await asyncio.sleep(0.2)
    # No resume call - stop must finalize the still-open pause window itself.
    stopped = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)

    assert stopped.status_code == 200
    body = stopped.json()
    assert body["status"] == "stopped"
    assert body["planned_dt_min"] > 0


async def test_pace_mixed_pause_reasons_tracked_separately(client):
    token = await _register_and_login(client, "pacemixed@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    await asyncio.sleep(0.15)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "setup"})
    await asyncio.sleep(0.1)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "other"})
    await asyncio.sleep(0.1)
    resumed = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    body = resumed.json()
    assert body["unplanned_dt_min"] > 0
    assert body["planned_dt_min"] > 0
    assert body["other_dt_min"] > 0
```

- [ ] **Step 2: Run the tests to verify they pass (no new production code needed)**

Run: `cd backend && python -m pytest tests/test_pace_tracking.py -v`
Expected: 5 passed (the 2 from Task 1 plus these 3).

If `test_pace_multi_cycle_pause_sums_not_overwrites` fails with `unplanned` close to `expected_min * 0.2` (i.e., only the *last* pause window counted), the bug is that `resume_session`/`stop_session` overwrote the accumulator instead of incrementing it in SQL — re-check `_finalize_pause` uses `column + elapsed_min` in the `update().values()` call, not a plain assignment.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_pace_tracking.py
git commit -m "test: verify multi-cycle pause accounting and stop-while-paused"
```

---

## Task 3: RBAC — shift handoff and creator-or-owner discard

**Files:**
- Modify: `backend/tests/test_pace_tracking.py`

**Interfaces:**
- Consumes: everything from Task 1 (discard's creator-or-owner check and the lack of a creator check on tap/pause/resume/stop were already implemented in Task 1's router; this task proves both).

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_pace_tracking.py
async def test_pace_shift_handoff_allows_other_member_to_act(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "paceowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_id = await _start_session(client, headers, line="Line A", operator="Alice")

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="paceoperatorb@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": "paceoperatorb@example.com", "password": PASSWORD}
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Operator B (a different org member) can tap, pause, resume, and stop
    # Operator A's still-running session - the deliberate shift-handoff allowance.
    res = await client.post(f"/pace/sessions/{session_id}/tap", headers=member_headers, json={"kind": "good"})
    assert res.status_code == 200
    res = await client.post(
        f"/pace/sessions/{session_id}/pause", headers=member_headers, json={"reason": "breakdown"}
    )
    assert res.status_code == 200
    res = await client.post(f"/pace/sessions/{session_id}/resume", headers=member_headers)
    assert res.status_code == 200
    res = await client.post(f"/pace/sessions/{session_id}/stop", headers=member_headers)
    assert res.status_code == 200
    assert res.json()["status"] == "stopped"


async def test_pace_discard_allowed_for_creator_not_other_members(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "pdowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_id = await _start_session(client, headers)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="pdmember@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post("/auth/login", json={"email": "pdmember@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # a different member can't discard someone else's session...
    res = await client.delete(f"/pace/sessions/{session_id}", headers=member_headers)
    assert res.status_code == 403

    # ...but the creator can discard their own, in any status (still "active" here).
    res = await client.delete(f"/pace/sessions/{session_id}", headers=headers)
    assert res.status_code == 204


async def test_pace_discard_allowed_for_owner_deleting_someone_elses_session(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "pd2owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="pd2member@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post("/auth/login", json={"email": "pd2member@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    session_id = await _start_session(client, member_headers)

    # the org owner can discard a different member's session.
    res = await client.delete(f"/pace/sessions/{session_id}", headers=headers)
    assert res.status_code == 204
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_pace_tracking.py -v`
Expected: 8 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_pace_tracking.py
git commit -m "test: verify pace-tracking shift-handoff and creator-or-owner discard RBAC"
```

---

## Task 4: End-to-end OEE integration (materialized dataset -> `/analytics/oee`)

**Files:**
- Modify: `backend/tests/test_pace_tracking.py`

**Interfaces:**
- Consumes: `POST /analytics/oee` (existing, `app.api.routes.oee`), the `six_big_losses` response shape (`{total_loss_minutes, buckets: [{key, label, family, minutes, ...}], availability_split, ...}`).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_pace_tracking.py
async def test_pace_materialized_dataset_feeds_oee_with_unclassified_downtime(client):
    token = await _register_and_login(client, "paceoee@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers, line="Line 7", operator="R. Chen")

    for _ in range(10):
        await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"})
    for _ in range(2):
        await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "reject"})

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    await asyncio.sleep(0.15)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "setup"})
    await asyncio.sleep(0.1)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    # >3s pushes the "other" pause past _six_big_losses' 0.05-minute floor for
    # reporting the resulting gap as its own "Unclassified Downtime" bucket.
    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "other"})
    await asyncio.sleep(3.2)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    stopped = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    body = stopped.json()
    dataset_id = body["dataset_id"]
    assert body["good_count"] == 10
    assert body["reject_count"] == 2

    oee_res = await client.post(
        "/analytics/oee",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "unplanned_downtime_col": "unplanned_dt_min",
            "planned_downtime_col": "planned_dt_min",
        },
    )
    assert oee_res.status_code == 200
    oee = oee_res.json()

    assert 0 <= oee["availability"] <= 100
    assert 0 <= oee["quality"] <= 100
    assert oee["total_units"] == 12
    assert oee["good_units"] == 10
    assert oee["quality"] == round(10 / 12 * 100, 2)

    six = oee["six_big_losses"]
    assert six is not None
    assert six["availability_split"] is True
    buckets = {b["key"]: b for b in six["buckets"]}
    assert "equipment_failure" in buckets  # breakdown pause -> unplanned_dt_min column
    assert "setup_adjustments" in buckets  # setup pause -> planned_dt_min column
    # "other" isn't its own CSV column, so its time falls into the leftover
    # gap between planned_min and (operating_min + unplanned + planned) -
    # exactly what _six_big_losses buckets as Unclassified Downtime.
    assert "unclassified_downtime" in buckets
    assert buckets["unclassified_downtime"]["minutes"] > 0
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pace_tracking.py -v -k oee`
Expected: 1 passed (takes ~3.5s due to the `other`-reason sleep).

Then run the full file once more to confirm nothing regressed:

Run: `cd backend && python -m pytest tests/test_pace_tracking.py -v`
Expected: 9 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_pace_tracking.py
git commit -m "test: verify pace-tracking dataset materializes into working OEE six-big-losses"
```

---

## Task 5: Frontend page, nav entry, route, and OEE deep-link pre-selection

**Files:**
- Create: `frontend/src/pages/analytics/PaceTracking.tsx`
- Modify: `frontend/src/lib/nav.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/analytics/shared.tsx` (small addition to `EngineScreen` for the OEE deep-link)

**Interfaces:**
- Consumes: `apiFetch` (`../../api/client`), `PageHeader`, `Field`, `StatCard`, `Button`, `Input`, `Select`, `Badge`, `Alert`, `Card`/`CardContent`/`CardHeader`/`CardTitle` (all existing UI components), the `/pace/sessions*` endpoints from Task 1 (response shape `PaceSessionResponse` above).
- Produces: route `/analytics/pace-tracking`; nav entry under "Plant Analytics".

- [ ] **Step 1: Add the nav entry in `frontend/src/lib/nav.ts`**

Add `Gauge` is already imported for OEE; add a new icon import (`Timer`) and the nav item. In the icon import block, add `Timer` alphabetically after `TimerOff`... actually `Timer` sorts before `TimerOff` (shorter string prefix) - add it right before `TimerOff`:

```typescript
import {
  Activity,
  BarChart3,
  Bell,
  Boxes,
  Brain,
  CalendarRange,
  Camera,
  Database,
  Factory,
  FileStack,
  FileText,
  FlaskConical,
  Gauge,
  History,
  LayoutDashboard,
  LayoutGrid,
  Layers,
  PackageX,
  Plug,
  Radio,
  Search,
  ShoppingCart,
  Sparkles,
  SplitSquareHorizontal,
  Target,
  Terminal,
  Timer,
  TimerOff,
  TrendingUp,
  Users,
  Warehouse,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";
```

In `NAV_SECTIONS`, "Plant Analytics" section, add the item right after OEE:

```typescript
  {
    label: "Plant Analytics",
    items: [
      { to: "/analytics/plant-summary", label: "Plant Summary", icon: Factory },
      { to: "/analytics/oee", label: "OEE", icon: Gauge },
      { to: "/analytics/pace-tracking", label: "Pace Tracking", icon: Timer },
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff },
      { to: "/analytics/throughput", label: "Throughput", icon: BarChart3 },
      { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX },
      { to: "/analytics/shifts", label: "Shifts", icon: Users },
      { to: "/analytics/spc", label: "SPC", icon: Activity },
      { to: "/kpis", label: "KPIs", icon: Target },
    ],
  },
```

- [ ] **Step 2: Add the route in `frontend/src/App.tsx`**

Add the import alphabetically-by-usage near the other analytics pages, right after `Oee`:

```typescript
import { Oee } from "./pages/analytics/Oee";
import { PaceTracking } from "./pages/analytics/PaceTracking";
import { PlantSummary } from "./pages/analytics/PlantSummary";
```

Add the route right after the OEE route:

```typescript
        <Route path="/analytics/oee" element={<Oee />} />
        <Route path="/analytics/pace-tracking" element={<PaceTracking />} />
        <Route path="/analytics/plant-summary" element={<PlantSummary />} />
```

- [ ] **Step 3: Create `frontend/src/pages/analytics/PaceTracking.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { StatCard } from "@/components/StatCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STORAGE_KEY = "pace-tracking-session-id";
const POLL_MS = 5000;

interface PaceSession {
  id: string;
  line: string;
  operator: string;
  target_rate: number | null;
  status: "active" | "paused" | "stopped";
  pause_reason: "breakdown" | "setup" | "other" | null;
  started_at: string;
  stopped_at: string | null;
  dataset_id: string | null;
  good_count: number;
  reject_count: number;
  unplanned_dt_min: number;
  planned_dt_min: number;
  other_dt_min: number;
}

const PAUSE_LABELS: Record<string, string> = {
  breakdown: "Breakdown",
  setup: "Setup / Changeover",
  other: "Other",
};

function useElapsed(startedAt: string | undefined) {
  const [elapsed, setElapsed] = useState("00:00:00");
  useEffect(() => {
    if (!startedAt) return;
    const started = new Date(startedAt).getTime();
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

export function PaceTracking() {
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY));
  const [form, setForm] = useState({ line: "", operator: "", target_rate: "" });
  const [formError, setFormError] = useState<string | null>(null);
  const [pauseReason, setPauseReason] = useState("breakdown");
  const [showPausePicker, setShowPausePicker] = useState(false);

  const session = useQuery<PaceSession>({
    queryKey: ["pace-session", sessionId],
    queryFn: async () => {
      const res = await apiFetch(`/pace/sessions/${sessionId}`);
      if (!res.ok) throw new Error("Session not found");
      return res.json();
    },
    enabled: !!sessionId,
    refetchInterval: POLL_MS,
    retry: false,
  });

  function persist(id: string | null) {
    setSessionId(id);
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  }

  useEffect(() => {
    if (sessionId && session.isError) persist(null);
  }, [sessionId, session.isError]);

  const start = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/pace/sessions", {
        method: "POST",
        body: JSON.stringify({
          line: form.line,
          operator: form.operator,
          target_rate: form.target_rate ? Number(form.target_rate) : null,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to start session");
      return res.json() as Promise<PaceSession>;
    },
    onSuccess: (s) => {
      setFormError(null);
      persist(s.id);
      qc.setQueryData(["pace-session", s.id], s);
    },
    onError: (e: Error) => setFormError(e.message),
  });

  function action(path: string, body?: unknown) {
    return apiFetch(`/pace/sessions/${sessionId}/${path}`, {
      method: "POST",
      ...(body ? { body: JSON.stringify(body) } : {}),
    }).then(async (res) => {
      if (!res.ok) throw new Error((await res.json()).detail ?? "Request failed");
      return res.json() as Promise<PaceSession>;
    });
  }

  const tap = useMutation({
    mutationFn: (kind: "good" | "reject") => action("tap", { kind }),
    onSuccess: (s) => qc.setQueryData(["pace-session", sessionId], s),
  });
  const pause = useMutation({
    mutationFn: (reason: string) => action("pause", { reason }),
    onSuccess: (s) => {
      qc.setQueryData(["pace-session", sessionId], s);
      setShowPausePicker(false);
    },
  });
  const resume = useMutation({
    mutationFn: () => action("resume"),
    onSuccess: (s) => qc.setQueryData(["pace-session", sessionId], s),
  });
  const stop = useMutation({
    mutationFn: () => action("stop"),
    onSuccess: (s) => qc.setQueryData(["pace-session", sessionId], s),
  });

  const elapsed = useElapsed(session.data?.started_at);

  if (!sessionId || (!session.data && !session.isLoading)) {
    return (
      <div className="space-y-8">
        <PageHeader
          title="Pace Tracking"
          description="No sensors, no time-tracking system - just an operator tapping a button per completed unit. Stop turns the session into a normal dataset that every OEE feature already understands."
        />
        <Card>
          <CardHeader>
            <CardTitle>Start a session</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Line">
                <Input
                  value={form.line}
                  onChange={(e) => setForm({ ...form, line: e.target.value })}
                  placeholder="Assembly Line 3"
                />
              </Field>
              <Field label="Operator">
                <Input
                  value={form.operator}
                  onChange={(e) => setForm({ ...form, operator: e.target.value })}
                  placeholder="J. Alvarez"
                />
              </Field>
              <Field label="Target rate (units/min, optional)">
                <Input
                  type="number"
                  step="any"
                  value={form.target_rate}
                  onChange={(e) => setForm({ ...form, target_rate: e.target.value })}
                />
              </Field>
            </div>
            {formError && <Alert variant="destructive">{formError}</Alert>}
            <Button disabled={!form.line || !form.operator} loading={start.isPending} onClick={() => start.mutate()}>
              Start session
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!session.data) {
    return (
      <div className="space-y-8">
        <PageHeader title="Pace Tracking" description="Loading…" />
      </div>
    );
  }

  const s = session.data;

  if (s.status === "stopped") {
    return (
      <div className="space-y-8">
        <PageHeader title="Pace Tracking" description="Session complete." />
        <Card>
          <CardHeader>
            <CardTitle>Session stopped</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {s.line} — {s.operator}: {s.good_count} good, {s.reject_count} reject.
            </p>
            <div className="flex gap-2">
              {s.dataset_id && (
                <Button onClick={() => (window.location.href = `/analytics/oee?dataset_id=${s.dataset_id}`)}>
                  View in OEE dashboard
                </Button>
              )}
              <Button variant="secondary" onClick={() => persist(null)}>
                Start another session
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Pace Tracking"
        description={`${s.line} — ${s.operator}`}
        actions={<Badge variant={s.status === "paused" ? "warning" : "success"}>{s.status}</Badge>}
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Elapsed" value={elapsed} tone="primary" />
        <StatCard label="Good" value={s.good_count} tone="success" />
        <StatCard label="Reject" value={s.reject_count} tone="warning" />
        <StatCard label="Total" value={s.good_count + s.reject_count} tone="accent" />
      </div>

      {s.status === "paused" && (
        <Alert variant="warning">Paused — {PAUSE_LABELS[s.pause_reason ?? ""] ?? s.pause_reason}</Alert>
      )}

      <div className="grid grid-cols-2 gap-4">
        <Button
          size="lg"
          className="h-24 text-xl"
          disabled={s.status !== "active"}
          loading={tap.isPending}
          onClick={() => tap.mutate("good")}
        >
          Good unit
        </Button>
        <Button
          size="lg"
          variant="destructive"
          className="h-24 text-xl"
          disabled={s.status !== "active"}
          loading={tap.isPending}
          onClick={() => tap.mutate("reject")}
        >
          Reject
        </Button>
      </div>

      <div className="flex flex-wrap gap-2">
        {s.status === "active" && (
          <Button variant="secondary" onClick={() => setShowPausePicker((v) => !v)}>
            Pause ▾
          </Button>
        )}
        {s.status === "paused" && (
          <Button variant="secondary" loading={resume.isPending} onClick={() => resume.mutate()}>
            Resume
          </Button>
        )}
        <Button variant="destructive" loading={stop.isPending} onClick={() => stop.mutate()}>
          Stop
        </Button>
      </div>

      {showPausePicker && (
        <Card>
          <CardContent className="flex flex-wrap items-end gap-3 pt-6">
            <Field label="Reason">
              <Select value={pauseReason} onChange={(e) => setPauseReason(e.target.value)}>
                <option value="breakdown">Breakdown</option>
                <option value="setup">Setup / Changeover</option>
                <option value="other">Other</option>
              </Select>
            </Field>
            <Button loading={pause.isPending} onClick={() => pause.mutate(pauseReason)}>
              Confirm pause
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add OEE deep-link pre-selection in `frontend/src/pages/analytics/shared.tsx`**

Add the import at the top of the file:

```typescript
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import * as echarts from "echarts";
```

Inside `EngineScreen`, right after the `datasets = useDatasets()` line, add a one-time effect that reads `?dataset_id=` from the URL and pre-selects it once datasets have loaded:

```typescript
  const datasets = useDatasets();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    const fromUrl = searchParams.get("dataset_id");
    if (fromUrl && datasets.data && !datasetId) pickDataset(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, datasets.data]);
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 6: Manual verification in-browser**

Start the dev server, log in, go to `/analytics/pace-tracking`:
1. Start a session (Line, Operator, no target rate).
2. Tap "Good unit" 3 times, "Reject" once — counters update live.
3. Click "Pause ▾", pick "Breakdown", confirm — banner shows "Paused — Breakdown".
4. Click "Resume" — tap buttons re-enable.
5. Repeat pause/resume for "Setup / Changeover" and "Other".
6. Click "Stop" — confirmation screen shows final counts and a "View in OEE dashboard" link.
7. Click the link — lands on `/analytics/oee?dataset_id=...` with that dataset pre-selected in the picker (no manual re-selection needed).
8. Reload the page mid-session (before Stop) — the live screen reappears with the same counts (localStorage + `GET /pace/sessions/{id}` polling survived the refresh).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/analytics/PaceTracking.tsx frontend/src/lib/nav.ts frontend/src/App.tsx \
  frontend/src/pages/analytics/shared.tsx
git commit -m "feat: add Pace Tracking page with tap buttons, pause/resume, and OEE deep-link"
```

---

## Self-Review Notes

**Spec coverage:**
- Session lifecycle (start/tap/pause/resume/stop) — Task 1.
- Multi-cycle pause accounting correctness — Task 2.
- Materialization into `Dataset` via `create_dataset` — Task 1 (`stop_session`), proven end-to-end in Task 4.
- RBAC (view/mutate org-scoped no-creator-check; discard creator-or-owner) — Task 1 (implementation) + Task 3 (tests).
- Frontend page + nav + route + OEE deep-link — Task 5.
- Out-of-scope items (cross-session history, multi-device offline queuing, tap undo) — deliberately not built; no task touches them.

**Placeholder scan:** none — every step has complete, runnable code.

**Type consistency:** `PaceSessionResponse` fields (`good_count`, `reject_count`, `unplanned_dt_min`, `planned_dt_min`, `other_dt_min`, `pause_reason`, `dataset_id`) are identical across the router (Task 1), the RBAC tests (Task 3), the OEE-integration test (Task 4), and the frontend `PaceSession` interface (Task 5).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-15-pace-tracking.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
