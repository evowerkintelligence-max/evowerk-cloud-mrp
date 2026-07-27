# Work Orders Implementation Plan (Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the make loop, so a shop can record that it built the thing the planning grid told it to build, and the plan stops promising units nobody is making.

**Architecture:** Three new tables (`WorkOrder`, `WorkOrderComponent`, `WorkOrderComponentLot`) hold the execution record. A new `app/services/work_orders.py` owns the three pieces of arithmetic that must not be duplicated: component requirements from the active BOM, reservations ranked by urgency, and expected job duration. `compute_mrp_plan` gains a fourth supply source inline. A new Production page mirrors Sales and Procurement, and the planning grid gains a Make button.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + Pydantic v2 (backend); React + TypeScript + TanStack Query + Tailwind (frontend); pytest + vitest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-27-work-orders-design.md`. Read it before Task 1.
- Job status values are exactly `scheduled` | `in_progress` | `done` | `cancelled`.
- `remainder_disposition` values are exactly `not_started` | `consumed`, and null unless the job under-ran.
- No money anywhere: no cost, labour rate or valuation column, field or input.
- New Alembic migration's `down_revision` is `b41f7c2e8d05` (the sales-orders revision). Verify with `cd backend && python -m alembic heads` before writing it; if the head is not `b41f7c2e8d05`, stop and report rather than guessing.
- User-facing `HTTPException` details follow house voice: lowercase, terse, no trailing period, concrete numbers or SKU interpolated. 400 for malformed input, 409 for state or stock conflicts.
- Quantities are `Numeric(12, 4)`. Every quantity accepted from a user is range-checked against `MAX_LINE_QUANTITY = 100_000_000`, matching `app/api/routes/sales_orders.py`.
- No new dependencies, backend or frontend.
- No em dashes in code, comments, docstrings or user-visible copy.
- Backend tests run from `backend/`: `python -m pytest tests/<file> -v`. Frontend from `frontend/`: `npx vitest run` and `npx tsc -b`.
- Two tests in `backend/tests/test_admin_customers.py` fail intermittently for reasons unrelated to this work. If only those fail, say so explicitly; any other failure blocks the task.
- The working tree carries unrelated uncommitted work, including a `ProductionSetup` page. Stage only files you change, and only your own hunks in a file that already has unrelated changes. Never `git add -A` or `git add .`.

---

### Task 1: Models, migration and the feature key

**Files:**
- Create: `backend/app/models/work_order.py`
- Create: `backend/alembic/versions/<new_rev>_work_orders.py`
- Modify: `backend/app/core/features.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_work_orders.py` (new)

**Interfaces:**
- Produces: `WorkOrder`, `WorkOrderComponent`, `WorkOrderComponentLot` in `app.models.work_order`; feature key `"production"`.

- [ ] **Step 1: Confirm the migration parent**

```bash
cd backend && python -m alembic heads
```

Expected: `b41f7c2e8d05 (head)`. If it differs, stop and report.

- [ ] **Step 2: Write the models**

Create `backend/app/models/work_order.py`. Mirror `backend/app/models/sales.py` in style: same import set, comments that say *why* rather than *what*.

```python
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkOrder(Base):
    """One production job: make N of an item. The execution record the app
    was missing, as opposed to Bom and Routing which describe how a thing is
    made in the abstract.
    """

    __tablename__ = "work_orders"
    __table_args__ = (UniqueConstraint("org_id", "wo_number", name="uq_wos_org_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True
    )
    # The recipe VERSION in force when the job was raised or last edited.
    # Snapshotted rather than looked up live so editing a BOM next month
    # cannot rewrite what this job reserved or consumed.
    bom_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("boms.id"), nullable=False)
    # Recorded because the definition exists and is useful history. This
    # phase does not track the job step by step.
    routing_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("routings.id"), nullable=True
    )
    # Server-computed (max wo_number for this org + 1) at creation, never
    # user-supplied. Plain int; format it as "WO-4" at the response or
    # frontend layer, never store the formatted string.
    wo_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # scheduled | in_progress | done | cancelled. Only `scheduled` and
    # `in_progress` reach the planning engine, and only `scheduled` holds
    # stock - see app.services.work_orders.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled", index=True)
    quantity_planned: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    quantity_good: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    quantity_scrapped: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal(0)
    )
    # A scrap count with no cause tells the shop its yield is poor without
    # telling it why, which is the question the number always prompts next.
    scrap_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # not_started | consumed. What happened to the components of units never
    # attempted. Null unless the job finished below its planned quantity.
    remainder_disposition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id"), nullable=False
    )
    # When the shop intends to begin. Drives both the planning horizon bound
    # and the reservation ranking: the job due soonest wins contested stock.
    scheduled_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class WorkOrderComponent(Base):
    """What this job takes. Stored rather than re-derived from the BOM,
    because the BOM may change between raising and starting a job and both
    the reservation and the return-on-cancel have to reflect what this job
    actually committed to.
    """

    __tablename__ = "work_order_components"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False)
    # BOM quantity times planned quantity, already including that BOM line's
    # own scrap_pct (load_bom_children folds it in).
    quantity_required: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    quantity_consumed: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal(0)
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorkOrderComponentLot(Base):
    """Which lots a lot-tracked component was actually drawn from, written
    from the LotConsumption list consume_lot_fefo returns. It exists so a
    cancel or an under-run return can put stock back in the lots it came out
    of; LotTransaction carries no job reference, so the split cannot be
    recovered from the existing ledger.
    """

    __tablename__ = "work_order_component_lots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wo_component_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_order_components.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lot_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("lots.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
```

- [ ] **Step 3: Add the feature key**

In `backend/app/core/features.py`, add to the `FEATURES` dict beside the other core operations keys:

```python
    "production": ("Production", "core"),
```

- [ ] **Step 4: Grant it in tests**

`backend/tests/conftest.py` holds `_GRANTED_IN_TESTS`, a deliberately static snapshot. Add `"production"` to it, next to `"sales"`. Without this every route in this plan returns 403 in tests.

Also add `work_order` to the model import list in that file (the one carrying the `# noqa: F401 - register models` comment) so `Base.metadata.create_all` sees the new tables.

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/<rev>_work_orders.py`. Pick a 12-hex-character revision id. Follow `backend/alembic/versions/b41f7c2e8d05_sales_orders.py` exactly, including the secrets-scanner pragma and the entitlement backfill.

```python
"""work orders

Revision ID: <rev>
Revises: b41f7c2e8d05
Create Date: 2026-07-27

"""
from alembic import context, op
import sqlalchemy as sa

revision = "<rev>"  # pragma: allowlist secret
down_revision = "b41f7c2e8d05"  # pragma: allowlist secret
branch_labels = None
depends_on = None
```

`upgrade()` creates the three tables, then calls the entitlement backfill. `downgrade()` revokes the entitlement, then drops `work_order_component_lots`, `work_order_components`, `work_orders` in that order.

Copy the `_set_sales_entitlement` helper from `b41f7c2e8d05_sales_orders.py` verbatim, renaming it and its constants for `production`. It must keep: the `connection=None` parameter (so the test below can drive it), the offline-mode branch emitting a single Postgres statement, the skip for orgs with an empty `enabled_features`, and the `GRANTS_FEATURES` module-level declaration. `backend/tests/test_feature_migration_coverage.py` will fail without `GRANTS_FEATURES = ["production"]`.

Indexes required, matching `index=True` in the models: `ix_work_orders_org_id`, `ix_work_orders_item_id`, `ix_work_orders_status`, `ix_work_order_components_org_id`, `ix_work_order_components_wo_id`, `ix_work_order_component_lots_org_id`, `ix_work_order_component_lots_wo_component_id`. Plus the unique constraint `uq_wos_org_number`.

- [ ] **Step 6: Verify the migration renders both ways**

```bash
cd backend && DATABASE_URL=postgresql+asyncpg://u:p@localhost/db python -m alembic upgrade b41f7c2e8d05:<rev> --sql
```

Expected: exit 0, ending with the `UPDATE orgs ... jsonb` backfill statement. Then the same for `downgrade <rev>:b41f7c2e8d05 --sql`.

- [ ] **Step 7: Run the entitlement coverage guard**

```bash
cd backend && python -m pytest tests/test_feature_migration_coverage.py -v
```

Expected: 3 passed. If `test_every_core_feature_is_backfilled_onto_existing_orgs` fails naming `production`, the backfill or `GRANTS_FEATURES` is missing.

- [ ] **Step 8: Write the schema test**

Create `backend/tests/test_work_orders.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def test_production_feature_is_enabled_for_a_new_test_org(client):
    """Guards the conftest grant: every route in this plan 403s without it."""
    token = await _register_and_login(client)
    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "production" in res.json()["enabled_features"]
```

- [ ] **Step 9: Run the tests**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v
```

Expected: 1 passed. Then the full suite:

```bash
cd backend && python -m pytest -q
```

Expected: no new failures beyond the two known-flaky admin tests.

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/work_order.py backend/app/core/features.py backend/tests/conftest.py backend/tests/test_work_orders.py backend/alembic/versions/
git commit -m "feat: work order models, migration and production feature key"
```

---

### Task 2: The work orders service, and raising a job

**Files:**
- Create: `backend/app/services/work_orders.py`
- Create: `backend/app/api/routes/work_orders.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_work_orders.py` (extend)

**Interfaces:**
- Consumes: models from Task 1; `load_bom_children` from `app.services.mrp`; `resolve_location_id` from `app.services.locations`.
- Produces: `component_requirements(db, org_id, item_id, quantity) -> list[tuple[uuid.UUID, Decimal]]` and `active_bom_id(db, org_id, item_id) -> uuid.UUID | None` in `app.services.work_orders`; `POST /work-orders`, `GET /work-orders`, `GET /work-orders/{id}`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_work_orders.py`. Copy `_create_item` and `_create_active_bom` verbatim from `backend/tests/test_sales_orders.py`; do not reconstruct them from memory, because the BOM PATCH payload requires `expected_updated_at` and getting it wrong produces a confusing 409.

