# Operator Identity Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the product a machine-scoped "who was at this machine, starting when" signal, fed by three tiers a customer's floor might actually have (badge/RFID reader, QR code plus phone, work-order assignment as a no-hardware fallback), behind one `resolve_attribution` query that hides which tier answered.

**Architecture:** A `CaptureEvent` table (open/close per machine, mirroring `PaceSession`'s own start/stop simplicity) fed by two public, unauthenticated endpoints - `POST /operators/capture` for a badge/RFID reader and `GET`/`POST /checkin/{machine_id}` for a QR-scanned phone page - plus a `WorkOrder.assigned_operator_id` column that participates only as a query-time fallback, never its own event row. `app.services.operator_capture.resolve_attribution(db, org_id, machine_id, window_start, window_end)` is the one call anything downstream makes. A server-rendered QR SVG (new `qrcode` dependency) on the Machines panel is what gets printed and stuck on the machine.

**Tech Stack:** FastAPI + SQLAlchemy async ORM + Alembic (backend), React + TanStack Query + Vitest render-tests (frontend), `qrcode` (new, pure-Python SVG QR rendering, no Pillow needed).

## Global Constraints

- Spec source: `docs/superpowers/specs/2026-07-29-operator-identity-capture-design.md`. Follow it exactly for the attribution model, the three tiers, and what's explicitly out of scope (reader/kiosk hardware, continuous presence tracking, the live operator board UI, retiring Pace Tracking's code).
- **Hard prerequisite:** `docs/superpowers/plans/2026-07-29-operator-registry.md` must be built and merged first. This plan imports `app.models.operator.Operator`, `app.services.registry_matching.resolve_or_create_operator`, and calls the `/operators` API - none of that exists until that plan lands. Do not start Task 1 until `from app.models.operator import Operator` succeeds and `GET /operators` returns 200 in this codebase.
- Migration chain continues from the registry plan's head revision `b3f8a2d914c7`. This plan's two migrations are `c4a9b3e825d1` (down_revision `b3f8a2d914c7`) and `d5b0c4f936e2` (down_revision `c4a9b3e825d1`).
- New dependency: `qrcode>=7.4.0`, added to `backend/requirements.txt` in Task 5. This is the one exception to this codebase's usual "no new dependencies" default - a real QR code needs Reed-Solomon encoding, not a few lines of code, and the spec explicitly calls for a "Print QR" action. `qrcode`'s bundled `qrcode.image.svg.SvgPathImage` factory needs no Pillow, so this adds exactly one package. No other new dependencies anywhere in this plan.
- `POST /operators/capture` and `GET`/`POST /checkin/{machine_id}` are deliberately unauthenticated (no `CurrentUser` dependency, not registered with any `require_feature(...)` gate). Their callers - a badge-reader edge device, an operator's own phone with no app login - have no app-user concept in this product. Org scope always comes from `machine_id` (and, for the reader, `badge_id`), never a bearer token. This mirrors the actual security model of a physical QR sticker or badge: whoever has it can act, the same posture already accepted for a printed QR code in the outside world.
- Follow existing code style: no docstrings beyond one-line rationale comments where non-obvious, `from __future__ import annotations` at the top of every backend file, org-scoped queries via `current.org_id` (or, for the two public routes, via the machine/badge lookup instead).
- Frontend: no interaction-testing library exists in this codebase (no `fireEvent`/`userEvent`/`@testing-library/react`). Every new frontend test in this plan follows the existing pattern exactly - `renderToString` + a pre-seeded `QueryClient`, asserting on the resulting HTML string. Don't introduce a different testing approach.

---

### Task 1: `CaptureEvent` model + migration

**Files:**
- Create: `backend/app/models/capture_event.py`
- Create: `backend/alembic/versions/c4a9b3e825d1_capture_events.py`
- Modify: `backend/alembic/env.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_capture_event_model.py`

**Interfaces:**
- Consumes: `Operator` (operator-registry plan), `Machine` (`app.models.machine`).
- Produces: `app.models.capture_event.CaptureEvent` with fields `id, org_id, operator_id, machine_id, source, started_at, ended_at`. Task 3's `operator_capture` service is the only later task that touches this table directly.

- [ ] **Step 1: Write the model**

```python
# backend/app/models/capture_event.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CaptureEvent(Base):
    """One row per who-was-at-this-machine signal, from a badge/RFID reader
    (source="reader") or a QR check-in tap (source="qr"). A machine has at
    most one open event (ended_at is null) at a time - the next scan on that
    machine closes it before opening its own, matching PaceSession's own
    start/stop simplicity, minus the throughput-tap machinery. See
    app.services.operator_capture. Work-order assignment (the third,
    weakest tier) produces no row here at all - see WorkOrder.assigned_operator_id
    and resolve_attribution's query-time fallback."""

    __tablename__ = "capture_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Register the model with `Base.metadata`**

In `backend/alembic/env.py`, add `capture_event` to the import list (alphabetical, between `bom` and `dataset`):

```python
from app.models import (  # noqa: F401 - registers models with Base.metadata
    alert,
    audit,
    bom,
    capture_event,
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

In `backend/tests/conftest.py`, add `capture_event` to its import list (alphabetical, between `bom` and `dashboard`):

```python
from app.models import (  # noqa: F401 - register models
    alert,
    audit,
    automl_live_binding,
    automl_run,
    bom,
    capture_event,
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

- [ ] **Step 3: Write the failing model test**

```python
# backend/tests/test_capture_event_model.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.capture_event import CaptureEvent
from app.models.machine import Machine
from app.models.operator import Operator
from app.models.work_center import WorkCenter

pytestmark = pytest.mark.asyncio


async def test_create_capture_event(db_session):
    org_id = uuid.uuid4()
    wc = WorkCenter(org_id=org_id, code="WC1", name="Line 1")
    db_session.add(wc)
    await db_session.flush()
    machine = Machine(org_id=org_id, work_center_id=wc.id, code="M1", name="Machine 1")
    db_session.add(machine)
    operator = Operator(org_id=org_id, name="J Kim", normalized_name="j kim")
    db_session.add(operator)
    await db_session.flush()

    event = CaptureEvent(
        org_id=org_id, operator_id=operator.id, machine_id=machine.id,
        source="reader", started_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    await db_session.flush()

    assert event.id is not None
    assert event.ended_at is None
    assert event.source == "reader"
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_capture_event_model.py -v`
Expected: FAIL / ERROR - `ModuleNotFoundError: No module named 'app.models.capture_event'`

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_capture_event_model.py -v`
Expected: `1 passed`

- [ ] **Step 6: Write the migration**

```python
# backend/alembic/versions/c4a9b3e825d1_capture_events.py
"""capture events

Revision ID: c4a9b3e825d1
Revises: b3f8a2d914c7
Create Date: 2026-07-29

"""
from alembic import op
import sqlalchemy as sa

revision = "c4a9b3e825d1"  # pragma: allowlist secret
down_revision = "b3f8a2d914c7"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capture_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operator_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("operators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "machine_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("machines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_capture_events_org_id", "capture_events", ["org_id"])
    op.create_index("ix_capture_events_operator_id", "capture_events", ["operator_id"])
    op.create_index("ix_capture_events_machine_id", "capture_events", ["machine_id"])


def downgrade() -> None:
    op.drop_table("capture_events")
```

- [ ] **Step 7: Verify the migration applies cleanly**

Run: `cd backend && .venv/Scripts/python.exe -m alembic upgrade head`
Expected: no errors, ends on `c4a9b3e825d1`. Confirm: `.venv/Scripts/python.exe -m alembic current` shows `c4a9b3e825d1 (head)`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/capture_event.py backend/alembic/versions/c4a9b3e825d1_capture_events.py backend/alembic/env.py backend/tests/conftest.py backend/tests/test_capture_event_model.py
git commit -m "feat: add CaptureEvent model and migration"
```

---

### Task 2: `WorkOrder.assigned_operator_id` (tier 3 fallback field)

**Files:**
- Modify: `backend/app/models/work_order.py`
- Create: `backend/alembic/versions/d5b0c4f936e2_wo_assigned_operator.py`
- Modify: `backend/app/api/routes/work_orders.py`
- Test: `backend/tests/test_work_orders.py`

**Interfaces:**
- Consumes: `Operator` (operator-registry plan), `get_or_404` (`app.api.deps`).
- Produces: `WorkOrder.assigned_operator_id: uuid.UUID | None`. `WorkOrderResponse.assigned_operator_id: str | None`. `PATCH /work-orders/{id}` accepts `assigned_operator_id` (a UUID string, or `null` to clear), settable regardless of the job's status (every other PATCH field still requires `status == "scheduled"`). Task 3's `resolve_attribution` reads this column directly.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_work_orders.py`:

```python
async def _create_operator(client, token, name="J Kim"):
    res = await client.post(
        "/operators", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_assign_operator_to_scheduled_job(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    operator = await _create_operator(client, token)

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"assigned_operator_id": operator["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] == operator["id"]


async def test_assign_operator_to_in_progress_job(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    headers = {"Authorization": f"Bearer {token}"}
    start = await client.post(f"/work-orders/{job['id']}/start", headers=headers)
    assert start.status_code == 200
    operator = await _create_operator(client, token)

    # A running job 409s on a quantity/date/notes edit, but assigning who's
    # actually running it is exactly the thing you'd want to do mid-job.
    res = await client.patch(
        f"/work-orders/{job['id']}", json={"assigned_operator_id": operator["id"]}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] == operator["id"]


async def test_editing_quantity_on_in_progress_job_still_409s(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(f"/work-orders/{job['id']}/start", headers=headers)

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "5"}, headers=headers
    )
    assert res.status_code == 409


async def test_clear_assigned_operator(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    operator = await _create_operator(client, token)
    headers = {"Authorization": f"Bearer {token}"}
    await client.patch(f"/work-orders/{job['id']}", json={"assigned_operator_id": operator["id"]}, headers=headers)

    res = await client.patch(f"/work-orders/{job['id']}", json={"assigned_operator_id": None}, headers=headers)
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] is None


async def test_assign_unknown_operator_404(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"assigned_operator_id": "00000000-0000-0000-0000-000000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_work_orders.py -k assign_operator -v`
Expected: FAIL - `assigned_operator_id` unknown field / 422, since the model column and request field don't exist yet.

- [ ] **Step 3: Add the column to the model**

In `backend/app/models/work_order.py`, add the field right after `routing_id` (which already exists at line 44-46):

```python
    routing_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("routings.id"), nullable=True
    )
    # Who's actually running this job - a plan-level fact, not an
    # observation. Nullable: most jobs never set it. Participates only in
    # resolve_attribution's fallback query (app.services.operator_capture);
    # no capture-event history of its own, because a work order's assignment
    # can change after the fact and this column has to reflect that.
    assigned_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("operators.id"), nullable=True
    )
```

- [ ] **Step 4: Write the migration**

```python
# backend/alembic/versions/d5b0c4f936e2_wo_assigned_operator.py
"""work order assigned operator

Revision ID: d5b0c4f936e2
Revises: c4a9b3e825d1
Create Date: 2026-07-29

"""
from alembic import op
import sqlalchemy as sa

revision = "d5b0c4f936e2"  # pragma: allowlist secret
down_revision = "c4a9b3e825d1"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column(
            "assigned_operator_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("operators.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("work_orders", "assigned_operator_id")
```

- [ ] **Step 5: Wire it through the API**

In `backend/app/api/routes/work_orders.py`, add the import (alphabetical, with the other model imports at the top):

```python
from app.models.operator import Operator
```

Add the field to `WorkOrderUpdateRequest` (currently at line 67-74):

```python
class WorkOrderUpdateRequest(BaseModel):
    # Every field optional: a PATCH touches only what it sends. Omitted
    # fields keep the job's current value.
    quantity_planned: Decimal | None = None
    scheduled_start: date | None = None
    location_id: str | None = None
    notes: str | None = None
    assigned_operator_id: str | None = None
```

Add the field to `WorkOrderResponse` (currently at line 95-117), right after `notes`:

```python
    notes: str | None
    assigned_operator_id: str | None
    components: list[WorkOrderComponentResponse]
```

Add it to `_wo_to_response` (currently at line 120-143), right after `notes=wo.notes,`:

```python
        notes=wo.notes,
        assigned_operator_id=str(wo.assigned_operator_id) if wo.assigned_operator_id else None,
        components=components,
```

Replace the whole `update_work_order` function (currently lines 778-861) with:

```python
@router.patch("/{wo_id}", response_model=WorkOrderResponse)
async def update_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderUpdateRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    # Locked like start, finish and cancel are, and for a sharper reason than
    # any of them: this path DELETEs and re-inserts the component rows, so an
    # edit racing a start would reset quantity_consumed to 0 on a job whose
    # stock has already left the shelf, and the later return would put nothing
    # back.
    wo = await get_or_404(
        db, WorkOrder, current.org_id, wo_id, "Work order not found", for_update=True
    )
    supplied = body.model_fields_set

    # Who's actually running the job is a plan-level fact, not a
    # component/quantity edit - it can change on a job that's already
    # started (that's exactly when you'd correct or set it), so it's applied
    # independently of the scheduled-only guard below.
    if "assigned_operator_id" in supplied:
        if body.assigned_operator_id is not None:
            try:
                operator_id = uuid.UUID(body.assigned_operator_id)
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid assigned_operator_id") from None
            await get_or_404(db, Operator, current.org_id, operator_id, "Operator not found")
            wo.assigned_operator_id = operator_id
        else:
            wo.assigned_operator_id = None

    if wo.status != "scheduled":
        # A started job's components are already being consumed on the floor;
        # editing the recipe or quantity under it would rewrite what already
        # happened. assigned_operator_id doesn't touch components, so it's
        # exempt from this guard (handled above).
        if supplied - {"assigned_operator_id"}:
            raise HTTPException(status.HTTP_409_CONFLICT, f"job {wo.wo_number} is {wo.status}")
        await db.commit()
        await db.refresh(wo)
        item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
        location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
        return await _detail_response(db, wo, item, location)

    quantity_planned = wo.quantity_planned
    if body.quantity_planned is not None:
        _validate_quantity(body.quantity_planned)
        # Match the Numeric(12, 4) column so the response reflects what gets
        # stored, not the raw decimal places the caller happened to send.
        quantity_planned = body.quantity_planned.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    # A job reflects the recipe as of its LAST edit, not the one it was
    # raised on: if the BOM was wrong and has since been fixed, editing
    # picks the fix up.
    bom_id = await active_bom_id(db, current.org_id, wo.item_id)
    if bom_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{item.sku} has no active recipe")

    # Which keys the caller SENT, not which came out non-null: null is how the
    # UI clears a date or a note, and reading it as "field omitted" meant
    # clearing a start date silently did nothing, on the very field that drives
    # reservation priority, the horizon bound and the Late badge.
    if body.location_id is not None:
        wo.location_id = await resolve_location_id(db, current.org_id, body.location_id)
    if "scheduled_start" in supplied:
        wo.scheduled_start = body.scheduled_start
    if "notes" in supplied:
        wo.notes = body.notes
    wo.quantity_planned = quantity_planned
    wo.bom_id = bom_id

    # Delete and re-insert every time, even when only the date changed: it is
    # one query either way, and a conditional here is exactly how the stored
    # components and the recipe drift apart.
    requirements = await component_requirements(db, current.org_id, wo.item_id, quantity_planned)
    await db.execute(
        WorkOrderComponent.__table__.delete().where(WorkOrderComponent.wo_id == wo.id)
    )
    for sequence, (component_item_id, quantity_required) in enumerate(requirements):
        db.add(
            WorkOrderComponent(
                org_id=current.org_id,
                wo_id=wo.id,
                item_id=component_item_id,
                quantity_required=quantity_required,
                sequence=sequence,
            )
        )

    # Child rows changing alone never fires onupdate on the parent; touch it
    # so updated_at still means "last changed".
    wo.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await log_event(
        db,
        action="work_order.updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(wo.id),
        metadata={"wo_number": wo.wo_number},
    )
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)
```

- [ ] **Step 6: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_work_orders.py -v`
Expected: all pass, including the 5 new tests.

- [ ] **Step 7: Verify the migration applies cleanly**

Run: `cd backend && .venv/Scripts/python.exe -m alembic upgrade head`
Expected: ends on `d5b0c4f936e2`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/work_order.py backend/alembic/versions/d5b0c4f936e2_wo_assigned_operator.py backend/app/api/routes/work_orders.py backend/tests/test_work_orders.py
git commit -m "feat: add WorkOrder.assigned_operator_id as the tier-3 attribution fallback"
```

---

### Task 3: `operator_capture` service - `open_capture_event` + `resolve_attribution`

**Files:**
- Create: `backend/app/services/operator_capture.py`
- Test: `backend/tests/test_operator_capture_service.py`

**Interfaces:**
- Consumes: `CaptureEvent` (Task 1), `WorkOrder.assigned_operator_id` (Task 2), `Operator`/`Machine`/`Routing`/`RoutingStep` (existing + registry plan).
- Produces: `open_capture_event(db, org_id, operator_id, machine_id, source, scanned_at) -> CaptureEvent` and `resolve_attribution(db, org_id, machine_id, window_start, window_end) -> Operator | None`. Task 4's public routes call `open_capture_event`; nothing in this plan calls `resolve_attribution` yet (the live operator board that will is its own, deferred spec) - it's built and tested here because the spec requires it to exist and be correct now.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_operator_capture_service.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.machine import Machine
from app.models.operator import Operator
from app.models.routing import Routing, RoutingStep
from app.models.work_center import WorkCenter
from app.models.work_order import WorkOrder
from app.services.operator_capture import open_capture_event, resolve_attribution

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)


async def _seed_machine(db_session, org_id):
    wc = WorkCenter(org_id=org_id, code="WC1", name="Line 1")
    db_session.add(wc)
    await db_session.flush()
    machine = Machine(org_id=org_id, work_center_id=wc.id, code="M1", name="Machine 1")
    db_session.add(machine)
    await db_session.flush()
    return machine, wc


async def _seed_operator(db_session, org_id, name):
    operator = Operator(org_id=org_id, name=name, normalized_name=name.casefold())
    db_session.add(operator)
    await db_session.flush()
    return operator


async def _seed_in_progress_work_order(db_session, org_id, work_center_id, operator_id):
    from app.models.bom import Item
    from app.models.location import Location

    item = Item(org_id=org_id, sku="SKU1", name="Widget", item_type="finished_good", unit_of_measure="ea")
    db_session.add(item)
    location = Location(org_id=org_id, code="MAIN", name="Main")
    db_session.add(location)
    await db_session.flush()

    routing = Routing(org_id=org_id, item_id=item.id, version=1, status="active")
    db_session.add(routing)
    await db_session.flush()
    db_session.add(RoutingStep(org_id=org_id, routing_id=routing.id, sequence=1, work_center_id=work_center_id))

    wo = WorkOrder(
        org_id=org_id, item_id=item.id, bom_id=uuid.uuid4(), routing_id=routing.id,
        wo_number=1, status="in_progress", quantity_planned=Decimal("10"),
        location_id=location.id, started_at=NOW - timedelta(minutes=30),
        assigned_operator_id=operator_id, created_by=uuid.uuid4(),
    )
    db_session.add(wo)
    await db_session.flush()
    return wo


async def test_open_capture_event_closes_prior_open_event_on_same_machine(db_session):
    org_id = uuid.uuid4()
    machine, _ = await _seed_machine(db_session, org_id)
    a = await _seed_operator(db_session, org_id, "A")
    b = await _seed_operator(db_session, org_id, "B")

    first = await open_capture_event(
        db_session, org_id=org_id, operator_id=a.id, machine_id=machine.id,
        source="reader", scanned_at=NOW,
    )
    second_time = NOW + timedelta(minutes=5)
    second = await open_capture_event(
        db_session, org_id=org_id, operator_id=b.id, machine_id=machine.id,
        source="reader", scanned_at=second_time,
    )

    await db_session.refresh(first)
    assert first.ended_at == second_time
    assert second.ended_at is None


async def test_resolve_attribution_prefers_capture_event_over_work_order(db_session):
    org_id = uuid.uuid4()
    machine, wc = await _seed_machine(db_session, org_id)
    scanned = await _seed_operator(db_session, org_id, "Scanned")
    scheduled = await _seed_operator(db_session, org_id, "Scheduled")

    await open_capture_event(
        db_session, org_id=org_id, operator_id=scanned.id, machine_id=machine.id,
        source="reader", scanned_at=NOW,
    )
    await _seed_in_progress_work_order(db_session, org_id, wc.id, scheduled.id)

    result = await resolve_attribution(
        db_session, org_id=org_id, machine_id=machine.id,
        window_start=NOW, window_end=NOW + timedelta(minutes=1),
    )
    assert result is not None
    assert result.id == scanned.id


async def test_resolve_attribution_falls_back_to_work_order_assignment(db_session):
    org_id = uuid.uuid4()
    machine, wc = await _seed_machine(db_session, org_id)
    scheduled = await _seed_operator(db_session, org_id, "Scheduled")
    await _seed_in_progress_work_order(db_session, org_id, wc.id, scheduled.id)

    result = await resolve_attribution(
        db_session, org_id=org_id, machine_id=machine.id,
        window_start=NOW, window_end=NOW + timedelta(minutes=1),
    )
    assert result is not None
    assert result.id == scheduled.id


async def test_resolve_attribution_returns_none_when_neither_exists(db_session):
    org_id = uuid.uuid4()
    machine, _ = await _seed_machine(db_session, org_id)

    result = await resolve_attribution(
        db_session, org_id=org_id, machine_id=machine.id,
        window_start=NOW, window_end=NOW + timedelta(minutes=1),
    )
    assert result is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_operator_capture_service.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'app.services.operator_capture'`

- [ ] **Step 3: Implement the service**

```python
# backend/app/services/operator_capture.py
"""Who was at a machine, per the identity-capture spec's attribution model.
open_capture_event is what both public capture routes
(app.api.routes.operator_capture) call after resolving an operator, whichever
tier they came from. resolve_attribution is the one read anything downstream
(the live operator board, a future sensor-driven OEE-by-operator view) calls,
so it never has to know which tier answered."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capture_event import CaptureEvent
from app.models.machine import Machine
from app.models.operator import Operator
from app.models.routing import RoutingStep
from app.models.work_order import WorkOrder


async def open_capture_event(
    db: AsyncSession,
    org_id: uuid.UUID,
    operator_id: uuid.UUID,
    machine_id: uuid.UUID,
    source: str,
    scanned_at: datetime,
) -> CaptureEvent:
    """Closes whatever capture event is still open on this machine, then
    opens a new one - a machine has at most one open event at a time."""
    open_event = (
        await db.execute(
            select(CaptureEvent).where(
                CaptureEvent.org_id == org_id,
                CaptureEvent.machine_id == machine_id,
                CaptureEvent.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if open_event is not None:
        open_event.ended_at = scanned_at

    event = CaptureEvent(
        org_id=org_id, operator_id=operator_id, machine_id=machine_id,
        source=source, started_at=scanned_at,
    )
    db.add(event)
    await db.flush()
    return event


async def resolve_attribution(
    db: AsyncSession,
    org_id: uuid.UUID,
    machine_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
) -> Operator | None:
    """1) An overlapping capture event (reader or QR) beats everything else -
    a real scan. 2) Otherwise, the operator assigned to whichever in-progress
    work order's routing step uses this machine's work center during the
    window - a schedule, not an observation. 3) Otherwise None: the
    dimension is simply unpopulated for that window, the same
    graceful-absence behaviour every other optional column here already has."""
    event = (
        await db.execute(
            select(CaptureEvent)
            .where(
                CaptureEvent.org_id == org_id,
                CaptureEvent.machine_id == machine_id,
                CaptureEvent.started_at < window_end,
                or_(CaptureEvent.ended_at.is_(None), CaptureEvent.ended_at > window_start),
            )
            .order_by(CaptureEvent.started_at.desc())
        )
    ).scalars().first()
    if event is not None:
        return await db.get(Operator, event.operator_id)

    machine = await db.get(Machine, machine_id)
    if machine is None:
        return None

    wo = (
        await db.execute(
            select(WorkOrder)
            .join(RoutingStep, RoutingStep.routing_id == WorkOrder.routing_id)
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.status == "in_progress",
                WorkOrder.assigned_operator_id.is_not(None),
                RoutingStep.work_center_id == machine.work_center_id,
                WorkOrder.started_at < window_end,
                or_(WorkOrder.finished_at.is_(None), WorkOrder.finished_at > window_start),
            )
        )
    ).scalars().first()
    if wo is None or wo.assigned_operator_id is None:
        return None
    return await db.get(Operator, wo.assigned_operator_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_operator_capture_service.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/operator_capture.py backend/tests/test_operator_capture_service.py
git commit -m "feat: add open_capture_event and resolve_attribution"
```

---

### Task 4: Public capture routes - `POST /operators/capture` (tier 1) and `GET`/`POST /checkin/{machine_id}` (tier 2)

**Files:**
- Create: `backend/app/api/routes/operator_capture.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_operator_capture_routes.py`

**Interfaces:**
- Consumes: `open_capture_event` (Task 3), `Machine`, `Operator`, `resolve_or_create_operator` (registry plan's `app.services.registry_matching`).
- Produces: `POST /operators/capture` (`{badge_id, machine_id, scanned_at?}` -> `CaptureResponse`), `GET /checkin/{machine_id}` (-> roster), `POST /checkin/{machine_id}` (`{operator_id}` or `{name}` -> `CaptureResponse`). `CaptureResponse`: `{operator_id, operator_name, machine_id, started_at}`. Task 6's frontend check-in page consumes the `GET`/`POST /checkin/{machine_id}` pair.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_operator_capture_routes.py
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _seed_machine_and_operator(client, token, badge_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    wc = (await client.post("/work-centers", json={"code": "WC1", "name": "Line 1"}, headers=headers)).json()
    machine = (
        await client.post(
            "/machines", json={"work_center_id": wc["id"], "code": "M1", "name": "Machine 1"}, headers=headers
        )
    ).json()
    operator = (
        await client.post("/operators", json={"name": "J Kim", "badge_id": badge_id}, headers=headers)
    ).json()
    return machine, operator


async def test_badge_scan_creates_capture_event(client):
    token = await _register_and_login(client)
    machine, operator = await _seed_machine_and_operator(client, token, badge_id="B1")

    res = await client.post("/operators/capture", json={"badge_id": "B1", "machine_id": machine["id"]})
    assert res.status_code == 201
    body = res.json()
    assert body["operator_id"] == operator["id"]
    assert body["operator_name"] == "J Kim"


async def test_badge_scan_unrecognized_badge_404(client):
    token = await _register_and_login(client)
    machine, _ = await _seed_machine_and_operator(client, token, badge_id="B1")

    res = await client.post("/operators/capture", json={"badge_id": "NOPE", "machine_id": machine["id"]})
    assert res.status_code == 404


async def test_badge_scan_unknown_machine_404(client):
    token = await _register_and_login(client)
    await _seed_machine_and_operator(client, token, badge_id="B1")

    res = await client.post("/operators/capture", json={"badge_id": "B1", "machine_id": str(uuid.uuid4())})
    assert res.status_code == 404


async def test_badge_id_scoped_per_org(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="Acme")
    machine_a, _ = await _seed_machine_and_operator(client, token_a, badge_id="B1")

    token_b = await _register_and_login(client, email="b@example.com", org_name="Zenith")
    await _seed_machine_and_operator(client, token_b, badge_id="B1")

    # B1 exists in both orgs (allowed - badge_id is unique per org, not
    # globally). Scanning it against org A's machine must resolve org A's
    # operator, never leak into org B's roster.
    res = await client.post("/operators/capture", json={"badge_id": "B1", "machine_id": machine_a["id"]})
    assert res.status_code == 201


async def test_checkin_roster_lists_operators(client):
    token = await _register_and_login(client)
    machine, operator = await _seed_machine_and_operator(client, token)

    res = await client.get(f"/checkin/{machine['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["machine_name"] == "Machine 1"
    assert [o["id"] for o in body["operators"]] == [operator["id"]]


async def test_checkin_unknown_machine_404(client):
    res = await client.get(f"/checkin/{uuid.uuid4()}")
    assert res.status_code == 404


async def test_checkin_with_existing_operator_id(client):
    token = await _register_and_login(client)
    machine, operator = await _seed_machine_and_operator(client, token)

    res = await client.post(f"/checkin/{machine['id']}", json={"operator_id": operator["id"]})
    assert res.status_code == 201
    assert res.json()["operator_id"] == operator["id"]


async def test_checkin_with_new_name_creates_operator(client):
    token = await _register_and_login(client)
    machine, _ = await _seed_machine_and_operator(client, token)

    res = await client.post(f"/checkin/{machine['id']}", json={"name": "New Hire"})
    assert res.status_code == 201
    assert res.json()["operator_name"] == "New Hire"

    roster = await client.get(f"/checkin/{machine['id']}")
    assert "New Hire" in [o["name"] for o in roster.json()["operators"]]


async def test_checkin_requires_operator_id_or_name(client):
    token = await _register_and_login(client)
    machine, _ = await _seed_machine_and_operator(client, token)

    res = await client.post(f"/checkin/{machine['id']}", json={})
    assert res.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_operator_capture_routes.py -v`
Expected: FAIL - `404 Not Found` on every request (routes don't exist yet)

- [ ] **Step 3: Write the routes**

```python
# backend/app/api/routes/operator_capture.py
"""Public, unauthenticated ingestion for who's-at-the-machine signals: a
badge/RFID reader posts to /operators/capture directly, and the QR check-in
page (its own public frontend route, see frontend/src/pages/CheckIn.tsx)
calls /checkin/{machine_id}. Neither actor has an app login - see the
identity-capture spec's Tier 1/2 sections. Org scope always comes from
machine_id/badge_id, never a bearer token."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.machine import Machine
from app.models.operator import Operator
from app.services.operator_capture import open_capture_event
from app.services.registry_matching import resolve_or_create_operator