```python
async def _create_item(client, token, sku, name="Widget", is_lot_tracked=False):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "finished_good",
            "unit_of_measure": "ea",
            "is_lot_tracked": is_lot_tracked,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _create_active_bom(client, token, produced_item, lines):
    """lines: list of (component_item, qty_per)."""
    headers = {"Authorization": f"Bearer {token}"}
    bom = await client.post("/boms", json={"item_id": produced_item["id"]}, headers=headers)
    assert bom.status_code == 201
    bom_data = bom.json()
    patch = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": component["id"],
                    "quantity": str(qty),
                    "sequence": i,
                    "scrap_pct": "0",
                }
                for i, (component, qty) in enumerate(lines)
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert patch.status_code == 200
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200
    return bom_data["id"]


async def _raise_job(client, token, item, qty=10, scheduled_start=None):
    body = {"item_id": item["id"], "quantity_planned": str(qty)}
    if scheduled_start is not None:
        body["scheduled_start"] = scheduled_start
    res = await client.post(
        "/work-orders", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_raising_a_job_computes_its_components_and_moves_no_stock(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    job = await _raise_job(client, token, pump, qty=10)
    assert job["status"] == "scheduled"
    assert job["wo_number"] == 1
    assert [(c["item_sku"], c["quantity_required"]) for c in job["components"]] == [
        ("SEAL-1", "20.0000")
    ]

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal(0), "raising must move no stock"


async def test_job_numbers_allocate_sequentially_per_org(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    numbers = [(await _raise_job(client, token, pump))["wo_number"] for _ in range(3)]
    assert numbers == [1, 2, 3]


async def test_an_item_with_no_active_bom_cannot_be_built(client):
    token = await _register_and_login(client)
    pump = await _create_item(client, token, "PUMP-1")
    res = await client.post(
        "/work-orders",
        json={"item_id": pump["id"], "quantity_planned": "5"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "PUMP-1" in res.json()["detail"]


async def test_job_rejects_a_nonpositive_or_oversized_quantity(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    headers = {"Authorization": f"Bearer {token}"}

    for bad in ("0", "-5", "100000000"):
        res = await client.post(
            "/work-orders",
            json={"item_id": pump["id"], "quantity_planned": bad},
            headers=headers,
        )
        assert res.status_code == 400, bad


async def test_bom_scrap_pct_is_folded_into_the_requirement(client):
    """load_bom_children already folds scrap_pct in, so the job must not
    apply it a second time."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    bom = await client.post("/boms", json={"item_id": pump["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": seal["id"],
                    "quantity": "2",
                    "sequence": 0,
                    "scrap_pct": "10",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)

    job = await _raise_job(client, token, pump, qty=10)
    assert job["components"][0]["quantity_required"] == "22.0000"


async def test_jobs_are_org_scoped(client):
    token_a = await _register_and_login(client)
    seal = await _create_item(client, token_a, "SEAL-1")
    pump = await _create_item(client, token_a, "PUMP-1")
    await _create_active_bom(client, token_a, pump, [(seal, 2)])
    job = await _raise_job(client, token_a, pump)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    headers_b = {"Authorization": f"Bearer {token_b}"}
    assert (await client.get("/work-orders", headers=headers_b)).json() == []
    assert (await client.get(f"/work-orders/{job['id']}", headers=headers_b)).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v
```

Expected: the new tests fail with 404, since `/work-orders` does not exist.

- [ ] **Step 3: Write the service**

Create `backend/app/services/work_orders.py`:

```python
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Bom
from app.services.mrp import load_bom_children


async def active_bom_id(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> uuid.UUID | None:
    """The item's currently active recipe, or None if it has none. A job
    snapshots this so a later BOM edit cannot rewrite what it committed to."""
    return await db.scalar(
        select(Bom.id).where(Bom.org_id == org_id, Bom.item_id == item_id, Bom.status == "active")
    )


async def component_requirements(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal
) -> list[tuple[uuid.UUID, Decimal]]:
    """What a job for `quantity` of `item_id` needs, one entry per direct
    component.

    Deliberately ONE level deep, not a full explosion: a work order consumes
    what its own recipe lists. If a sub-assembly is itself manufactured, it
    is consumed as a part and gets its own job; exploding through it here
    would consume raw material the shop never touched for this job.

    load_bom_children already folds each line's scrap_pct into the per-unit
    figure, so this must not apply scrap a second time.
    """
    children = await load_bom_children(db, org_id)
    return [(child_id, qty_per * quantity) for child_id, qty_per in children.get(item_id, [])]
```

- [ ] **Step 4: Write the route module**

Create `backend/app/api/routes/work_orders.py`, mirroring `backend/app/api/routes/sales_orders.py`. Read that file first and follow it closely. This step delivers creation and reads only; start, finish and cancel arrive in Tasks 4 to 6.

Requirements:

- `router = APIRouter(prefix="/work-orders", tags=["work-orders"])`
- `MAX_WO_NUMBER_ATTEMPTS = 3` and `MAX_LINE_QUANTITY = 100_000_000` as module constants.
- Role gates: mutations `require_role("owner", "manager")`, reads `get_current_user`.
- `wo_number` allocation uses the same race guard as `create_sales_order`: a loop reading `select(func.max(WorkOrder.wo_number)).where(WorkOrder.org_id == org_id)` with the insert wrapped in `async with db.begin_nested():`, so an `IntegrityError` retry does not expire already-loaded objects.
- `WorkOrderCreateRequest`: `item_id: str`, `quantity_planned: Decimal`, `scheduled_start: date | None = None`, `location_id: str | None = None`, `notes: str | None = None`, `start_now: bool = False`. `start_now` is accepted and validated now but ignored until Task 4, which wires it to the start path; note that in a comment so the next implementer does not think it is dead.
- Validation, each 400 in house voice: quantity `> 0` and `< MAX_LINE_QUANTITY`; item exists in this org; the item has an active BOM (message names the SKU, for example `f"{item.sku} has no active recipe"`).
- Resolve the location with `resolve_location_id(db, current.org_id, body.location_id)`.
- Write the `WorkOrder` then its `WorkOrderComponent` rows from `component_requirements`, `sequence` in enumeration order.
- Response models: `WorkOrderComponentResponse` (`item_id`, `item_sku`, `item_name`, `quantity_required`, `quantity_consumed`, `sequence`), and `WorkOrderResponse` carrying `id`, `wo_number`, `item_id`, `item_sku`, `item_name`, `status`, `quantity_planned`, `quantity_good`, `quantity_scrapped`, `scrap_reason`, `remainder_disposition`, `location_id`, `location_code`, `scheduled_start`, `started_at`, `finished_at`, `notes`, and `components`. `id` fields serialize as `str`.
- `GET /work-orders` supports `q` (matching item SKU or `wo_number` cast to string), a `status` filter, `limit` (1-200, default 50) and `offset`, ordered by `wo_number`. Return components in the same grouped query, following `_fetch_lines_for_orders` in `sales_orders.py`; do not fetch per row.
- Audit events `work_order.raised` via `log_event`.

- [ ] **Step 5: Register the router**

In `backend/app/main.py`, import `work_orders` alongside the other route modules and register it beside the other operations routers:

```python
app.include_router(work_orders.router, dependencies=[Depends(require_feature("production"))])
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/work_orders.py backend/app/api/routes/work_orders.py backend/app/main.py backend/tests/test_work_orders.py
git commit -m "feat: raise a work order against the active recipe"
```

---

### Task 3: Editing a scheduled job

**Files:**
- Modify: `backend/app/api/routes/work_orders.py`
- Test: `backend/tests/test_work_orders.py` (extend)

**Interfaces:**
- Consumes: `component_requirements`, `active_bom_id` from Task 2.
- Produces: `PATCH /work-orders/{id}`.

Correcting a job raised for 50 that should have been 100 is the most common thing a shop will want to do. Without this it means cancel and retype.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_work_orders.py`:

```python
async def test_editing_a_scheduled_job_recomputes_its_components(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=50)

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "100"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["quantity_planned"] == "100.0000"
    assert res.json()["components"][0]["quantity_required"] == "200.0000"