router = APIRouter(tags=["operator-capture"])


class CaptureRequest(BaseModel):
    badge_id: str
    machine_id: uuid.UUID
    scanned_at: datetime | None = None


class CaptureResponse(BaseModel):
    operator_id: str
    operator_name: str
    machine_id: str
    started_at: str


class RosterOperatorResponse(BaseModel):
    id: str
    name: str


class CheckinRosterResponse(BaseModel):
    machine_id: str
    machine_name: str
    operators: list[RosterOperatorResponse]


class CheckinRequest(BaseModel):
    operator_id: uuid.UUID | None = None
    name: str | None = None


async def _machine_or_404(db: AsyncSession, machine_id: uuid.UUID) -> Machine:
    machine = await db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine not found")
    return machine


@router.post("/operators/capture", response_model=CaptureResponse, status_code=status.HTTP_201_CREATED)
async def capture_badge_scan(body: CaptureRequest, db: AsyncSession = Depends(get_db)):
    machine = await _machine_or_404(db, body.machine_id)
    operator = (
        await db.execute(
            select(Operator).where(Operator.org_id == machine.org_id, Operator.badge_id == body.badge_id)
        )
    ).scalar_one_or_none()
    if operator is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "badge not recognized")

    scanned_at = body.scanned_at or datetime.now(timezone.utc)
    event = await open_capture_event(
        db, org_id=machine.org_id, operator_id=operator.id, machine_id=machine.id,
        source="reader", scanned_at=scanned_at,
    )
    await db.commit()
    return CaptureResponse(
        operator_id=str(operator.id), operator_name=operator.name,
        machine_id=str(machine.id), started_at=event.started_at.isoformat(),
    )


@router.get("/checkin/{machine_id}", response_model=CheckinRosterResponse)
async def checkin_roster(machine_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    machine = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(Operator)
            .where(Operator.org_id == machine.org_id, Operator.status == "active")
            .order_by(Operator.name)
        )
    ).scalars().all()
    return CheckinRosterResponse(
        machine_id=str(machine.id),
        machine_name=machine.name,
        operators=[RosterOperatorResponse(id=str(o.id), name=o.name) for o in rows],
    )


@router.post("/checkin/{machine_id}", response_model=CaptureResponse, status_code=status.HTTP_201_CREATED)
async def checkin_scan(machine_id: uuid.UUID, body: CheckinRequest, db: AsyncSession = Depends(get_db)):
    machine = await _machine_or_404(db, machine_id)
    if body.operator_id is not None:
        operator = (
            await db.execute(
                select(Operator).where(Operator.org_id == machine.org_id, Operator.id == body.operator_id)
            )
        ).scalar_one_or_none()
        if operator is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Operator not found")
    elif body.name:
        # Not on the roster from this screen is allowed - a new hire's first
        # scan creates the roster row, exactly as dataset auto-discovery does.
        operator = await resolve_or_create_operator(db, machine.org_id, body.name)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "operator_id or name is required")

    scanned_at = datetime.now(timezone.utc)
    event = await open_capture_event(
        db, org_id=machine.org_id, operator_id=operator.id, machine_id=machine.id,
        source="qr", scanned_at=scanned_at,
    )
    await db.commit()
    return CaptureResponse(
        operator_id=str(operator.id), operator_name=operator.name,
        machine_id=str(machine.id), started_at=event.started_at.isoformat(),
    )
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, add `operator_capture` to the import list, immediately before `operators` (wherever the operator-registry plan placed that import):