async def test_editing_picks_up_a_bom_changed_since_the_job_was_raised(client):
    """A job always reflects the recipe as of its last edit, so a corrected
    BOM reaches a job nobody has started."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    bolt = await _create_item(client, token, "BOLT-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=10)

    # Supersede the recipe: a new active BOM adding a bolt.
    await _create_active_bom(client, token, pump, [(seal, 2), (bolt, 1)])

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "10"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert sorted(c["item_sku"] for c in res.json()["components"]) == ["BOLT-1", "SEAL-1"]


async def test_editing_can_move_the_date_and_notes(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=10, scheduled_start="2030-06-01")

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"scheduled_start": "2030-05-01", "notes": "moved up"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduled_start"] == "2030-05-01"
    assert res.json()["notes"] == "moved up"


async def test_editing_rejects_a_bad_quantity(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=10)

    for bad in ("0", "-1", "100000000"):
        res = await client.patch(
            f"/work-orders/{job['id']}", json={"quantity_planned": bad}, headers=headers
        )
        assert res.status_code == 400, bad
    # The refused edits left the original quantity alone.
    still = await client.get(f"/work-orders/{job['id']}", headers=headers)
    assert still.json()["quantity_planned"] == "10.0000"
```

A test that editing a started job is refused belongs with Task 4, which is the first task able to start one. Task 4 adds it.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v -k "editing"
```

Expected: fail with 405 or 404, since PATCH does not exist.

- [ ] **Step 3: Implement the edit**

Add to `backend/app/api/routes/work_orders.py`:

- `WorkOrderUpdateRequest` with every field optional: `quantity_planned: Decimal | None`, `scheduled_start: date | None`, `location_id: str | None`, `notes: str | None`.
- Role gate `require_role("owner", "manager")`.
- `get_or_404(db, WorkOrder, current.org_id, wo_id, "Work order not found")`.
- Refuse unless `status == "scheduled"`, 409 in house voice, because a started job's components are already consumed.
- Validate any supplied `quantity_planned` with the same bounds as creation.
- Re-snapshot `bom_id` from `active_bom_id`, 400 naming the SKU if the item no longer has an active recipe.
- Delete the existing `WorkOrderComponent` rows and re-insert from `component_requirements` against the new quantity and recipe. Do this even when only the date changed: it is one query either way, and a conditional here is how the components and the recipe drift apart.
- Touch `updated_at` explicitly, because only child rows changing does not fire SQLAlchemy's `onupdate` on the parent.
- Audit event `work_order.updated`.
- One `await db.commit()`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/work_orders.py backend/tests/test_work_orders.py
git commit -m "feat: correct a scheduled job instead of retyping it"
```

---

### Task 4: Reservations and starting a job

**Files:**
- Modify: `backend/app/services/work_orders.py`
- Modify: `backend/app/api/routes/work_orders.py`
- Test: `backend/tests/test_work_orders.py` (extend)

**Interfaces:**
- Consumes: `apply_inventory_delta` from `app.services.inventory`; `consume_lot_fefo` and `LotConsumption` from `app.services.lots`; `resolve_grid_window` from `app.services.planning_grid`.
- Produces: `reserved_by_higher_priority(db, org_id, job) -> dict[uuid.UUID, Decimal]` in `app.services.work_orders` (the job carries its own `location_id` and rank, so they are not separate parameters); `POST /work-orders/{id}/start`.

**Critical context.** There is no single consume path. `apply_inventory_delta` maintains `InventoryBalance` and does **not** itself check for lot tracking; sending a lot-tracked item down it writes a balance row alongside the lots and double-counts the stock. Lot stock is consumed with `consume_lot_fefo`, which returns `list[LotConsumption]` where each entry has `.lot` and `.quantity`. So start branches per component on `item.is_lot_tracked`, and the branch **is** the guard.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_work_orders.py`. Copy `_give_stock` verbatim from `backend/tests/test_sales_orders.py`.

```python
async def _give_stock(client, token, item, qty):
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": str(qty), "reason_code": "found_stock"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    return res


async def _start(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/start", headers={"Authorization": f"Bearer {token}"}
    )


async def test_starting_consumes_the_stored_requirements(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)

    res = await _start(client, token, job)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "in_progress"
    assert res.json()["started_at"] is not None

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("80")


async def test_a_shortfall_on_one_component_rolls_back_the_others(client):
    """One transaction, so a job short on its third component must undo the
    first two. A single-component job cannot prove this."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    plenty_a = await _create_item(client, token, "PLENTY-A")
    plenty_b = await _create_item(client, token, "PLENTY-B")
    short = await _create_item(client, token, "SHORT-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(plenty_a, 1), (plenty_b, 1), (short, 1)])
    await _give_stock(client, token, plenty_a, 100)
    await _give_stock(client, token, plenty_b, 100)
    await _give_stock(client, token, short, 1)
    job = await _raise_job(client, token, pump, qty=10)

    res = await _start(client, token, job)
    assert res.status_code == 409

    for item, expected in ((plenty_a, "100"), (plenty_b, "100"), (short, "1")):
        inv = await client.get(f"/inventory/{item['id']}", headers=headers)
        assert Decimal(inv.json()["quantity_on_hand"]) == Decimal(expected), item["sku"]
    still = await client.get(f"/work-orders/{job['id']}", headers=headers)
    assert still.json()["status"] == "scheduled"


async def test_the_more_urgent_job_wins_contested_stock(client):
    """Claims rank by scheduled_start, so a job due sooner starts even though
    a later job was raised first and holds the material."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    later = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(30))
    sooner = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))

    assert (await _start(client, token, sooner)).status_code == 200

    refused = await _start(client, token, later)
    assert refused.status_code == 409
    assert "SEAL-1" in refused.json()["detail"]


async def test_a_later_job_cannot_block_a_sooner_one(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(30))
    sooner = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))

    assert (await _start(client, token, sooner)).status_code == 200, (
        "a job due next month must not hold stock a job due this week needs"
    )


async def test_a_job_scheduled_beyond_the_horizon_holds_nothing(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(400))
    near = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))

    assert (await _start(client, token, near)).status_code == 200


async def test_a_job_does_not_block_itself(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 10)
    job = await _raise_job(client, token, pump, qty=10)

    assert (await _start(client, token, job)).status_code == 200


async def test_starting_twice_is_refused(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)

    assert (await _start(client, token, job)).status_code == 200
    assert (await _start(client, token, job)).status_code == 409

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("90"), "stock must come off once"


async def test_a_started_job_cannot_be_edited(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "20"}, headers=headers
    )
    assert res.status_code == 409


async def test_starting_a_lot_tracked_component_draws_from_lots(client):
    """A lot-tracked component cannot go through apply_inventory_delta, so
    this pins the other branch."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "TRACKED-SEAL", is_lot_tracked=True)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _receive_into_lot(client, token, seal, qty=100, lot_number="LOT-A")
    job = await _raise_job(client, token, pump, qty=10)

    assert (await _start(client, token, job)).status_code == 200

    lots = await client.get("/lots", headers=headers)
    remaining = sum(Decimal(lot["quantity_remaining"]) for lot in lots.json())
    assert remaining == Decimal("80")
```

Add these helpers to the module:

```python
from datetime import datetime, timedelta, timezone


def _in_days(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()
```

`_receive_into_lot` does not exist in this module yet. Copy it verbatim from `backend/tests/test_sales_orders.py`, along with the `_create_supplier` helper it needs. Read those functions before writing the test rather than reconstructing them, because the PO receive payload has several required fields and getting one wrong produces a confusing 400.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v -k "start or block or urgent or horizon"
```

Expected: fail with 404 on `/start`.

- [ ] **Step 3: Add reservations to the service**

Add to `backend/app/services/work_orders.py`:

```python
from datetime import date

from sqlalchemy import func

from app.models.work_order import WorkOrder, WorkOrderComponent
from app.services.planning_grid import resolve_grid_window

# A job with no scheduled_start is due now, so it outranks every dated job.
# date.min as the sort key expresses that without a second branch in SQL.
_UNDATED_RANKS_FIRST = date.min


async def reserved_by_higher_priority(
    db: AsyncSession, org_id: uuid.UUID, job: WorkOrder
) -> dict[uuid.UUID, Decimal]:
    """Component quantity per item held by scheduled jobs that outrank `job`.

    Ranking is by scheduled_start, earliest first, with wo_number breaking
    ties so the order is total and stable. Whoever clicks first decides
    nothing, which is what makes this a priority rule rather than a race.

    Only jobs inside the planning horizon hold stock, the same bound the
    engine applies. A job scheduled for next quarter is intent, not a claim
    on today's shelf, and letting it block this week's work would be the
    worst of both worlds since it is not even in the plan being looked at.
    """
    _, horizon_end = resolve_grid_window()
    rank = func.coalesce(WorkOrder.scheduled_start, _UNDATED_RANKS_FIRST)
    job_rank = job.scheduled_start or _UNDATED_RANKS_FIRST

    rows = (
        await db.execute(
            select(
                WorkOrderComponent.item_id,
                func.sum(WorkOrderComponent.quantity_required - WorkOrderComponent.quantity_consumed),
            )
            .join(WorkOrder, WorkOrder.id == WorkOrderComponent.wo_id)
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.status == "scheduled",
                WorkOrder.id != job.id,
                WorkOrder.location_id == job.location_id,
                rank <= horizon_end,
                # Outranks this job: due sooner, or due the same day and
                # raised earlier.
                sa_tuple_lt(rank, WorkOrder.wo_number, job_rank, job.wo_number),
            )
            .group_by(WorkOrderComponent.item_id)
        )
    ).all()
    return {item_id: total for item_id, total in rows if total is not None and total > 0}
```

Replace the `sa_tuple_lt(...)` placeholder with the explicit two-term comparison, since SQLAlchemy tuple comparison is not portable to SQLite:

```python
                or_(
                    rank < job_rank,
                    and_(rank == job_rank, WorkOrder.wo_number < job.wo_number),
                ),
```

and import `and_`, `or_` from `sqlalchemy`.

- [ ] **Step 4: Implement start**

Add `POST /work-orders/{id}/start` to `backend/app/api/routes/work_orders.py`:

- Role gate `require_role("owner", "manager")`.
- Row-lock the job with `get_or_404(..., for_update=True)`, so two concurrent starts cannot both pass the status check.
- Refuse unless `status == "scheduled"`, 409 in house voice.
- Load the job's `WorkOrderComponent` rows joined to `Item`, **ordered by `Item.id`**. Take a row lock over that select with `.with_for_update(of=...)` on the stock rows, in that same deterministic order.

Add this comment above the locking block verbatim, because the ordering is load-bearing and the test suite cannot enforce it:

```python
    # Lock the stock this start is about to read and write, ordered by item
    # id, BEFORE computing availability. Two jobs sharing components would
    # otherwise both read the same free quantity and both pass, and the
    # loser would fail later with a confusing shortfall instead of a
    # reservation message. The deterministic order is what stops two jobs
    # locking the same pair in opposite orders and deadlocking. The suite
    # runs on SQLite where FOR UPDATE is a no-op, so this is guaranteed by
    # the query and by review, not by a concurrency test.
```

- Compute `reserved = await reserved_by_higher_priority(db, current.org_id, job)`.
- For each component, if `on_hand_for(item, location) - reserved.get(item_id, 0) < quantity_required`, raise 409 naming the item SKU, the shortfall, the location code and the blocking job when one is holding the stock, for example `f"{item.sku}: 20 short at {location.code}, WO-7 needs them on 2026-08-14"`. Read `app/services/inventory.py` for the existing on-hand-at-location read rather than writing a new query.
- Then consume, branching per component. Add this comment above the branch:

```python
        # Two paths on purpose, do not collapse them. apply_inventory_delta
        # does not check for lot tracking, so sending a lot-tracked item
        # down it would write an InventoryBalance row alongside the lots and
        # double-count the stock. This branch IS the guard.
```

  - `item.is_lot_tracked`: `consumptions = await consume_lot_fefo(db, org_id=..., item_id=..., location_id=job.location_id, quantity=component.quantity_required)`, then write one `WorkOrderComponentLot` per entry (`lot_id=c.lot.id`, `quantity=c.quantity`), and the `LotTransaction` rows the way `POST /lots/consume-fefo` does. Read that route and mirror it.
  - otherwise: `await apply_inventory_delta(db, org_id=..., item_id=..., location_id=job.location_id, delta=-component.quantity_required, transaction_type="consumption", created_by=current.id, reason="work order")`.
- Set each component's `quantity_consumed = quantity_required`, the job's `status = "in_progress"` and `started_at = datetime.now(timezone.utc)`.
- `log_event` with action `work_order.started`, metadata carrying the job number and the per-item quantities moved.
- One `await db.commit()` at the end, so a shortfall on the third component rolls back the first two.

- [ ] **Step 5: Name the location in the shared shortfall messages**

The spec puts this in the shared services rather than wrapping it at this caller, so every existing caller's message improves too.

In `app/services/inventory.py` and `app/services/lots.py`, find the 409s raised when stock cannot cover a request. They currently say "at this location" without naming it. Look the location code up and interpolate it. Do the lookup **inside the error branch only**, so the happy path takes no extra query.

Existing tests assert on these strings. Run them first and update the assertions that break:

```bash
cd backend && python -m pytest tests/test_inventory.py tests/test_lots.py tests/test_sales_orders.py -v -k "short or insufficient or shortfall or refused"
```

Expected: some failures on the changed message text. Update those assertions to match the new wording; do not weaken them to substring checks that would pass either way.

- [ ] **Step 6: Wire `start_now`**

In the create route from Task 2, when `body.start_now` is true, run the same start path in the same request so the grid's Make button is one click. Extract the start body into a module-level `async def _start_job(db, job, current) -> None` and call it from both places, rather than duplicating the consume logic.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/work_orders.py backend/app/services/inventory.py backend/app/services/lots.py backend/app/api/routes/work_orders.py backend/tests/
git commit -m "feat: starting a job consumes its components, urgent jobs win contested stock"
```

---

### Task 5: Finishing a job

**Files:**
- Modify: `backend/app/api/routes/work_orders.py`
- Test: `backend/tests/test_work_orders.py` (extend)

**Interfaces:**
- Consumes: `find_or_create_lot` from `app.services.lots`; `apply_inventory_delta`.
- Produces: `POST /work-orders/{id}/finish`.

**Critical context.** Finishing below the planned quantity means some units were never attempted, and their components were consumed at start but may still be on the shelf. The app cannot tell, and both wrong answers are bad, so `remainder_disposition` is required rather than defaulted on an under-run.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_work_orders.py`:

```python
async def _finish(client, token, job, good, scrap="0", **extra):
    body = {"quantity_good": str(good), "quantity_scrapped": str(scrap)}
    body.update(extra)
    return await client.post(
        f"/work-orders/{job['id']}/finish",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _running_job(client, token, seal, pump, planned=50, stock=200):
    await _give_stock(client, token, seal, stock)
    job = await _raise_job(client, token, pump, qty=planned)
    assert (await _start(client, token, job)).status_code == 200
    return job


async def test_finishing_stocks_only_the_good_units(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    res = await _finish(client, token, job, good=47, scrap=3, scrap_reason="porosity")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "done"
    assert body["quantity_good"] == "47.0000"
    assert body["quantity_scrapped"] == "3.0000"
    assert body["scrap_reason"] == "porosity"
    assert body["finished_at"] is not None

    inv = await client.get(f"/inventory/{pump['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("47"), "scrap must not be stocked"


async def test_an_under_run_requires_saying_what_happened_to_the_remainder(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    res = await _finish(client, token, job, good=30, scrap=2)
    assert res.status_code == 400


async def test_an_under_run_can_return_the_unused_components(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50, stock=200)
    # 50 seals consumed at start, 150 left.

    res = await _finish(
        client, token, job, good=30, scrap=2, remainder_disposition="not_started"
    )
    assert res.status_code == 200, res.text
    assert res.json()["remainder_disposition"] == "not_started"

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("168"), (
        "18 unmade units' seals go back on the shelf"
    )


async def test_an_under_run_can_write_the_remainder_off(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50, stock=200)

    res = await _finish(client, token, job, good=30, scrap=2, remainder_disposition="consumed")
    assert res.status_code == 200, res.text

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("150"), "nothing comes back"


async def test_meeting_the_planned_quantity_needs_no_disposition(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    assert (await _finish(client, token, job, good=50)).status_code == 200


async def test_the_over_run_guard_allows_a_small_batch_overshoot(client):
    """max(2 x planned, planned + 10): a plain doubling would refuse a job
    planned for 1 that yielded 3, which is routine on a prototype line."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=1)

    assert (await _finish(client, token, job, good=3)).status_code == 200


async def test_the_over_run_guard_refuses_a_typo(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    res = await _finish(client, token, job, good=5000)
    assert res.status_code == 400
    assert "50" in res.json()["detail"]


async def test_finishing_a_lot_tracked_product_needs_a_lot_number(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "TRACKED-PUMP", is_lot_tracked=True)
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=10)

    assert (await _finish(client, token, job, good=10)).status_code == 400
    ok = await _finish(client, token, job, good=10, lot_number="WO-LOT-1")
    assert ok.status_code == 200, ok.text


async def test_finishing_into_an_existing_lot_number_adds_to_that_lot(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "TRACKED-PUMP", is_lot_tracked=True)
    await _create_active_bom(client, token, pump, [(seal, 1)])

    first = await _running_job(client, token, seal, pump, planned=10, stock=100)
    await _finish(client, token, first, good=10, lot_number="BATCH-9")
    second = await _raise_job(client, token, pump, qty=5)
    await _start(client, token, second)
    await _finish(client, token, second, good=5, lot_number="BATCH-9")

    lots = await client.get("/lots", headers=headers)
    batches = [lot for lot in lots.json() if lot["lot_number"] == "BATCH-9"]
    assert len(batches) == 1, "a repeat lot number is the same physical batch"
    assert Decimal(batches[0]["quantity_remaining"]) == Decimal("15")


async def test_finishing_a_job_that_is_not_running_is_refused(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)

    assert (await _finish(client, token, job, good=10)).status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v -k "finish or under_run or over_run"
```

Expected: fail with 404 on `/finish`.

- [ ] **Step 3: Implement finish**

Add to `backend/app/api/routes/work_orders.py`:

```python
# max(2 x planned, planned + 10). A typo guard, not a business rule, which
# is why it is fixed rather than configurable. The additive term matters
# more than it looks: a plain doubling refuses a job planned for 1 that
# yielded 3, routine on a prototype line, and would feel broken to exactly
# the shops least able to work around it.
def _over_run_ceiling(planned: Decimal) -> Decimal:
    return max(planned * 2, planned + 10)
```

Behaviour:

- Role gate `require_role("owner", "manager")`, row-lock with `for_update=True`.
- Refuse unless `status == "in_progress"`, 409.
- `WorkOrderFinishRequest`: `quantity_good: Decimal`, `quantity_scrapped: Decimal = Decimal(0)`, `scrap_reason: str | None = None`, `lot_number: str | None = None`, `remainder_disposition: str | None = None`.
- Validate: neither quantity negative (400); each below `MAX_LINE_QUANTITY` (400); `quantity_good + quantity_scrapped <= _over_run_ceiling(job.quantity_planned)` else 400 naming the planned figure and suggesting a second job.
- If `quantity_good + quantity_scrapped < job.quantity_planned`, require `remainder_disposition` in `{"not_started", "consumed"}` else 400. Reject a disposition supplied without an under-run with 400, so the stored value always means something.
- If the produced item `is_lot_tracked`, require `lot_number` (400), then `await find_or_create_lot(db, org_id=..., item_id=job.item_id, lot_number=body.lot_number, location_id=job.location_id, quantity=body.quantity_good, expiry_date=None, created_by=current.id)`. Otherwise `apply_inventory_delta(..., delta=+body.quantity_good, transaction_type="production", created_by=current.id, reason="work order")`. Skip the stock write entirely when `quantity_good` is 0.
- On `remainder_disposition == "not_started"`, return the remainder's share of each component. The fraction is `(planned - good - scrapped) / planned`. For each component, `return_qty = quantity_consumed * fraction`, quantized to 4 places. For a lot-tracked component, put stock back into the lots named in `WorkOrderComponentLot`, largest draw first, capping each at that draw's quantity; for a non-lot component, `apply_inventory_delta` with a positive delta and `transaction_type="production"`, reason `"work order remainder"`. Reduce each component's `quantity_consumed` by what went back.

Add this comment above the proportional return:

```python
    # Proportional: a job that made half its units returns half of each
    # remaining component. Right for material cut per unit, wrong for a
    # setup piece consumed whole at the start of the run. Getting that exact
    # needs per-component consumption points, which is operation tracking.
```

- Set `quantity_good`, `quantity_scrapped`, `scrap_reason`, `remainder_disposition`, `status = "done"`, `finished_at = datetime.now(timezone.utc)`.
- `log_event` with action `work_order.finished`, metadata carrying the job number, good, scrap and disposition.
- One `await db.commit()`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/work_orders.py backend/tests/test_work_orders.py
git commit -m "feat: finishing a job stocks the good units and accounts for the rest"
```

---

### Task 6: Cancelling a job

**Files:**
- Modify: `backend/app/api/routes/work_orders.py`
- Test: `backend/tests/test_work_orders.py` (extend)

**Interfaces:**
- Produces: `POST /work-orders/{id}/cancel`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_work_orders.py`:

```python
async def test_cancelling_a_scheduled_job_moves_nothing(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)

    res = await client.post(f"/work-orders/{job['id']}/cancel", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("100")


async def test_cancelling_a_running_job_puts_everything_back(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=10, stock=100)

    res = await client.post(f"/work-orders/{job['id']}/cancel", headers=headers)
    assert res.status_code == 200

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("100"), "stock as it was before start"


async def test_cancelling_restores_the_exact_lot_split(client):
    """Not merely the right total: the stock has to go back into the lots it
    came out of, or FEFO ordering and expiry tracking quietly rot."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "TRACKED-SEAL", is_lot_tracked=True)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _receive_into_lot(client, token, seal, qty=60, lot_number="LOT-A")
    await _receive_into_lot(client, token, seal, qty=40, lot_number="LOT-B")

    job = await _raise_job(client, token, pump, qty=80)
    assert (await _start(client, token, job)).status_code == 200

    await client.post(f"/work-orders/{job['id']}/cancel", headers=headers)

    lots = {lot["lot_number"]: Decimal(lot["quantity_remaining"]) for lot in (
        await client.get("/lots", headers=headers)
    ).json()}
    assert lots["LOT-A"] == Decimal("60")
    assert lots["LOT-B"] == Decimal("40")


async def test_cancelling_releases_the_stock_for_another_job(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    holder = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))
    later = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(20))
    assert (await _start(client, token, later)).status_code == 409

    await client.post(f"/work-orders/{holder['id']}/cancel", headers=headers)
    assert (await _start(client, token, later)).status_code == 200


async def test_a_finished_job_cannot_be_cancelled(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=10)
    await _finish(client, token, job, good=10)

    res = await client.post(f"/work-orders/{job['id']}/cancel", headers=headers)
    assert res.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v -k "cancel"
```

Expected: fail with 404.

- [ ] **Step 3: Implement cancel**

Add to `backend/app/api/routes/work_orders.py`. Role gate `require_role("owner", "manager")`, row-lock with `for_update=True`.

- From `scheduled`: set `status = "cancelled"` and commit. Nothing was consumed, so nothing moves.
- From `in_progress`: this is exactly `finish(good 0, scrap 0, remainder not_started)`. Reuse the return-the-remainder helper Task 5 extracted rather than writing a second path that moves stock its own way; with good and scrap both 0 the fraction is 1, so every component comes back in full. Then set `status = "cancelled"` (not `done`) and `finished_at`.
- Any other status: 409 in house voice.

Add this comment above the branch:

```python
    # Cancelling a running job means nothing was made and every component is
    # back on the shelf. It is defined in terms of the finish path rather
    # than being a second way to move stock, so the two cannot drift. A job
    # where any work actually happened must be finished instead, where the
    # under-run question gets asked properly.
```

- `log_event` with action `work_order.cancelled`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_work_orders.py -v
```

Expected: all pass.

- [ ] **Step 5: Run the full suite**

```bash
cd backend && python -m pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/work_orders.py backend/tests/test_work_orders.py
git commit -m "feat: cancelling a job returns its components to the lots they came from"
```

---

### Task 7: Production reaches the planning engine

**Files:**
- Modify: `backend/app/services/work_orders.py`
- Modify: `backend/app/services/mrp.py`
- Test: `backend/tests/test_work_order_planning.py` (new)

**Interfaces:**
- Produces: `load_production_supply(db, org_id, horizon_end)` and `load_scheduled_component_demand(db, org_id, horizon_end)` in `app.services.work_orders`; `MrpPlanLine.in_production`.

**Critical context.** This is the trap in the whole design. If a scheduled job counted as supply and nothing else, it would zero the finished good's net requirement, `_explode_and_net` would never explode its components, and the grid would read "all in stock" for a job that cannot run. A scheduled job must contribute **both** supply of its finished good and demand for its stored components. The two always move together.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_work_order_planning.py`. Copy the `PASSWORD` constant, `_register_and_login`, `_create_item`, `_create_active_bom`, `_raise_job`, `_start`, `_finish`, `_give_stock` and `_in_days` helpers from `backend/tests/test_work_orders.py`.

```python
async def _plan_lines(client, token):
    res = await client.get("/mrp/plan", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return {line["item_sku"]: line for line in res.json()["lines"]}


async def _plan_for(client, token, item, qty, when=None):
    await client.put(
        f"/planning/grid/{item['id']}",
        json={"quantity": str(qty), "needed_by": when or _in_days(30)},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_a_running_job_is_incoming_supply(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan_for(client, token, pump, 50)
    await _give_stock(client, token, seal, 100)

    before = await _plan_lines(client, token)
    assert Decimal(before["PUMP-1"]["net_requirement"]) == Decimal("50")

    job = await _raise_job(client, token, pump, qty=50)
    await _start(client, token, job)

    after = await _plan_lines(client, token)
    assert Decimal(after["PUMP-1"]["net_requirement"]) == Decimal(0), (
        "a job in flight must not be planned a second time"
    )
    assert Decimal(after["PUMP-1"]["in_production"]) == Decimal("50")


async def test_a_scheduled_job_still_plans_its_components(client):
    """The trap: supply without component demand would leave the grid saying
    all in stock for a job that cannot actually run."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(5))

    lines = await _plan_lines(client, token)
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("20"), (
        "a scheduled job must still ask for its own components"
    )


async def test_a_running_job_does_not_re_raise_component_demand(client):
    """Its components are already consumed and on_hand reflects that, so
    counting them again would buy them twice."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _give_stock(client, token, seal, 20)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)

    lines = await _plan_lines(client, token)
    assert "SEAL-1" not in lines or Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal(0)


async def test_a_scheduled_job_is_arithmetically_neutral(client):
    """Forecast 50 with a scheduled job for 50 must ask for exactly the same
    components as the forecast alone would."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _plan_for(client, token, pump, 50)

    without = await _plan_lines(client, token)
    baseline = Decimal(without["SEAL-1"]["net_requirement"])

    await _raise_job(client, token, pump, qty=50, scheduled_start=_in_days(5))
    with_job = await _plan_lines(client, token)

    assert Decimal(with_job["SEAL-1"]["net_requirement"]) == baseline == Decimal("100")


async def test_a_job_beyond_the_horizon_is_not_in_the_plan(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(400))

    assert await _plan_lines(client, token) == {}, (
        "neither its supply nor its component demand belongs in this window"
    )


async def test_a_job_late_to_start_still_counts(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(-7))

    lines = await _plan_lines(client, token)
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("20"), (
        "a job that slipped is still going to happen"
    )


async def test_finished_and_cancelled_jobs_count_for_nothing(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan_for(client, token, pump, 50)
    await _give_stock(client, token, seal, 100)

    done_job = await _raise_job(client, token, pump, qty=20)
    await _start(client, token, done_job)
    await _finish(client, token, done_job, good=20)
    cancelled = await _raise_job(client, token, pump, qty=20)
    await client.post(f"/work-orders/{cancelled['id']}/cancel", headers=headers)

    lines = await _plan_lines(client, token)
    assert Decimal(lines["PUMP-1"]["in_production"]) == Decimal(0)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_work_order_planning.py -v
```

Expected: `KeyError: 'in_production'` and the scheduled-job tests failing.

- [ ] **Step 3: Add the loaders to the service**

Add to `backend/app/services/work_orders.py`:

```python
# Only these two statuses reach the plan. A done job has already delivered
# its units into stock and a cancelled one never will, so counting either
# would promise the shop something twice.
PLANNED_STATUSES = ("scheduled", "in_progress")


async def load_production_supply(
    db: AsyncSession, org_id: uuid.UUID, horizon_end: date
) -> dict[uuid.UUID, Decimal]:
    """Incoming supply per item from jobs in flight.

    A scheduled job counts only if it is due inside the window; a running job
    counts whatever its schedule said, because it is happening now. There is
    deliberately no lower bound on the scheduled case: a job that should have
    started last week is still going to happen, matching the rule Phase 2
    settled for late sales orders.
    """
    rank = func.coalesce(WorkOrder.scheduled_start, _UNDATED_RANKS_FIRST)
    rows = (
        await db.execute(
            select(WorkOrder.item_id, func.sum(WorkOrder.quantity_planned))
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.status.in_(PLANNED_STATUSES),
                or_(WorkOrder.status == "in_progress", rank <= horizon_end),
            )
            .group_by(WorkOrder.item_id)
        )
    ).all()
    return {item_id: total for item_id, total in rows if total is not None and total > 0}


async def load_scheduled_component_demand(
    db: AsyncSession, org_id: uuid.UUID, horizon_end: date
) -> dict[uuid.UUID, Decimal]:
    """What jobs not yet started will need.

    Only `scheduled` jobs: a running job's components have already left
    stock, so on_hand reflects them and counting them again would buy them
    twice. This must always move together with load_production_supply's
    scheduled half, or the plan under-orders components (supply counted, its
    demand dropped) or over-orders them (the reverse).
    """
    rank = func.coalesce(WorkOrder.scheduled_start, _UNDATED_RANKS_FIRST)
    rows = (
        await db.execute(
            select(
                WorkOrderComponent.item_id,
                func.sum(WorkOrderComponent.quantity_required - WorkOrderComponent.quantity_consumed),
            )
            .join(WorkOrder, WorkOrder.id == WorkOrderComponent.wo_id)
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.status == "scheduled",
                rank <= horizon_end,
            )
            .group_by(WorkOrderComponent.item_id)
        )
    ).all()
    return {item_id: total for item_id, total in rows if total is not None and total > 0}
```

- [ ] **Step 4: Wire the engine**

In `backend/app/services/mrp.py`:

1. Add `in_production: Decimal` to `MrpPlanLine`, beside `on_draft`, with a comment saying it is supply from jobs in flight and is kept separate so the sources stay visible.
2. In `compute_mrp_plan`, after the existing demand seeding, add the scheduled jobs' component demand into the independent-demand map before exploding:

```python
    scheduled_components = await load_scheduled_component_demand(db, org_id, horizon_end)
    for item_id, qty in scheduled_components.items():
        independent[item_id] = independent.get(item_id, Decimal(0)) + qty
```

Add above it:

```python
    # A scheduled job's components are demand in their own right. Without
    # this, its finished good's supply below would zero that item's net
    # requirement, _explode_and_net would never explode it, and the grid
    # would read "all in stock" for a job that cannot actually run.
```

3. In `_explode_and_net`, load `in_production = await load_production_supply(db, org_id, horizon_end)` alongside the existing `on_order` and `on_draft` loads, and change the netting line to:

```python
        net_requirement = max(0, gross_demand - on_hand - on_order - on_draft - in_production)
```

4. Populate `in_production=production_qty` on each `MrpPlanLine`, following exactly how `order_qty` and `draft_qty` are derived with `max(Decimal(0), ...)`.
5. `_explode_and_net` needs `horizon_end`; pass it from `compute_mrp_plan` and from `compute_build_plan`. For `compute_build_plan`, pass the same window `resolve_grid_window()` gives, so an ad hoc build nets against jobs in flight exactly as planning does.
6. Update `compute_mrp_plan`'s docstring to name the fourth supply source.

- [ ] **Step 5: Expose it on the API**

In `backend/app/api/routes/mrp.py` (and any other response model carrying `on_order`/`on_draft`, grep for `on_draft` to find them all), add `in_production: Decimal` and populate it.

- [ ] **Step 6: Run the tests**

```bash
cd backend && python -m pytest tests/test_work_order_planning.py -v
```

Then the regression check that matters most, since this changed the engine every planning surface depends on:

```bash
cd backend && python -m pytest tests/test_mrp_explosion.py tests/test_planning_grid.py tests/test_sales_orders.py -v
```

Expected: unchanged pass counts. If any fail, the seeding change altered existing behaviour and must be fixed before committing.

- [ ] **Step 7: Run the full suite and commit**

```bash
cd backend && python -m pytest -q
git add backend/app/services/work_orders.py backend/app/services/mrp.py backend/app/api/routes/mrp.py backend/tests/test_work_order_planning.py
git commit -m "feat: jobs in flight are supply, scheduled jobs still plan their components"
```

---

### Task 8: Chasing late and stalled jobs

**Files:**
- Modify: `backend/app/services/work_orders.py`
- Modify: `backend/app/services/morning_brief.py`
- Modify: `backend/app/api/routes/inventory_analytics.py`
- Test: `backend/tests/test_work_order_attention.py` (new)

**Interfaces:**
- Produces: `expected_duration_seconds(db, org_id, item_id, quantity)` and `compute_job_alerts(db, org_id) -> list[JobAlertLine]` in `app.services.work_orders`; `job_alerts` on the morning brief response.

**Critical context.** A job started and never finished is worse than one never started: its components are already gone and it counts as incoming supply, so the plan quietly promises units nobody is making. A fixed number of days is wrong for any shop it was not chosen for, so expected duration comes from evidence.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_work_order_attention.py` with the usual helpers copied from `test_work_orders.py`, plus:

```python
async def _brief(client, token):
    res = await client.get(
        "/inventory/morning-brief", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_a_job_late_to_start_is_flagged(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(-3))

    alerts = (await _brief(client, token))["job_alerts"]
    assert [(a["wo_number"], a["reason"]) for a in alerts] == [(1, "late_to_start")]


async def test_a_job_due_later_is_not_flagged(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(3))

    assert (await _brief(client, token))["job_alerts"] == []
```

Testing the stalled branch needs a job whose `started_at` is in the past, which no API can produce. Write it directly against the test database, following how `backend/tests/test_customers.py::test_a_member_can_read_customers_but_not_create_them` builds a `User` with `async_sessionmaker(test_engine)`:

```python
async def _backdate_start(test_engine, wo_id, days):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models.work_order import WorkOrder
    import uuid as uuid_module

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        job = await session.get(WorkOrder, uuid_module.UUID(wo_id))
        job.started_at = datetime.now(timezone.utc) - timedelta(days=days)
        await session.commit()


async def test_a_job_running_far_too_long_is_flagged_stalled(client, test_engine):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)
    await _backdate_start(test_engine, job["id"], days=60)

    alerts = (await _brief(client, token))["job_alerts"]
    assert [(a["wo_number"], a["reason"]) for a in alerts] == [(1, "stalled")]


async def test_a_job_running_a_normal_length_of_time_is_not_flagged(client, test_engine):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)
    await _backdate_start(test_engine, job["id"], days=1)

    assert (await _brief(client, token))["job_alerts"] == []


async def test_history_sets_the_bar_when_there_is_no_routing(client, test_engine):
    """Five quick jobs teach the app that this item takes about an hour, so a
    sixth still running after two days is stalled even though the cold-start
    constant would have allowed it seven."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 1000)

    for _ in range(5):
        done = await _raise_job(client, token, pump, qty=10)
        await _start(client, token, done)
        await _finish(client, token, done, good=10)
    # Those finished within the test's own runtime, so their measured
    # duration is effectively zero and the +1 day floor governs.

    running = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, running)
    await _backdate_start(test_engine, running["id"], days=3)

    alerts = (await _brief(client, token))["job_alerts"]
    assert any(a["wo_number"] == running["wo_number"] and a["reason"] == "stalled" for a in alerts)


async def test_a_done_job_is_never_flagged(client, test_engine):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)
    await _backdate_start(test_engine, job["id"], days=90)
    await _finish(client, token, job, good=10)

    assert (await _brief(client, token))["job_alerts"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_work_order_attention.py -v
```

Expected: `KeyError: 'job_alerts'`.

- [ ] **Step 3: Add duration measurement to the service**

Add to `backend/app/services/work_orders.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median

from app.models.routing import Routing, RoutingStep
from app.utils.time import ensure_utc

# Used only by a shop with neither routing times nor one completed job of an
# item. It stops being used the moment the shop has told the app anything
# better, which is what makes it a cold start rather than a policy.
DEFAULT_JOB_DAYS = 7
# How many completed jobs of an item the median is taken over.
DURATION_SAMPLE = 5
# Stalled once elapsed exceeds max(FACTOR x expected, expected + FLOOR). The
# additive floor is the same lesson as the over-run guard's +10: doubling
# alone is far too tight at small values and would call a two-hour job
# stalled after four.
STALL_FACTOR = 2
STALL_FLOOR = timedelta(days=1)


async def expected_duration_seconds(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal
) -> float:
    """How long a job like this should take, from the best evidence there is.

    In order: the item's routing, which is the shop's own stated intent and
    is already recorded; else the median per-unit duration of its recently
    completed jobs, median rather than mean so one job left open over a
    holiday does not move the bar; else a cold-start constant.

    Per item, not per org, because a shop making both a five-minute bracket
    and a three-week assembly has no single sensible number, which is
    precisely the problem a constant cannot solve.
    """
    routed = (
        await db.execute(
            select(
                func.sum(func.coalesce(RoutingStep.setup_time_seconds, 0)),
                func.sum(func.coalesce(RoutingStep.cycle_time_seconds, 0)),
            )
            .join(Routing, Routing.id == RoutingStep.routing_id)
            .where(
                Routing.org_id == org_id,
                Routing.item_id == item_id,
                Routing.status == "active",
            )
        )
    ).one()
    setup_total, cycle_total = routed
    if setup_total or cycle_total:
        return float(setup_total or 0) + float(cycle_total or 0) * float(quantity)

    rows = (
        await db.execute(
            select(WorkOrder.started_at, WorkOrder.finished_at, WorkOrder.quantity_planned)
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.item_id == item_id,
                WorkOrder.status == "done",
                WorkOrder.started_at.is_not(None),
                WorkOrder.finished_at.is_not(None),
            )
            .order_by(WorkOrder.finished_at.desc())
            .limit(DURATION_SAMPLE)
        )
    ).all()
    per_unit = [
        (ensure_utc(finished) - ensure_utc(started)).total_seconds() / float(planned)
        for started, finished, planned in rows
        if planned and planned > 0
    ]
    if per_unit:
        return median(per_unit) * float(quantity)

    return DEFAULT_JOB_DAYS * 24 * 3600


@dataclass
class JobAlertLine:
    wo_id: uuid.UUID
    wo_number: int
    item_sku: str
    item_name: str
    # late_to_start | stalled
    reason: str
    days_over: float


async def compute_job_alerts(db: AsyncSession, org_id: uuid.UUID) -> list[JobAlertLine]:
    """Jobs that have gone quiet: scheduled past their date, or running far
    longer than a job like them should. A flag on a page nobody opens is not
    chasing anything, so these ride the morning brief the shop already
    reads."""
    now = datetime.now(timezone.utc)
    today = now.date()
    rows = (
        await db.execute(
            select(WorkOrder, Item)
            .join(Item, Item.id == WorkOrder.item_id)
            .where(WorkOrder.org_id == org_id, WorkOrder.status.in_(PLANNED_STATUSES))
            .order_by(WorkOrder.wo_number)
        )
    ).all()

    alerts: list[JobAlertLine] = []
    for job, item in rows:
        if job.status == "scheduled":
            if job.scheduled_start is not None and job.scheduled_start < today:
                alerts.append(
                    JobAlertLine(
                        wo_id=job.id,
                        wo_number=job.wo_number,
                        item_sku=item.sku,
                        item_name=item.name,
                        reason="late_to_start",
                        days_over=(today - job.scheduled_start).days,
                    )
                )
            continue

        if job.started_at is None:
            continue
        expected = timedelta(
            seconds=await expected_duration_seconds(db, org_id, job.item_id, job.quantity_planned)
        )
        limit = max(expected * STALL_FACTOR, expected + STALL_FLOOR)
        elapsed = now - ensure_utc(job.started_at)
        if elapsed > limit:
            alerts.append(
                JobAlertLine(
                    wo_id=job.id,
                    wo_number=job.wo_number,
                    item_sku=item.sku,
                    item_name=item.name,
                    reason="stalled",
                    days_over=(elapsed - limit).total_seconds() / 86400,
                )
            )
    return alerts
```

Import `Item` from `app.models.bom`. Confirm `ensure_utc` lives in `app.utils.time` by grepping for its definition; `app/services/pace_tracking.py` already imports it, so follow that import path exactly.

- [ ] **Step 4: Put them on the morning brief**

In `backend/app/api/routes/inventory_analytics.py`, add a `JobAlertResponse` model (`wo_id: str`, `wo_number: int`, `item_sku: str`, `item_name: str`, `reason: str`, `days_over: float`), add `job_alerts: list[JobAlertResponse]` to `MorningBriefResponse`, and populate it in `get_morning_brief` from `compute_job_alerts`. Follow exactly how `lot_alerts` is already wired, including its `_lot_alert_to_response` helper style.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_work_order_attention.py -v
```

Expected: all pass.

- [ ] **Step 6: Put the count on the grid summary**

The spec requires the count on the planning grid's summary bar too, because the morning brief alone is still a page someone has to open.

Add `jobs_needing_attention: int` to `GridSummaryResponse` in `backend/app/api/routes/planning_grid.py`, populated with `len(await compute_job_alerts(db, current.org_id))`. Add the assertion to `backend/tests/test_planning_grid.py`:

```python
async def test_the_summary_counts_jobs_needing_attention(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(-3))

    grid = await _grid(client, token)
    assert grid["summary"]["jobs_needing_attention"] == 1
```

Then surface it in `frontend/src/pages/Planning.tsx` beside the existing material warnings, wording it as the count followed by `jobs need attention`, linking to `/production`. Add the matching field to the grid summary type in `frontend/src/pages/planning/types.ts`.

- [ ] **Step 7: Run the tests**

```bash
cd backend && python -m pytest tests/test_work_order_attention.py tests/test_planning_grid.py -v
cd frontend && npx vitest run && npx tsc -b
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/work_orders.py backend/app/services/morning_brief.py backend/app/api/routes/inventory_analytics.py backend/app/api/routes/planning_grid.py backend/tests/ frontend/src/pages/Planning.tsx frontend/src/pages/planning/types.ts
git commit -m "feat: chase jobs that are late to start or have gone quiet"
```

---

### Task 9: OEE learns what was being made

**Files:**
- Modify: `backend/app/models/pace_tracking.py`
- Modify: `backend/app/services/pace_tracking.py`
- Modify: `backend/app/api/routes/pace_tracking.py`
- Modify: the Task 1 migration is already applied, so create a second migration: `backend/alembic/versions/<rev2>_pace_session_work_order.py`
- Test: `backend/tests/test_pace_work_order_link.py` (new)

**Interfaces:**
- Consumes: `WorkOrder` from Task 1.
- Produces: `PaceSession.work_order_id`; a `product` column on the materialised dataset.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pace_work_order_link.py`. Copy `_create_item`, `_create_active_bom`, `_raise_job`, `_start` and `_give_stock` from `backend/tests/test_work_orders.py`, and note that `test_pace_tracking.py`'s `_register_and_login` takes the email positionally rather than as a keyword.

```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="pacewo@example.com", org_name="PaceOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _start_session(client, headers, work_order_id=None):
    body = {"line": "Line 1", "operator": "J. Alvarez", "target_rate": None}
    if work_order_id is not None:
        body["work_order_id"] = work_order_id
    res = await client.post("/pace/sessions", headers=headers, json=body)
    return res


async def _run_and_stop(client, headers, session_id, good=5):
    for _ in range(good):
        tap = await client.post(
            f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"}
        )
        assert tap.status_code == 200
    stopped = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    assert stopped.status_code == 200, stopped.text
    return stopped.json()


async def _dataset_columns(client, headers, dataset_id):
    res = await client.get(f"/datasets/{dataset_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _running_job(client, token):
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    assert (await _start(client, token, job)).status_code == 200
    return job, pump


async def test_a_session_can_be_started_from_a_job(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    job, pump = await _running_job(client, token)

    res = await _start_session(client, headers, work_order_id=job["id"])
    assert res.status_code == 201, res.text
    assert res.json()["work_order_id"] == job["id"]


async def test_a_linked_sessions_dataset_carries_the_product(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    job, pump = await _running_job(client, token)

    session = await _start_session(client, headers, work_order_id=job["id"])
    stopped = await _run_and_stop(client, headers, session.json()["id"])

    dataset = await _dataset_columns(client, headers, stopped["dataset_id"])
    assert "product" in str(dataset), dataset
    assert pump["sku"] in str(dataset), "the product column must carry the item's SKU"


async def test_an_unlinked_sessions_dataset_is_unchanged(client):
    """The backward-compatibility guard: every shipped OEE dashboard reads
    these datasets, so a session with no job must produce exactly the
    columns it produces today."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    session = await _start_session(client, headers)
    assert session.status_code == 201
    stopped = await _run_and_stop(client, headers, session.json()["id"])

    dataset = await _dataset_columns(client, headers, stopped["dataset_id"])
    assert "product" not in str(dataset)


async def test_a_session_cannot_link_to_another_orgs_job(client):
    token_a = await _register_and_login(client)
    job, _pump = await _running_job(client, token_a)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    res = await _start_session(
        client, {"Authorization": f"Bearer {token_b}"}, work_order_id=job["id"]
    )
    assert res.status_code in (400, 404), res.text
```

The two `str(dataset)` assertions are deliberately loose because the dataset read endpoint's exact response shape is not restated here. Before implementing, run `test_pace_tracking.py` and look at what `GET /datasets/{id}` actually returns, then tighten both to assert on the real column list. Leaving them loose is acceptable only if that response genuinely has no stable column field.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest tests/test_pace_work_order_link.py -v
```

- [ ] **Step 3: Add the column and migration**

Add to `PaceSession` in `backend/app/models/pace_tracking.py`:

```python
    # Nullable on purpose: a session started the existing free-text way has
    # no job, and every already-shipped session predates this column. When
    # set, it is what lets OEE say which product a session was running.
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("work_orders.id"), nullable=True
    )
```

Create the migration with `down_revision` set to the Task 1 revision. It adds one nullable column; `downgrade()` drops it. No entitlement backfill is needed, so this migration declares no `GRANTS_FEATURES`.

- [ ] **Step 4: Accept the link when starting a session**

In `backend/app/api/routes/pace_tracking.py`, add optional `work_order_id: str | None` to the start request. Validate it exists in this org (400 in house voice if not) and store it. Include the job number and product SKU on the session response so the UI can show what a session is running.

- [ ] **Step 5: Carry the product into the dataset**

In `backend/app/services/pace_tracking.py`'s `stop_session`, after the existing `target_rate` block, append the product column the same conditional way:

```python
    # Same conditional shape as target_rate above: an unlinked session's CSV
    # keeps exactly the columns it has today, so every shipped OEE dashboard
    # reading these datasets is unaffected, while a linked session gains the
    # one column that lets OEE group by what was actually being made.
    if session.work_order_id is not None:
        header.append("product")
        row.append(product_sku)
```

Load `product_sku` with one query joining `WorkOrder` to `Item` on `session.work_order_id`, before the CSV is built.

- [ ] **Step 6: Run the tests and the pace regression**

```bash
cd backend && python -m pytest tests/test_pace_work_order_link.py tests/test_pace_tracking.py -v
```

Expected: all pass, with the existing pace tests unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/pace_tracking.py backend/app/services/pace_tracking.py backend/app/api/routes/pace_tracking.py backend/tests/test_pace_work_order_link.py backend/alembic/versions/
git commit -m "feat: a pace session can say which job it was running"
```

---

### Task 10: The Production page

**Files:**
- Create: `frontend/src/pages/Production.tsx`, `frontend/src/pages/production/types.ts`, `frontend/src/pages/production/JobsPanel.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/lib/nav.ts`, `frontend/src/lib/status.ts`, `frontend/src/lib/features.ts`, `frontend/vite.config.ts`
- Test: `frontend/src/pages/Production.render.test.tsx`

**Interfaces:**
- Consumes: the Task 2 to 6 endpoints.
- Produces: route `/production`, nav entry gated on `featureKey: "production"`.

- [ ] **Step 1: Write the failing render test**

Create `frontend/src/pages/Production.render.test.tsx`, following `frontend/src/pages/Sales.render.test.tsx` exactly for the harness (`renderToString`, `MemoryRouter`, `QueryClientProvider`, the `.split("<!-- -->").join("")` comment-stripping trick, and seeding `["org-me"]` with an owner role).

Cover: the page renders with no data; a scheduled job shows its number, product, planned quantity and a Start action; a running job shows Finish and Cancel instead of Start; a done job shows neither; a late scheduled job carries a Late badge; a member role sees no action buttons; and the empty state appears when there are no jobs.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npx vitest run src/pages/Production.render.test.tsx
```

- [ ] **Step 3: Write the types**

Create `frontend/src/pages/production/types.ts` with `WorkOrderComponent` and `WorkOrder` interfaces mirroring the backend response models exactly. Quantities are Decimal strings, so reuse the display helper: import `trimQty` from `../planning/types` rather than writing a second one.

- [ ] **Step 4: Add the status colours**

In `frontend/src/lib/status.ts`, add `woStatusVariant` beside the existing `poStatusVariant` and `soStatusVariant`, mapping `scheduled` to a neutral variant, `in_progress` to a working one, `done` to success and `cancelled` to muted. Reuse the house helper rather than inlining colours.

- [ ] **Step 5: Write the panel**

Create `frontend/src/pages/production/JobsPanel.tsx`, mirroring `frontend/src/pages/sales/OrdersPanel.tsx`. Read that file first. Requirements:

- Query key `["work-orders", search, statusFilter]`. The list endpoint returns components, so rows need no per-row fetch; Phase 2 shipped that N+1 and it was caught in review.
- `Table`/`TableWrap` for the list, `EmptyState` with a `lucide-react` icon when empty, `Alert variant="destructive"` for errors.
- Status via `Badge` and `woStatusVariant`. A scheduled job whose `scheduled_start` has passed also carries `<Badge variant="danger">Late</Badge>`, the same treatment `OrdersPanel` gives an overdue order.
- Actions per status: `scheduled` gets Start, Edit and Cancel; `in_progress` gets Finish and Cancel; `done` and `cancelled` get none.
- Finish opens a `Dialog` carrying good, scrap and scrap-reason inputs, plus a lot-number input when the product is lot tracked. When good plus scrap is below the planned quantity, the dialog additionally requires the remainder question, worded plainly: were the unmade units' components still on the shelf, or used up. Do not submit without an answer; the backend will 400.
- Cancel opens a `Dialog` whose copy states that the components go back as though the job never ran, and that a job where any work happened should be finished instead.
- Surface the backend's `detail` string verbatim on failure; the 409s are written to be read by the user.
- Gate every action on `useCurrentUser()`, showing them only for owner or manager, following `Planning.tsx`.
- Invalidate `["work-orders"]` and `["planning-grid"]` on every successful mutation, since all of them move stock or supply.

- [ ] **Step 6: Write the page and register it**

Create `frontend/src/pages/Production.tsx` rendering a `PageHeader` and the panel. It has one panel, so it needs no tab hub; do not copy `Sales.tsx`'s `PageTabs` scaffolding for a single tab.

In `frontend/src/App.tsx`:

```tsx
const Production = lazyPage(() => import("./pages/Production"), "Production");
```
```tsx
<Route path="/production" element={<RequireFeature feature="production"><Production /></RequireFeature>} />
```

In `frontend/src/lib/nav.ts`, add to the Operations section, importing `Factory` from `lucide-react` in the existing alphabetical import block:

```ts
      { to: "/production", label: "Production", icon: Factory, keywords: ["work orders", "jobs", "make", "build", "wo"], featureKey: "production" },
```

In `frontend/src/lib/features.ts`, add `production` alongside the other keys so the frontend registry matches the backend; `backend/tests/test_feature_registry_sync.py` fails otherwise.

In `frontend/vite.config.ts`, add `work-orders` to the proxy alternation. Read the comment in that file and follow its stated rule. `/production` is an SPA route with no backend prefix of the same name, so it does not go in the vite alternation; check `frontend/nginx.conf.template`'s location regex and state in your report what you concluded.

- [ ] **Step 7: Run the tests and type-check**

```bash
cd frontend && npx vitest run
cd frontend && npx tsc -b
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/Production.tsx frontend/src/pages/production/ frontend/src/pages/Production.render.test.tsx frontend/src/App.tsx frontend/src/lib/nav.ts frontend/src/lib/status.ts frontend/src/lib/features.ts frontend/vite.config.ts
git commit -m "feat: Production page for work orders"
```

---

### Task 11: The grid closes the loop

**Files:**
- Modify: `backend/app/services/planning_grid.py`, `backend/app/api/routes/planning_grid.py`
- Modify: `frontend/src/pages/planning/types.ts`, `frontend/src/pages/planning/PlannedQtyInput.tsx`, `frontend/src/pages/Planning.tsx`
- Test: `backend/tests/test_planning_grid.py` (extend), `frontend/src/pages/Planning.render.test.tsx` (extend)

**Interfaces:**
- Consumes: `load_production_supply` from Task 7, `POST /work-orders` with `start_now` from Task 4.
- Produces: `GridProduct.in_production` and `GridProduct.can_make`; a Make button on each row.

- [ ] **Step 1: Write the failing backend tests**

Append to `backend/tests/test_planning_grid.py`, reusing that file's helpers and adding the job helpers from `test_work_orders.py`:

```python
async def test_the_row_shows_what_is_in_production(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 50)
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=20)
    await _start(client, token, job)

    row = _product(await _grid(client, token), "PUMP-1")
    assert Decimal(row["in_production"]) == Decimal("20")


async def test_a_row_with_materials_can_be_made(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, seal, 100)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is True


async def test_a_row_short_of_materials_cannot_be_made(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False, "offering Make on a job that would 409 is a trap"


async def test_a_product_with_no_recipe_cannot_be_made(client):
    token = await _register_and_login(client)
    pump = await _create_item(client, token, "PUMP-1")
    await _plan(client, token, pump, 10)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v -k "production or can_make or made"
```

Expected: `KeyError`.

- [ ] **Step 3: Plumb both fields through the grid**

In `backend/app/services/planning_grid.py`:

- Add `in_production: Decimal` to `GridProduct`, with a comment saying it is units already being made, as opposed to `planned_qty` which is the estimate someone typed and `committed_qty` which is what customers ordered.
- Add `can_make: bool`, with a comment that it is false when the product has no active recipe or its components are short, because offering Make on a job that would only 409 is a trap.
- Load `production_by_item = await load_production_supply(db, org_id, horizon_end)` near the existing committed-demand load.
- Compute `can_make` per product: it has an active BOM, and every component's requirement for the row's outstanding quantity is covered by on-hand less what higher-priority scheduled jobs hold. Reuse `component_requirements` and `reserved_by_higher_priority`-equivalent logic from `app.services.work_orders`; do not write a second copy of the arithmetic here.

In `backend/app/api/routes/planning_grid.py`, add both fields to `GridProductResponse` and `_product_to_response`, and to the `PUT` endpoint's fallback response object (`in_production=Decimal("0.0000")`, `can_make=False`).

- [ ] **Step 4: Write the failing frontend test**

Extend `frontend/src/pages/Planning.render.test.tsx`. Add `in_production` and `can_make` to the `GRID` fixture's products, then cover: a row with `can_make` true renders a Make action naming the quantity; a row with `can_make` false renders it disabled; a row with `in_production` above zero renders a line reading `20 in production`; and a member-role user, whose cell renders read-only, sees the in-production line but no Make button. That last case matters: the read-only branch is the easiest one to miss, which is exactly what happened with the committed line in Phase 2.

- [ ] **Step 5: Run it to verify it fails**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

- [ ] **Step 6: Implement the frontend**

In `frontend/src/pages/planning/types.ts`, add to `GridProduct`:

```ts
  /** Units already being made, as opposed to planned_qty which is the
   * estimate someone typed and committed_qty which is what customers
   * ordered. */
  in_production: string;
  /** False when the product has no active recipe or its components are
   * short, so Make is offered only when it would actually succeed. */
  can_make: boolean;
```

In `frontend/src/pages/planning/PlannedQtyInput.tsx`, render the in-production line whenever `Number(product.in_production) > 0`, in BOTH the `readOnly` early-return branch and the editable branch, matching how the committed line is already handled there. Keep it quiet and secondary, worded as the number followed by `in production`.

Add the Make button to the editable branch only. It posts to `/work-orders` with `start_now: true` and the quantity still outstanding after `in_production`, then invalidates `["planning-grid"]` and `["work-orders"]`. Disable it when `can_make` is false or the outstanding quantity is zero, and when it is zero say a job is already covering the row rather than offering Make again. Surface the backend `detail` verbatim on failure.

- [ ] **Step 7: Run everything**

```bash
cd backend && python -m pytest -q
cd frontend && npx vitest run
cd frontend && npx tsc -b
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/planning_grid.py backend/app/api/routes/planning_grid.py backend/tests/test_planning_grid.py frontend/src/pages/planning/types.ts frontend/src/pages/planning/PlannedQtyInput.tsx frontend/src/pages/Planning.render.test.tsx
git commit -m "feat: make what the grid says you need, from the grid"
```

---

## Post-implementation

- [ ] Run both suites: `cd backend && python -m pytest -q` and `cd frontend && npx vitest run`, plus `npx tsc -b`.
- [ ] Verify in a browser: raise a job from the grid, watch the row's in-production line appear and the components come off stock, finish it short of plan and answer the remainder question, and confirm the finished units arrive in inventory. This needs a login, so it is the human's step.
- [ ] Run `graphify update .` to refresh the knowledge graph.
- [ ] Use `superpowers:requesting-code-review` for a whole-branch review before merging.