```python
    operator_capture,
    operators,
```

Add the include, immediately after `app.include_router(operators.router)` (wherever the operator-registry plan placed that line). No `require_feature(...)` gate, same as `machines.router` and `operators.router` - and these two routes have no `CurrentUser` at all, so a feature-gate dependency (which itself requires a logged-in user) would break public access entirely:

```python
app.include_router(operators.router)
app.include_router(operator_capture.router)
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_operator_capture_routes.py -v`
Expected: `9 passed`

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass, no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/operator_capture.py backend/app/main.py backend/tests/test_operator_capture_routes.py
git commit -m "feat: add public badge-scan and QR check-in capture routes"
```

---

### Task 5: Printable QR check-in code on the Machines panel

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/api/routes/machines.py`
- Test: `backend/tests/test_machines.py`

**Interfaces:**
- Consumes: `settings.frontend_base_url` (`app.core.config`), `qrcode` (new dependency).
- Produces: `GET /machines/{machine_id}/checkin-qr` -> `image/svg+xml` body encoding `{frontend_base_url}/checkin/{machine_id}`. Task 7's frontend "Print QR" button fetches this.

- [ ] **Step 1: Add the dependency**

In `backend/requirements.txt`, add near the other small single-purpose additions (e.g. after the `reportlab` line):

```
# QR code image for the operator identity-capture check-in page - pure
# Python, the bundled SVG factory needs no Pillow.
qrcode>=7.4.0
```

Run: `cd backend && .venv/Scripts/python.exe -m pip install qrcode>=7.4.0`

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_machines.py` (add `import uuid` at the top of the file alongside the existing `import pytest` if not already present):

```python
async def test_checkin_qr_svg(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    wc = await _create_work_center(client, token)
    machine = (
        await client.post(
            "/machines", json={"work_center_id": wc["id"], "code": "M1", "name": "Machine 1"}, headers=headers
        )
    ).json()

    res = await client.get(f"/machines/{machine['id']}/checkin-qr", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in res.content


async def test_checkin_qr_unknown_machine_404(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get(f"/machines/{uuid.uuid4()}/checkin-qr", headers=headers)
    assert res.status_code == 404
```

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_machines.py -k checkin_qr -v`
Expected: FAIL - `404 Not Found` (route doesn't exist yet)

- [ ] **Step 4: Add the route**

In `backend/app/api/routes/machines.py`, add the imports at the top (alongside the existing ones):

```python
import io

import qrcode
import qrcode.image.svg
from fastapi.responses import Response

from app.core.config import get_settings
```

Add the route at the end of the file:

```python
@router.get("/{machine_id}/checkin-qr")
async def machine_checkin_qr(
    machine_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SVG QR code encoding this machine's public check-in URL - see
    app.api.routes.operator_capture for what scanning it does. Generated
    server-side so printing it needs no frontend QR dependency."""
    await get_or_404(db, Machine, current.org_id, machine_id, "Machine not found")
    url = f"{get_settings().frontend_base_url}/checkin/{machine_id}"
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return Response(content=buffer.getvalue(), media_type="image/svg+xml")
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_machines.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/api/routes/machines.py backend/tests/test_machines.py
git commit -m "feat: add printable QR check-in code endpoint for machines"
```

---

### Task 6: Frontend check-in page (`/checkin/:machineId`, public)

**Files:**
- Create: `frontend/src/pages/CheckIn.tsx`
- Create: `frontend/src/pages/CheckIn.render.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `GET`/`POST /checkin/{machine_id}` (Task 4). `AuthLayout` (`@/components/AuthLayout`) for the public page shell.
- Produces: The `CheckIn` component, routed publicly (outside `RequireAuth`) at `/checkin/:machineId`.

- [ ] **Step 1: Write the component**

```tsx
// frontend/src/pages/CheckIn.tsx
import { FormEvent, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

interface RosterOperator {
  id: string;
  name: string;
}

interface RosterResponse {
  machine_id: string;
  machine_name: string;
  operators: RosterOperator[];
}

interface CaptureResponse {
  operator_id: string;
  operator_name: string;
}

function rememberedKey(machineId: string) {
  return `evowerk:checkin:${machineId}`;
}

function readRemembered(machineId: string): RosterOperator | null {
  try {
    const raw = localStorage.getItem(rememberedKey(machineId));
    return raw ? (JSON.parse(raw) as RosterOperator) : null;
  } catch {
    return null;
  }
}

export function CheckIn() {
  const { machineId = "" } = useParams();
  const remembered = readRemembered(machineId);
  const [showList, setShowList] = useState(!remembered);
  const [newName, setNewName] = useState("");
  const [confirmed, setConfirmed] = useState<string | null>(null);

  const roster = useQuery<RosterResponse>({
    queryKey: ["checkin-roster", machineId],
    queryFn: async () => {
      const res = await apiFetch(`/checkin/${machineId}`);
      if (!res.ok) throw new Error("This QR code isn't linked to a machine anymore.");
      return res.json();
    },
  });

  const checkIn = useMutation({
    mutationFn: async (body: { operator_id?: string; name?: string }) => {
      const res = await apiFetch(`/checkin/${machineId}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error("Check-in failed. Try again.");
      return res.json() as Promise<CaptureResponse>;
    },
    onSuccess: (data) => {
      localStorage.setItem(
        rememberedKey(machineId),
        JSON.stringify({ id: data.operator_id, name: data.operator_name }),
      );
      setConfirmed(data.operator_name);
    },
  });

  function onAddName(e: FormEvent) {
    e.preventDefault();
    if (newName.trim()) checkIn.mutate({ name: newName.trim() });
  }

  if (confirmed) {
    return (
      <AuthLayout title="You're checked in" subtitle={roster.data?.machine_name}>
        <Alert variant="success">Checked in as {confirmed}. You're all set.</Alert>
        <Button className="mt-4 w-full" variant="secondary" onClick={() => setConfirmed(null)}>
          Not you? Check in again
        </Button>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={roster.data ? `Check in at ${roster.data.machine_name}` : "Check in"}
      subtitle="Tap your name to start this machine under your name."
    >
      {roster.isError && <Alert variant="destructive">{(roster.error as Error).message}</Alert>}
      {checkIn.isError && (
        <Alert variant="destructive" className="mb-4">
          {(checkIn.error as Error).message}
        </Alert>
      )}

      {remembered && !showList && (
        <div className="space-y-3">
          <Button
            className="w-full"
            loading={checkIn.isPending}
            onClick={() => checkIn.mutate({ operator_id: remembered.id })}
          >
            Continue as {remembered.name}
          </Button>
          <button
            type="button"
            onClick={() => setShowList(true)}
            className="block w-full text-center text-xs font-medium text-primary hover:underline"
          >
            Not you? Choose from the list
          </button>
        </div>
      )}

      {(!remembered || showList) && (
        <div className="space-y-4">
          <div className="space-y-2">
            {roster.data?.operators.map((o) => (
              <Button
                key={o.id}
                variant="outline"
                className="w-full justify-start"
                loading={checkIn.isPending}
                onClick={() => checkIn.mutate({ operator_id: o.id })}
              >
                {o.name}
              </Button>
            ))}
            {roster.data && roster.data.operators.length === 0 && (
              <p className="text-sm text-muted-foreground">No one on the roster yet - add your name below.</p>
            )}
          </div>
          <form onSubmit={onAddName} className="flex gap-2">
            <Field label="Not on the list?" htmlFor="checkin-new-name" className="flex-1">
              <Input
                id="checkin-new-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Type your name"
              />
            </Field>
            <Button type="submit" variant="secondary" loading={checkIn.isPending} className="self-end">
              Add
            </Button>
          </form>
        </div>
      )}
    </AuthLayout>
  );
}
```

- [ ] **Step 2: Write the failing render test**

```tsx
// frontend/src/pages/CheckIn.render.test.tsx
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CheckIn } from "./CheckIn";

function renderCheckIn(machineId: string, seed: (qc: QueryClient) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  return renderToString(
    createElement(
      MemoryRouter,
      { initialEntries: [`/checkin/${machineId}`] },
      createElement(
        QueryClientProvider,
        { client: qc },
        createElement(
          Routes,
          null,
          createElement(Route, { path: "/checkin/:machineId", element: createElement(CheckIn) }),
        ),
      ),
    ),
  ).split("<!-- -->").join("");
}

describe("CheckIn", () => {
  it("lists the roster to tap when there's no remembered operator", () => {
    const html = renderCheckIn("machine-1", (qc) =>
      qc.setQueryData(["checkin-roster", "machine-1"], {
        machine_id: "machine-1",
        machine_name: "CNC 01",
        operators: [{ id: "op-1", name: "J Kim" }],
      }),
    );
    expect(html).toContain("CNC 01");
    expect(html).toContain("J Kim");
    expect(html).toContain("Not on the list?");
  });

  it("shows the empty-roster message when there are no operators yet", () => {
    const html = renderCheckIn("machine-1", (qc) =>
      qc.setQueryData(["checkin-roster", "machine-1"], {
        machine_id: "machine-1",
        machine_name: "CNC 01",
        operators: [],
      }),
    );
    expect(html).toContain("No one on the roster yet");
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd frontend && npm run test -- CheckIn.render`
Expected: FAIL - `Cannot find module './CheckIn'`

- [ ] **Step 4: Wire the public route**

In `frontend/src/App.tsx`, add the lazy import (alongside the other `lazyPage` calls):

```typescript
const CheckIn = lazyPage(() => import("./pages/CheckIn"), "CheckIn");
```

Add the public route, right after the `/register` route (before `RequireAuth`-wrapped routes):

```tsx
          <Route path="/register" element={<Register />} />
          <Route path="/checkin/:machineId" element={<CheckIn />} />
```

- [ ] **Step 5: Run to verify pass**

Run: `cd frontend && npm run test -- CheckIn.render`
Expected: both tests pass.

- [ ] **Step 6: Run the full frontend test suite to check for regressions**

Run: `cd frontend && npm run test`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/CheckIn.tsx frontend/src/pages/CheckIn.render.test.tsx frontend/src/App.tsx
git commit -m "feat: add public QR check-in page"
```

---

### Task 7: "Print QR" action on the Machines panel

**Files:**
- Modify: `frontend/src/pages/Production.tsx`
- Modify: `frontend/src/pages/Production.render.test.tsx`

**Interfaces:**
- Consumes: `GET /machines/{machine_id}/checkin-qr` (Task 5).
- Produces: A "Print QR" button per row in `MachinesPanel`'s table.

- [ ] **Step 1: Write the failing render test**

Add to `frontend/src/pages/Production.render.test.tsx`, inside the `describe("Production (setup tabs)", ...)` block:

```typescript
  it("machines tab shows a Print QR action per row", () => {
    const html = renderSetup("machines", (qc) =>
      qc.setQueryData(
        ["machines"],
        [
          {
            id: "m-1", work_center_id: "wc-1", code: "CNC-01", name: "CNC 01",
            status: "active", mtbf_hours: null, mttr_hours: null, needs_review: false,
          },
        ],
      ),
    );
    expect(html).toContain("Print QR");
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm run test -- Production.render`
Expected: FAIL - "Print QR" not found

- [ ] **Step 3: Add the button**

In `frontend/src/pages/Production.tsx`, add a `printQr` helper right above `MachinesPanel` (which currently starts at line 249):

```typescript
// Same pattern ReportBuilder.tsx uses for an authenticated file download: open
// the tab synchronously (so the popup blocker doesn't see an async click),
// then point it at the blob once apiFetch's Authorization header has done its
// job - an <img src> can't carry that header on its own.
async function printQr(machineId: string) {
  const tab = window.open("", "_blank");
  const res = await apiFetch(`/machines/${machineId}/checkin-qr`);
  if (!res.ok) {
    tab?.close();
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  if (tab) tab.location.href = url;
  else window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
```

Add a `QR` column to the machines table (currently lines 384-412): the header row gains a `<TH>QR</TH>` after `<TH>Review</TH>`, and each row gains a matching `<TD>`:

```tsx
            <THead>
              <TR>
                <TH>Code</TH>
                <TH>Name</TH>
                <TH>Work center</TH>
                <TH>Status</TH>
                <TH>MTBF (h)</TH>
                <TH>MTTR (h)</TH>
                <TH>Review</TH>
                <TH>QR</TH>
              </TR>
            </THead>
            <TBody>
              {machines.data?.map((m) => (
                <TR key={m.id}>
                  <TD className="font-medium">{m.code}</TD>
                  <TD>{m.name}</TD>
                  <TD className="text-muted-foreground">{workCenterLabel(m.work_center_id)}</TD>
                  <TD>
                    <Badge variant={statusVariant(m.status)}>{humanize(m.status)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{m.mtbf_hours ?? "-"}</TD>
                  <TD className="text-muted-foreground">{m.mttr_hours ?? "-"}</TD>
                  <TD>{m.needs_review ? <Badge variant="warning">Needs review</Badge> : ""}</TD>
                  <TD>
                    <Button size="sm" variant="outline" onClick={() => printQr(m.id)}>
                      Print QR
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm run test -- Production.render`
Expected: all tests pass.

- [ ] **Step 5: Run the full frontend test suite to check for regressions**

Run: `cd frontend && npm run test`
Expected: all tests pass.

- [ ] **Step 6: Manually verify in the browser**

Start the dev servers (backend + frontend), open Production > Machines, click "Print QR" on a machine, confirm a new tab opens showing an SVG QR code. Scan it with a phone (or paste the encoded URL manually) and confirm it lands on `/checkin/{machine_id}` and shows the roster. Take a screenshot as proof.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/Production.tsx frontend/src/pages/Production.render.test.tsx
git commit -m "feat: add Print QR action to the Machines panel"
```

---

### Task 8: Operator assignment on work orders (tier 3 UI)

**Files:**
- Modify: `frontend/src/pages/production/types.ts`
- Modify: `frontend/src/pages/production/JobsPanel.tsx`
- Create: `frontend/src/pages/production/JobsPanel.render.test.tsx`

**Interfaces:**
- Consumes: `PATCH /work-orders/{id}` with `assigned_operator_id` (Task 2), `GET /operators` (registry plan).
- Produces: An "Assigned operator" field in the job Edit dialog, editable on both scheduled and in-progress jobs.

- [ ] **Step 1: Add the field to the `WorkOrder` type**

In `frontend/src/pages/production/types.ts`, add `assigned_operator_id` right after `notes`:

```typescript
  notes: string | null;
  assigned_operator_id: string | null;
  components: WorkOrderComponent[];
```

- [ ] **Step 2: Write the failing render test**

```tsx
// frontend/src/pages/production/JobsPanel.render.test.tsx
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobsPanel } from "./JobsPanel";

const RUNNING = {
  id: "wo-2", wo_number: 5, item_id: "item-1", item_sku: "PUMP-1", item_name: "Coolant pump",
  item_is_lot_tracked: false, status: "in_progress", quantity_planned: "50.0000",
  quantity_good: "0.0000", quantity_scrapped: "0.0000", scrap_reason: null,
  remainder_disposition: null, location_id: "loc-1", location_code: "MAIN",
  scheduled_start: null, started_at: "2026-07-27T08:00:00+00:00", finished_at: null,
  notes: null, assigned_operator_id: null, components: [],
};

function renderJobs(seed: (qc: QueryClient) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["org-me"], { id: "u-1", name: "Owner", role: "owner", user_id: "u-1", enabled_features: ["production"] });
  seed(qc);
  return renderToString(
    createElement(MemoryRouter, null, createElement(QueryClientProvider, { client: qc }, createElement(JobsPanel))),
  ).split("<!-- -->").join("");
}

describe("JobsPanel", () => {
  it("shows an Edit button for a running job, not just a scheduled one", () => {
    const html = renderJobs((qc) => qc.setQueryData(["work-orders", "", ""], [RUNNING]));
    expect(html).toContain("Edit");
    expect(html).toContain("Finish");
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd frontend && npm run test -- JobsPanel.render`
Expected: FAIL - "Edit" not found for the running job (the button is currently only rendered for `status === "scheduled"`)

- [ ] **Step 4: Show Edit for in-progress jobs too**

In `frontend/src/pages/production/JobsPanel.tsx`, replace `JobRow`'s action cell (currently lines 274-297):

```tsx
      <TD className="text-right">
        {canEdit && (job.status === "scheduled" || job.status === "in_progress") && (
          <div className="flex justify-end gap-2">
            {job.status === "scheduled" && (
              <Button size="sm" loading={starting} onClick={onStart}>
                Start
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={onEdit}>
              Edit
            </Button>
            {job.status === "in_progress" && (
              <Button size="sm" onClick={onFinish}>
                Finish
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        )}
      </TD>
```

- [ ] **Step 5: Run to verify pass**

Run: `cd frontend && npm run test -- JobsPanel.render`
Expected: passes.

- [ ] **Step 6: Add the operator field to `EditDialog`**

Add the import at the top of `JobsPanel.tsx` (extend the existing `@tanstack/react-query` import):

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
```

Replace the whole `EditDialog` function (currently lines 409-492):

```tsx
function EditDialog({
  job,
  onClose,
  onDone,
  onError,
}: {
  job: WorkOrder | null;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  // Keyed on the job id at the call site, so these initialise from whichever
  // job the user opened rather than sticking at the first one's values.
  const [quantity, setQuantity] = useState(trimQty(job?.quantity_planned ?? "0"));
  const [start, setStart] = useState(job?.scheduled_start ?? "");
  const [notes, setNotes] = useState(job?.notes ?? "");
  const [operatorId, setOperatorId] = useState(job?.assigned_operator_id ?? "");

  const isScheduled = job?.status === "scheduled";

  const operators = useQuery<{ id: string; name: string }[]>({
    queryKey: ["operators"],
    enabled: job !== null,
    queryFn: async () => {
      const res = await apiFetch("/operators");
      if (!res.ok) throw new Error("Failed to load operators");
      return res.json();
    },
  });

  const save = useMutation({
    mutationFn: async () => {
      if (job === null) return null;
      const body: Record<string, unknown> = { assigned_operator_id: operatorId || null };
      // Every other field 409s on a job that isn't "scheduled" - only send
      // them when the job actually is, so assigning an operator to a running
      // job doesn't trip that guard.
      if (isScheduled) {
        body.quantity_planned = quantity;
        body.scheduled_start = start || null;
        body.notes = notes || null;
      }
      const res = await apiFetch(`/work-orders/${job.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const responseBody = await res.json().catch(() => ({}));
        throw new Error(responseBody.detail ?? "Failed to save the job");
      }
      return res.json();
    },
    onSuccess: onDone,
    onError: (e: Error) => onError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <Dialog open={job !== null} onClose={onClose} title={`Edit WO-${job?.wo_number ?? ""}`}>
      <form onSubmit={onSubmit} className="grid gap-4">
        {isScheduled && (
          <>
            <p className="text-sm text-muted-foreground">
              Changing the quantity recomputes what this job takes, against the recipe as it stands
              now.
            </p>
            <Field label="Quantity" htmlFor="edit-qty">
              <Input
                id="edit-qty"
                type="number"
                min="0"
                step="0.0001"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                required
              />
            </Field>
            <Field label="Start date" htmlFor="edit-start">
              <Input
                id="edit-start"
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </Field>
            <Field label="Notes" htmlFor="edit-notes">
              <Input id="edit-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
          </>
        )}
        <Field
          label="Assigned operator"
          htmlFor="edit-operator"
          hint="Who's running this job, for shops without a badge reader or QR check-in."
        >
          <Select id="edit-operator" value={operatorId} onChange={(e) => setOperatorId(e.target.value)}>
            <option value="">Unassigned</option>
            {operators.data?.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Never mind
          </Button>
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
```

- [ ] **Step 7: Run the full frontend test suite to check for regressions**

Run: `cd frontend && npm run test`
Expected: all tests pass.

- [ ] **Step 8: Manually verify in the browser**

Start the dev servers, open Production > Jobs, start a job, click Edit on it, confirm the dialog shows only the "Assigned operator" field (no quantity/date/notes), pick an operator, save, and reopen to confirm it stuck. Then edit a still-scheduled job and confirm all fields show. Take a screenshot as proof.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/production/types.ts frontend/src/pages/production/JobsPanel.tsx frontend/src/pages/production/JobsPanel.render.test.tsx
git commit -m "feat: let a job's assigned operator be edited regardless of job status"
```

---

## Self-Review

**Spec coverage:**
- Attribution model (`CaptureEvent`, at-most-one-open-per-machine, `resolve_attribution`'s three-step precedence) -> Tasks 1, 3. ✓
- Tier 1 (badge/RFID reader, `Operator.badge_id` already added by the registry plan, `POST /operators/capture`, 404 on unrecognized badge) -> Task 4. ✓
- Tier 2 (QR code per machine, check-in page listing the roster, tap-to-select, "remembers" via local storage, new-name creation via `resolve_or_create_operator`) -> Tasks 4, 6. ✓
- Tier 3 (`WorkOrder.assigned_operator_id`, editable wherever work orders are edited, query-time-only fallback with no event history) -> Tasks 2, 8. ✓
- "Print QR" action on the Machines panel -> Tasks 5, 7. ✓
- Testing section's 7 bullet points -> covered exactly: unrecognized badge/no event (Task 4), second scan closes first (Task 3's `test_open_capture_event_closes_prior_open_event_on_same_machine`), capture-beats-work-order / work-order-fallback / neither-returns-None (Task 3), QR check-in with an unlisted name (Task 4's `test_checkin_with_new_name_creates_operator`), cross-org isolation for both badge_id and machine_id (Task 4's `test_badge_id_scoped_per_org`, `test_badge_scan_unknown_machine_404`, `test_checkin_unknown_machine_404`). ✓
- Out-of-scope items (reader/kiosk hardware, native phone app, continuous presence tracking, the live operator board UI, retiring Pace Tracking) -> not touched anywhere in this plan, matching the spec's explicit exclusions.

**Placeholder scan:** no TBD/TODO, every step has runnable code and exact commands.

**Type consistency:** `CaptureResponse` (`operator_id, operator_name, machine_id, started_at`) is identical across Task 4's Pydantic model and Task 6's TypeScript interface. `RosterOperator`/`RosterResponse` (Task 6) match `RosterOperatorResponse`/`CheckinRosterResponse` (Task 4). `WorkOrderResponse.assigned_operator_id` (Task 2) matches `WorkOrder.assigned_operator_id` (Task 8's `types.ts`). `open_capture_event`'s signature in Task 3 matches every call site in Task 4.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-29-operator-identity-capture.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
