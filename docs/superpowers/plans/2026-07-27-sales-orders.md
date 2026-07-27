# Sales Orders Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make real customer orders a demand source, so the planning grid turns red because a commitment is at risk rather than because a typed estimate is.

**Architecture:** Three new tables mirror the buy side (`Customer`, `SalesOrder`, `SalesOrderLine` against `Supplier`, `PurchaseOrder`, `PurchaseOrderLine`). A new `app/services/demand.py` owns loading each demand source and combining them under an org policy; `compute_mrp_plan` seeds from the combined map so the engine, the build calculator and generated POs cannot disagree. A new Sales page mirrors Procurement's tab hub. Completing an order flips status and removes finished goods through the existing stock paths.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + Pydantic v2 (backend); React + TypeScript + TanStack Query + Tailwind (frontend); pytest + vitest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-27-sales-orders-design.md`. Read it before Task 1.
- Order status values are exactly `open` | `done` | `cancelled`. Only `open` counts as demand.
- Demand policy values are exactly `consume` (default) and `add`. Stored as a string, not a boolean.
- No money anywhere: no price, tax, discount or currency column, field or input.
- No partial shipments, no delivery addresses, no per-order stock allocation.
- New Alembic migration's `down_revision` is `c7a3d5e91f42` (the item-safety-stock revision, committed as a prerequisite to this plan). Verify with `cd backend && python -m alembic heads` before writing it; if the head is not `c7a3d5e91f42`, stop and report rather than guessing.
- User-facing `HTTPException` details follow house voice: lowercase, terse, no trailing period, concrete numbers or SKU interpolated. 400 for malformed input, 409 for state or stock conflicts.
- No new dependencies, backend or frontend.
- No em dashes in code, comments, docstrings or user-visible copy.
- Backend tests run from `backend/`: `python -m pytest tests/<file> -v`. Frontend from `frontend/`: `npx vitest run` and `npx tsc -b`.
- Two tests in `backend/tests/test_admin_customers.py` fail intermittently for reasons unrelated to this work. If only those fail, say so explicitly; any other failure blocks the task.
- The working tree carries unrelated uncommitted work. Stage only files you change, and only your own hunks in a file that already has unrelated changes. Never `git add -A` or `git add .`.

---

### Task 1: Models, migration, feature key and the demand policy setting

**Files:**
- Create: `backend/app/models/sales.py`
- Create: `backend/alembic/versions/<new_rev>_sales_orders.py`
- Modify: `backend/app/models/tenant.py` (add `demand_policy` to `Org`)
- Modify: `backend/app/core/features.py` (add the `sales` key)
- Modify: `backend/app/api/routes/orgs.py` (expose and validate `demand_policy`)
- Modify: `backend/tests/conftest.py` (register the new model module)
- Test: `backend/tests/test_sales_orders.py` (new, one migration/setting test for now)

**Interfaces:**
- Produces: `Customer`, `SalesOrder`, `SalesOrderLine` in `app.models.sales`; `Org.demand_policy: str`; feature key `"sales"`; `GET/PATCH /orgs/me` carrying `demand_policy`.

- [ ] **Step 1: Confirm the migration parent**

```bash
cd backend && python -m alembic heads
```

Expected: `c7a3d5e91f42 (head)`. If it differs, stop and report; do not invent a parent.

- [ ] **Step 2: Write the models**

Create `backend/app/models/sales.py`. Mirror `backend/app/models/procurement.py` exactly in style: same import set, same column conventions, comments that say *why* rather than *what*.

```python
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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


class Customer(Base):
    """Who an order is for. Mirrors Supplier on the buy side, including the
    soft-delete-only rule: a customer is deactivated, never deleted, so past
    orders keep a valid customer_id instead of being orphaned.
    """

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_customers_org_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Deactivated customers stay on their past orders and simply drop out of
    # the order-entry picker, the same way Supplier.is_active works.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SalesOrder(Base):
    """A customer's commitment to buy. This is demand, not an invoice: there is
    deliberately no price, tax or currency anywhere on the sell side, because
    retrofitting those semantics onto historical rows is not reconstructable
    (same reasoning as PurchaseOrderLine.unit_price documents on the buy side).
    """

    __tablename__ = "sales_orders"
    __table_args__ = (UniqueConstraint("org_id", "so_number", name="uq_sos_org_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Server-computed (max so_number for this org + 1) at creation, never
    # user-supplied. Plain int, not a padded string like "SO-0004" - format that
    # at the response or frontend layer, never store the formatted string.
    so_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # open | done | cancelled. Only `open` counts as demand, so this column is
    # the single switch that puts an order into or out of the plan.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    # When the customer needs it. This is what drives planning dates, so it is
    # required rather than nullable: an order with no date cannot be planned.
    needed_by: Mapped[date] = mapped_column(Date, nullable=False)
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


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    so_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False)
    quantity_ordered: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Mirrors PurchaseOrderLine.quantity_received. Partial shipments are out of
    # scope, so today this is only ever 0 or the full ordered quantity, and the
    # outstanding-quantity subtraction in services.demand is a no-op on every
    # open order. It still earns its place: it records what actually left the
    # building rather than inferring it from status, and a later partial
    # -shipment feature builds on it without revisiting the demand maths.
    quantity_delivered: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

- [ ] **Step 3: Add the org setting**

In `backend/app/models/tenant.py`, add to `Org` directly below `adjust_approval_threshold`:

```python
    # consume | add. How committed demand (open sales orders) combines with
    # estimated demand (typed or imported forecasts) when planning. `consume`
    # plans the larger of the two, so orders count towards the estimate rather
    # than on top of it and materials are not bought twice for the same sale.
    # `add` sums them, for a shop whose estimate deliberately excludes booked
    # work. A string rather than a boolean so a third policy needs no migration.
    # See app.services.demand.combine_demand.
    demand_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="consume")
```

- [ ] **Step 4: Add the feature key**

In `backend/app/core/features.py`, add to the `FEATURES` dict beside the other core operations keys:

```python
    "sales": ("Sales Orders", "core"),
```

Then read the rest of that module and `backend/tests/conftest.py` to determine how an org comes to have a feature enabled in tests. Report in your task report exactly which mechanism grants `sales` to a freshly registered test org, because later tasks' tests depend on it. Do not change that mechanism; just establish and record it.

- [ ] **Step 5: Register the model module for tests**

`backend/tests/conftest.py` imports every model module so `Base.metadata.create_all` sees the tables. Add `sales` to that import list, matching the existing style (the list carries a `# noqa: F401 - register models` comment).

- [ ] **Step 6: Write the migration**

Create `backend/alembic/versions/<rev>_sales_orders.py`. Pick a 12-hex-character revision id. Follow the house header exactly, including the secrets-scanner pragma:

```python
"""sales orders and customers

Revision ID: <rev>
Revises: c7a3d5e91f42
Create Date: 2026-07-27

"""
from alembic import op
import sqlalchemy as sa

revision = "<rev>"  # pragma: allowlist secret
down_revision = "c7a3d5e91f42"  # pragma: allowlist secret
branch_labels = None
depends_on = None
```

`upgrade()` creates the three tables plus the `orgs.demand_policy` column. Follow `backend/alembic/versions/1e284768aa62_procurement.py` for style: inline unnamed column constraints, one explicit `op.create_index("ix_<table>_<col>", ...)` per indexed column, and `op.create_unique_constraint` with the exact names from the models (`uq_customers_org_name`, `uq_sos_org_number`). Every NOT NULL column added to an existing table needs a `server_default`, so `demand_policy` gets `server_default="consume"`. Indexes required, matching `index=True` in the models: `ix_customers_org_id`, `ix_sales_orders_org_id`, `ix_sales_orders_customer_id`, `ix_sales_orders_status`, `ix_sales_order_lines_org_id`, `ix_sales_order_lines_so_id`.

`downgrade()` drops in reverse dependency order: `sales_order_lines`, `sales_orders`, `customers`, then the `orgs.demand_policy` column.

- [ ] **Step 7: Expose the setting on the org API**

In `backend/app/api/routes/orgs.py`, follow exactly how `adjust_approval_threshold` is handled: add `demand_policy: str` to `OrgResponse`, add it as an optional field on the PATCH request model, validate it is one of `consume` or `add` (rejecting anything else with 400 and a plain message), assign it, include it in the audit metadata, and return it. The PATCH endpoint's existing role gate applies unchanged; confirm by reading it that the gate is owner-only, since this setting changes what every planning number in the app means.

- [ ] **Step 8: Write the tests**

Create `backend/tests/test_sales_orders.py`. Use the house helpers, copied in as the other test modules do rather than imported:

```python
from __future__ import annotations

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


async def test_new_org_defaults_to_consuming_demand(client):
    token = await _register_and_login(client)
    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["demand_policy"] == "consume"


async def test_demand_policy_can_be_set_to_add(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.patch("/orgs/me", json={"demand_policy": "add"}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["demand_policy"] == "add"
    assert (await client.get("/orgs/me", headers=headers)).json()["demand_policy"] == "add"


async def test_unknown_demand_policy_is_rejected(client):
    token = await _register_and_login(client)
    res = await client.patch(
        "/orgs/me", json={"demand_policy": "whatever"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 400
```

- [ ] **Step 9: Run the tests**

```bash
cd backend && python -m pytest tests/test_sales_orders.py -v
```

Expected: 3 passed. Then the full suite:

```bash
cd backend && python -m pytest -q
```

Expected: no new failures beyond the two known-flaky admin tests.

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/sales.py backend/app/models/tenant.py backend/app/core/features.py backend/app/api/routes/orgs.py backend/tests/conftest.py backend/tests/test_sales_orders.py backend/alembic/versions/
git commit -m "feat: sales order models, migration and demand policy setting"
```

---

### Task 2: Customers CRUD

**Files:**
- Create: `backend/app/api/routes/customers.py`
- Modify: `backend/app/main.py` (register the router behind the `sales` feature)
- Test: `backend/tests/test_customers.py`

**Interfaces:**
- Consumes: `Customer` from Task 1.
- Produces: `POST /customers`, `GET /customers` (with `q`, `active_only`, `limit`, `offset`), `PATCH /customers/{id}`, `POST /customers/{id}/deactivate`, `POST /customers/{id}/reactivate`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_customers.py` with the same `PASSWORD` constant and `_register_and_login` helper as Task 1, plus:

```python
async def _create_customer(client, token, name="Northwind Ltd"):
    res = await client.post(
        "/customers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_create_and_list_customer(client):
    token = await _register_and_login(client)
    created = await _create_customer(client, token)
    assert created["name"] == "Northwind Ltd"
    assert created["is_active"] is True

    res = await client.get("/customers", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert [c["name"] for c in res.json()] == ["Northwind Ltd"]


async def test_duplicate_customer_name_is_rejected(client):
    token = await _register_and_login(client)
    await _create_customer(client, token)
    res = await client.post(
        "/customers", json={"name": "Northwind Ltd"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 409


async def test_deactivated_customer_is_hidden_from_the_picker(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)

    res = await client.post(f"/customers/{customer['id']}/deactivate", headers=headers)
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    active = await client.get("/customers?active_only=true", headers=headers)
    assert active.json() == []
    everyone = await client.get("/customers", headers=headers)
    assert len(everyone.json()) == 1


async def test_reactivate_restores_the_customer(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    await client.post(f"/customers/{customer['id']}/deactivate", headers=headers)

    res = await client.post(f"/customers/{customer['id']}/reactivate", headers=headers)
    assert res.status_code == 200
    assert res.json()["is_active"] is True


async def test_customers_are_org_scoped(client):
    token_a = await _register_and_login(client)
    await _create_customer(client, token_a)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    res = await client.get("/customers", headers={"Authorization": f"Bearer {token_b}"})
    assert res.json() == []
```

Add a role-gating test following the pattern in `backend/tests/test_supplier_lifecycle.py::test_deactivate_supplier_requires_owner`: read that test, and construct a second `User` with `role="member"` directly against the test DB the way it does (there is no API to create a non-owner). Assert the member gets 403 from `POST /customers` and 200 from `GET /customers`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_customers.py -v
```

Expected: all fail with 404, since `/customers` does not exist.

- [ ] **Step 3: Write the route module**

Create `backend/app/api/routes/customers.py`, mirroring `backend/app/api/routes/suppliers.py`. Read that file first and follow it closely. Specifically:

- `router = APIRouter(prefix="/customers", tags=["customers"])`
- `CustomerCreateRequest` with `name: str` required and `email`, `phone` optional; `CustomerUpdateRequest(CustomerCreateRequest)` subclassing it so PATCH takes the full field set, exactly as suppliers does.
- `CustomerResponse` with `id` as `str`, mirroring the columns minus `created_at`.
- Role gates: mutations `require_role("owner", "manager")`; reads `get_current_user`. Note this is deliberately broader than suppliers, which is owner-only, because the spec makes order entry a shop-floor supervisor action.
- On `IntegrityError` from the `uq_customers_org_name` constraint, roll back and raise 409 with a message in house voice, mirroring how `create_supplier` handles the duplicate-name case.
- A shared `_set_active(...)` helper behind `deactivate` and `reactivate`, as suppliers has, so the two cannot drift.
- No delete endpoint. Deactivation only, so orders are never orphaned.
- Audit events `customer.created`, `customer.updated`, `customer.activated`, `customer.deactivated` via `log_event`.

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, import `customers` alongside the other route modules and register it beside the other operations routers:

```python
app.include_router(customers.router, dependencies=[Depends(require_feature("sales"))])
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_customers.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/customers.py backend/app/main.py backend/tests/test_customers.py
git commit -m "feat: customer records for the sell side"
```

---

### Task 3: Sales order CRUD and number allocation

**Files:**
- Create: `backend/app/api/routes/sales_orders.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_sales_orders.py` (extend)

**Interfaces:**
- Consumes: `Customer`, `SalesOrder`, `SalesOrderLine` from Task 1.
- Produces: `POST /sales-orders`, `GET /sales-orders`, `GET /sales-orders/{id}`, `PATCH /sales-orders/{id}` (replace lines), `POST /sales-orders/{id}/cancel`. Completion is Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_sales_orders.py`. You will need an item helper; copy the shape used by `backend/tests/test_planning_grid.py::_create_item`.

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


async def _create_order(client, token, customer, item, qty=10, needed_by="2030-06-01"):
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": needed_by,
            "lines": [{"item_id": item["id"], "quantity_ordered": str(qty), "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_order_numbers_allocate_sequentially_per_org(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")

    numbers = [
        (await _create_order(client, token, customer, item))["so_number"] for _ in range(3)
    ]
    assert numbers == [1, 2, 3]


async def test_order_requires_at_least_one_line(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    res = await client.post(
        "/sales-orders",
        json={"customer_id": customer["id"], "needed_by": "2030-06-01", "lines": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_order_rejects_a_nonpositive_quantity(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": "2030-06-01",
            "lines": [{"item_id": item["id"], "quantity_ordered": "0", "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_order_rejects_an_unknown_customer(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "WIDGET-1")
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": "00000000-0000-0000-0000-000000000000",
            "needed_by": "2030-06-01",
            "lines": [{"item_id": item["id"], "quantity_ordered": "5", "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_cancelling_an_order_sets_cancelled(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    order = await _create_order(client, token, customer, item)

    res = await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"

    again = await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)
    assert again.status_code == 409


async def test_orders_are_org_scoped(client):
    token_a = await _register_and_login(client)
    customer = await _create_customer(client, token_a)
    item = await _create_item(client, token_a, "WIDGET-1")
    order = await _create_order(client, token_a, customer, item)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    headers_b = {"Authorization": f"Bearer {token_b}"}
    assert (await client.get("/sales-orders", headers=headers_b)).json() == []
    assert (await client.get(f"/sales-orders/{order['id']}", headers=headers_b)).status_code == 404
```

Also add the `_create_customer` helper to this file (same body as Task 2's).

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_sales_orders.py -v -k "order"
```

Expected: fail with 404.

- [ ] **Step 3: Write the route module**

Create `backend/app/api/routes/sales_orders.py`, mirroring `backend/app/api/routes/purchase_orders.py`. Read that file first. Requirements:

- `router = APIRouter(prefix="/sales-orders", tags=["sales-orders"])`
- Role gates: mutations `require_role("owner", "manager")`, reads `get_current_user`.
- `so_number` allocation must use the same race guard as `create_purchase_order`: a module constant `MAX_SO_NUMBER_ATTEMPTS = 3`, a loop reading `select(func.max(SalesOrder.so_number)).where(SalesOrder.org_id == org_id)`, and the insert wrapped in `async with db.begin_nested():` so an `IntegrityError` retry does not expire already-loaded objects (a full rollback would, and the later `customer.name` read while building the response would then trigger an async refresh and a `MissingGreenlet`). On final failure raise 409 in house voice.
- Creation takes the customer, `needed_by`, and at least one line, in one request. Unlike a purchase order, an order is not created empty and then filled: there is no draft state, so a line-less order is rejected at creation with 400.
- Validate: customer exists in this org and is active (400 if not), at least one line (400), each `quantity_ordered > 0` (400), each `item_id` exists in this org (400), `needed_by` is a required date.
- `PATCH` replaces all lines, allowed only while `status == "open"`, else 409 in house voice.
- `cancel` allowed only from `open`, else 409. It moves no stock.
- Response models carry `so_number`, `customer_id`, `customer_name`, `status`, `needed_by`, `notes`, and the lines with `item_id`, `item_sku`, `item_name`, `quantity_ordered`, `quantity_delivered`, `sequence`. `id` fields serialize as `str`.
- `GET /sales-orders` supports `q` (matching customer name or `so_number` cast to string, as the PO list does), a `status` filter, `limit` (1-200, default 50) and `offset`, ordered by `so_number`.
- Audit events `sales_order.created`, `sales_order.updated`, `sales_order.cancelled`.

- [ ] **Step 4: Register the router**

```python
app.include_router(sales_orders.router, dependencies=[Depends(require_feature("sales"))])
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_sales_orders.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/sales_orders.py backend/app/main.py backend/tests/test_sales_orders.py
git commit -m "feat: sales orders with server-allocated numbers"
```

---

### Task 4: Completing an order removes the goods from stock

**Files:**
- Modify: `backend/app/api/routes/sales_orders.py`
- Test: `backend/tests/test_sales_orders.py` (extend)

**Interfaces:**
- Consumes: `apply_inventory_delta` from `app.services.inventory`, `consume_lot_fefo` from `app.services.lots`, `resolve_location_id` from `app.services.locations`.
- Produces: `POST /sales-orders/{id}/complete`.

**Critical context.** There is no single consume path. `apply_inventory_delta` maintains `InventoryBalance` and works only for non-lot-tracked items; every route that calls it first rejects lot-tracked items. Lot-tracked stock lives in a separate subsystem and is consumed with `consume_lot_fefo`, which draws from active, unheld lots earliest-expiry-first with oldest-received as the tiebreak. So completion must branch per line on `item.is_lot_tracked`. Both paths already raise 409 with good messages when short, so do not write your own shortfall check; let theirs surface.

A sales order has no location. Resolve one with `resolve_location_id(db, org_id, None)`, which yields the org's default location, the same fallback `/inventory/{item_id}/consume` uses when the caller omits it.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_sales_orders.py`. Stock is seeded through an owner inventory adjustment, which applies immediately rather than queueing (owners never queue):

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


async def test_completing_an_order_removes_the_stock(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 50)
    order = await _create_order(client, token, customer, item, qty=20)

    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "done"
    assert body["lines"][0]["quantity_delivered"] == "20.0000"

    inv = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("30")


async def test_completing_without_enough_stock_is_refused_and_moves_nothing(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 5)
    order = await _create_order(client, token, customer, item, qty=20)

    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 409

    inv = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("5"), "a refused completion must move no stock"
    still = await client.get(f"/sales-orders/{order['id']}", headers=headers)
    assert still.json()["status"] == "open", "a refused completion must leave the order open"


async def test_completing_twice_is_refused(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 50)
    order = await _create_order(client, token, customer, item, qty=10)

    assert (await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)).status_code == 200
    again = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert again.status_code == 409

    inv = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("40"), "stock must come off once, not twice"


async def test_a_cancelled_order_cannot_be_completed(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 50)
    order = await _create_order(client, token, customer, item, qty=10)
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 409


async def test_completing_a_lot_tracked_order_draws_from_lots(client):
    """A lot-tracked finished good cannot go through apply_inventory_delta at
    all, so this pins the other branch."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "TRACKED-1", is_lot_tracked=True)

    # Lot-tracked stock only enters through a received purchase order.
    await _receive_into_lot(client, token, item, qty=50, lot_number="LOT-A")

    order = await _create_order(client, token, customer, item, qty=20)
    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 200, res.text

    lots = await client.get("/lots", headers=headers)
    remaining = sum(Decimal(lot["quantity_remaining"]) for lot in lots.json())
    assert remaining == Decimal("30")
```

`_receive_into_lot` does not exist in this module yet. Copy it verbatim from `backend/tests/test_mrp_explosion.py`, where it is already defined, then adapt only its signature to take `qty` and `lot_number` as keyword arguments if it does not already. Read that function before writing the test; do not reconstruct it from memory, because the PO receive payload has several required fields (`po_line_id`, `quantity`, `lot_number`) and getting one wrong produces a confusing 400 rather than a clear failure. It needs a supplier, so copy that module's `_create_supplier` helper too.

Import `Decimal` at the top of the test module.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_sales_orders.py -v -k "complet"
```

Expected: fail with 404 on `/complete`.

- [ ] **Step 3: Implement completion**

Add to `backend/app/api/routes/sales_orders.py`. Behaviour:

- Role gate `require_role("owner", "manager")`.
- Row-lock the order with `get_or_404(..., for_update=True)`, as the PO receive route does, so two concurrent completions cannot both pass the status check.
- Refuse unless `status == "open"`, with 409 in house voice.
- Resolve the location once: `location_id = await resolve_location_id(db, current.org_id, None)`.
- For each line, load the item, then branch:
  - `item.is_lot_tracked`: `await consume_lot_fefo(db, org_id=..., item_id=..., location_id=location_id, quantity=outstanding)` and write the `LotTransaction` rows the way `POST /lots/consume-fefo` does. Read that route and mirror it.
  - otherwise: `await apply_inventory_delta(db, org_id=..., item_id=..., location_id=location_id, delta=-outstanding, transaction_type="consumption", created_by=current.id, reason="sales order")`.
  where `outstanding = line.quantity_ordered - line.quantity_delivered`. Skip a line whose outstanding is zero or less.
- Set each line's `quantity_delivered = quantity_ordered`, set the order's `status = "done"`.
- `log_event` with action `sales_order.completed`, target the order id, and metadata carrying the order number and the per-item quantities moved.
- One `await db.commit()` at the end. Because every stock write and the status change share that single transaction, a shortfall on the second line rolls back the first line's movement too. The refusal test above pins this; do not add a pre-flight availability check to work around it.

Add a comment above the branch explaining why two paths exist, so nobody later "simplifies" it into one.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_sales_orders.py -v
```

Expected: all pass.

- [ ] **Step 5: Run the full suite**

```bash
cd backend && python -m pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/sales_orders.py backend/tests/test_sales_orders.py
git commit -m "feat: completing a sales order ships the goods out of stock"
```

---

### Task 5: Committed demand reaches the engine

**Files:**
- Create: `backend/app/services/demand.py`
- Modify: `backend/app/services/mrp.py` (the demand-seeding block in `compute_mrp_plan` only)
- Test: `backend/tests/test_demand_combination.py` (new), `backend/tests/test_sales_orders.py` (extend)

**Interfaces:**
- Produces: `combine_demand(forecast, committed, policy)`, `load_forecast_demand(db, org_id, start, end)`, `load_committed_demand(db, org_id, start, end)`, `load_demand_policy(db, org_id)` in `app.services.demand`.
- Consumed by: Task 6 (`load_committed_demand` for display).

**Critical context.** `compute_mrp_plan` currently returns `[]` early when the forecast map is empty. That gate must move to the combined map, or a shop with real orders and no typed estimates gets an empty plan and a blank grid.

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/test_demand_combination.py`. This is a pure function, so it needs no database:

```python
from __future__ import annotations

import uuid
from decimal import Decimal

from app.services.demand import combine_demand

A = uuid.uuid4()
B = uuid.uuid4()


def test_consume_plans_the_larger_of_the_two():
    result = combine_demand({A: Decimal(100)}, {A: Decimal(60)}, "consume")
    assert result == {A: Decimal(100)}


def test_consume_follows_the_orders_once_they_exceed_the_estimate():
    result = combine_demand({A: Decimal(100)}, {A: Decimal(130)}, "consume")
    assert result == {A: Decimal(130)}


def test_consume_at_the_boundary_where_they_are_equal():
    result = combine_demand({A: Decimal(100)}, {A: Decimal(100)}, "consume")
    assert result == {A: Decimal(100)}


def test_add_sums_them():
    result = combine_demand({A: Decimal(100)}, {A: Decimal(60)}, "add")
    assert result == {A: Decimal(160)}


def test_an_item_present_in_only_one_source_still_appears():
    result = combine_demand({A: Decimal(10)}, {B: Decimal(5)}, "consume")
    assert result == {A: Decimal(10), B: Decimal(5)}


def test_empty_on_both_sides_is_empty():
    assert combine_demand({}, {}, "consume") == {}


def test_committed_only_survives_under_both_policies():
    assert combine_demand({}, {A: Decimal(7)}, "consume") == {A: Decimal(7)}
    assert combine_demand({}, {A: Decimal(7)}, "add") == {A: Decimal(7)}
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest tests/test_demand_combination.py -v
```

Expected: collection error, no module `app.services.demand`.

- [ ] **Step 3: Write the demand service**

Create `backend/app/services/demand.py`:

```python
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forecast import DemandForecast
from app.models.sales import SalesOrder, SalesOrderLine
from app.models.tenant import Org

# Only an open order is a commitment. A cancelled one is off the books and a
# completed one has already shipped, so neither is demand any more.
OPEN_ORDER_STATUS = "open"


def combine_demand(
    forecast: dict[uuid.UUID, Decimal],
    committed: dict[uuid.UUID, Decimal],
    policy: str,
) -> dict[uuid.UUID, Decimal]:
    """One demand figure per item from the two sources.

    `consume` (the default) treats committed demand as part of the estimate
    rather than additional to it, so the answer is whichever is larger. An
    estimate of 100 with orders for 60 plans 100, because those 60 units were
    always expected to sell; orders for 130 plan 130, because reality has
    overtaken the estimate. Adding them instead would buy materials twice for
    the same sale.

    `add` sums them, which is right only when a shop's estimate deliberately
    excludes work already booked.

    Combination is per item across the whole window, not per date bucket,
    because compute_mrp_plan already flattens every forecast bucket in the
    horizon into one figure per item before netting. Two different summing
    rules in one engine would be worse than the precision is worth over 90
    days.
    """
    combined: dict[uuid.UUID, Decimal] = {}
    for item_id in set(forecast) | set(committed):
        estimated = forecast.get(item_id, Decimal(0))
        booked = committed.get(item_id, Decimal(0))
        combined[item_id] = estimated + booked if policy == "add" else max(estimated, booked)
    return combined


async def load_demand_policy(db: AsyncSession, org_id: uuid.UUID) -> str:
    return await db.scalar(select(Org.demand_policy).where(Org.id == org_id)) or "consume"


async def load_forecast_demand(
    db: AsyncSession, org_id: uuid.UUID, horizon_start: date, horizon_end: date
) -> tuple[dict[uuid.UUID, Decimal], dict[uuid.UUID, date]]:
    """Estimated demand: forecast quantity per item, and the earliest dated
    bucket per item, over the window."""
    rows = (
        await db.execute(
            select(
                DemandForecast.item_id,
                func.sum(DemandForecast.quantity),
                func.min(DemandForecast.period_start),
            )
            .where(
                DemandForecast.org_id == org_id,
                DemandForecast.period_start >= horizon_start,
                DemandForecast.period_start <= horizon_end,
            )
            .group_by(DemandForecast.item_id)
        )
    ).all()
    quantities = {item_id: (total or Decimal(0)) for item_id, total, _ in rows}
    dates = {item_id: earliest for item_id, _, earliest in rows if earliest is not None}
    return quantities, dates


async def load_committed_demand(
    db: AsyncSession, org_id: uuid.UUID, horizon_start: date, horizon_end: date
) -> tuple[dict[uuid.UUID, Decimal], dict[uuid.UUID, date]]:
    """Committed demand: outstanding quantity per item across open sales
    orders, and the earliest needed-by date per item, over the window."""
    rows = (
        await db.execute(
            select(
                SalesOrderLine.item_id,
                func.sum(SalesOrderLine.quantity_ordered - SalesOrderLine.quantity_delivered),
                func.min(SalesOrder.needed_by),
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.so_id)
            .where(
                SalesOrder.org_id == org_id,
                SalesOrder.status == OPEN_ORDER_STATUS,
                SalesOrder.needed_by >= horizon_start,
                SalesOrder.needed_by <= horizon_end,
            )
            .group_by(SalesOrderLine.item_id)
        )
    ).all()
    quantities = {
        item_id: total for item_id, total, _ in rows if total is not None and total > 0
    }
    dates = {
        item_id: earliest
        for item_id, total, earliest in rows
        if earliest is not None and total is not None and total > 0
    }
    return quantities, dates


def earliest_dates(
    forecast_dates: dict[uuid.UUID, date], committed_dates: dict[uuid.UUID, date]
) -> dict[uuid.UUID, date]:
    """Per item, the earlier of the two sources' dates. Whichever source is
    more urgent sets the date the plan works back from."""
    merged = dict(forecast_dates)
    for item_id, when in committed_dates.items():
        existing = merged.get(item_id)
        merged[item_id] = when if existing is None else min(existing, when)
    return merged
```

- [ ] **Step 4: Run the unit tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_demand_combination.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Seed the engine from both sources**

In `backend/app/services/mrp.py`, replace the demand-seeding block inside `compute_mrp_plan` (the `independent = await _sum_by_item(...)` query, the `if not independent: return []` gate, and the `need_by = await _min_date_by_item(...)` query) with:

```python
    policy = await load_demand_policy(db, org_id)
    forecast_qty, forecast_dates = await load_forecast_demand(db, org_id, horizon_start, horizon_end)
    committed_qty, committed_dates = await load_committed_demand(
        db, org_id, horizon_start, horizon_end
    )
    independent = combine_demand(forecast_qty, committed_qty, policy)
    # Gate on the COMBINED map, not the forecast alone: a shop with real orders
    # and no typed estimates has genuine demand and must get a real plan.
    if not independent:
        return []
    need_by = earliest_dates(forecast_dates, committed_dates)
    return await _explode_and_net(db, org_id, independent, need_by)
```

Add the import at the top of the module:

```python
from app.services.demand import (
    combine_demand,
    earliest_dates,
    load_committed_demand,
    load_demand_policy,
    load_forecast_demand,
)
```

Update `compute_mrp_plan`'s docstring: `independent_demand` is now the combined figure from both sources under the org's policy, and say which policy is the default. Leave `_explode_and_net`, `compute_build_plan` and every other function untouched. Delete `_min_date_by_item` only if nothing else uses it; check first.

- [ ] **Step 6: Write the engine integration tests**

Append to `backend/tests/test_sales_orders.py`. Use a needed-by date inside the default 90-day horizon so the grid tests in Task 6 can reuse the same helper; compute it rather than hardcoding:

```python
from datetime import datetime, timedelta, timezone


def _soon() -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()


async def _plan_lines(client, token):
    res = await client.get("/mrp/plan", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return {line["item_sku"]: line for line in res.json()["lines"]}


async def test_an_open_order_alone_produces_a_plan(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=25, needed_by=_soon())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["WIDGET-1"]["net_requirement"]) == Decimal("25"), (
        "an order with no forecast must still plan"
    )


async def test_a_cancelled_order_is_not_demand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    order = await _create_order(client, token, customer, item, qty=25, needed_by=_soon())
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    assert await _plan_lines(client, token) == {}


async def test_a_completed_order_is_not_demand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 100)
    order = await _create_order(client, token, customer, item, qty=25, needed_by=_soon())
    await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)

    assert await _plan_lines(client, token) == {}


async def test_consume_policy_does_not_double_count(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await client.put(
        f"/planning/grid/{item['id']}",
        json={"quantity": "100", "needed_by": _soon()},
        headers=headers,
    )
    await _create_order(client, token, customer, item, qty=60, needed_by=_soon())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["WIDGET-1"]["net_requirement"]) == Decimal("100"), (
        "60 ordered against a 100 estimate must plan 100, not 160"
    )


async def test_add_policy_sums_both_sources(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.patch("/orgs/me", json={"demand_policy": "add"}, headers=headers)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await client.put(
        f"/planning/grid/{item['id']}",
        json={"quantity": "100", "needed_by": _soon()},
        headers=headers,
    )
    await _create_order(client, token, customer, item, qty=60, needed_by=_soon())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["WIDGET-1"]["net_requirement"]) == Decimal("160")


async def test_orders_explode_through_a_bom(client):
    """An order for a built product must generate component demand, proving
    committed demand flows through the same explosion forecasts do."""
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _create_order(client, token, customer, pump, qty=10, needed_by=_soon())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("20")
```

Add this helper to the test module (it is the same one `backend/tests/test_mrp_explosion.py` and `test_planning_grid.py` define; copied rather than imported, matching how every test module in this repo shares helpers):

```python
async def _create_active_bom(client, token, produced_item, lines):
    """lines: list of (component_item, qty_per). Creates a draft BOM, sets its
    lines, and activates it."""
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
```

The component quantity of 2 per unit is what makes the expected 20 hold for an order of 10.

- [ ] **Step 7: Run the tests**

```bash
cd backend && python -m pytest tests/test_sales_orders.py tests/test_demand_combination.py -v
```

Then the regression check that matters most, since this task changed the engine every planning surface depends on:

```bash
cd backend && python -m pytest tests/test_mrp_explosion.py tests/test_planning_grid.py -v
```

Expected: unchanged pass counts. If any fail, the seeding change altered existing behaviour and must be fixed before committing.

- [ ] **Step 8: Run the full suite and commit**

```bash
cd backend && python -m pytest -q
git add backend/app/services/demand.py backend/app/services/mrp.py backend/tests/test_demand_combination.py backend/tests/test_sales_orders.py
git commit -m "feat: open sales orders are demand in the planning engine"
```

---

### Task 6: The grid shows what is committed

**Files:**
- Modify: `backend/app/services/planning_grid.py`
- Modify: `backend/app/api/routes/planning_grid.py`
- Test: `backend/tests/test_planning_grid.py` (extend)

**Interfaces:**
- Consumes: `load_committed_demand` from Task 5.
- Produces: `GridProduct.committed_qty: Decimal` and the same field on `GridProductResponse`.

**Critical context.** `compute_planning_grid` reads `DemandForecast` in its own grouped query, separate from the engine call, and that query decides both `planned_qty` and *which items get a row at all* (`product_ids` includes `set(planned)`). Feeding only the engine leaves a product whose demand is entirely committed with a row whose materials go red while Planned reads zero, or with no row at all. So committed demand must be plumbed here too.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_planning_grid.py`, reusing that file's existing helpers and adding customer and order helpers copied from `backend/tests/test_sales_orders.py`:

```python
async def test_committed_quantity_appears_on_the_row(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=40, needed_by=_soon())

    grid = await _grid(client, token)
    row = _product(grid, "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal("40")


async def test_a_product_with_only_committed_demand_still_gets_a_row(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=15, needed_by=_soon())

    grid = await _grid(client, token)
    row = _product(grid, "WIDGET-1")
    assert Decimal(row["planned_qty"]) == Decimal(0), "nothing was typed"
    assert Decimal(row["committed_qty"]) == Decimal("15")
    assert row["materials_status"] == "missing", "an order with no stock must show as short"


async def test_committed_quantity_is_zero_without_orders(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, item, 10)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal(0)


async def test_a_cancelled_order_leaves_nothing_committed(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, item, 10)
    order = await _create_order(client, token, customer, item, qty=40, needed_by=_soon())
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal(0)
    assert Decimal(row["planned_qty"]) == Decimal("10")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v -k "committed"
```

Expected: `KeyError: 'committed_qty'`, and the only-committed-demand test failing because the product has no row.

- [ ] **Step 3: Plumb committed demand into the grid service**

In `backend/app/services/planning_grid.py`:

- Add `committed_qty: Decimal` to `GridProduct`, with a comment saying it is what customers have actually ordered, as opposed to `planned_qty` which is the estimate someone typed.
- Load it once near the forecast query: `committed_qty_by_item, _ = await load_committed_demand(db, org_id, horizon_start, horizon_end)`.
- Include committed items in the row rule so a purely committed product gets a row. The current expression is `((produced | consumed) - components) | set(planned)`; committed items must be unioned in the same way `planned` is, and must not be filtered out by the `- components` subtraction, since a component sold directly is a legitimate product.
- Populate `committed_qty=committed_qty_by_item.get(product_id, Decimal(0))` when constructing each `GridProduct`.
- Check the consumed-only suppression filter added earlier in this file: a row that exists only because of committed demand must NOT be suppressed. Read that filter and make sure its condition still only targets rows whose sole claim is consumption history.

- [ ] **Step 4: Expose it**

In `backend/app/api/routes/planning_grid.py`, add `committed_qty: Decimal` to `GridProductResponse` and `committed_qty=product.committed_qty,` to `_product_to_response`. The `PUT` endpoint's fallback response object also constructs a `GridProductResponse`; add `committed_qty=Decimal("0.0000"),` there.

- [ ] **Step 5: Run the tests**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v
```

Expected: all pass, existing tests unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/planning_grid.py backend/app/api/routes/planning_grid.py backend/tests/test_planning_grid.py
git commit -m "feat: planning grid shows committed customer demand"
```

---

### Task 7: The Sales page

**Files:**
- Create: `frontend/src/pages/Sales.tsx`, `frontend/src/pages/sales/OrdersPanel.tsx`, `frontend/src/pages/sales/CustomersPanel.tsx`, `frontend/src/pages/sales/types.ts`
- Modify: `frontend/src/App.tsx`, `frontend/src/lib/nav.ts`
- Test: `frontend/src/pages/Sales.render.test.tsx`

**Interfaces:**
- Consumes: the Task 2 and 3 and 4 endpoints.
- Produces: route `/sales`, nav entry gated on `featureKey: "sales"`.

**Convention note to settle deliberately.** `Procurement.tsx` and its panels do NOT role-gate in the frontend; gating is backend-only there. `Planning.tsx` DOES, via `useCurrentUser()`. Follow Planning: hide write actions from a user who is neither owner nor manager. This is the newer convention and the spec's roles section is explicit, so the new page will differ from the buy-side page it otherwise mirrors. That is intended.

- [ ] **Step 1: Write the failing render test**

Create `frontend/src/pages/Sales.render.test.tsx`, following `frontend/src/pages/ProductionSetup.render.test.tsx` exactly for the render harness (`renderToString`, `MemoryRouter` with `initialEntries` carrying `?tab=`, `QueryClientProvider`, and the `.split("<!-- -->").join("")` comment-stripping trick). Seed `["org-me"]` with an owner role by default, as `Planning.render.test.tsx` does.

Cover: both tabs render without data; the orders tab lists an order with its number, customer, product, quantity, needed-by and status; the orders tab shows its empty state; the customers tab lists a customer and shows its empty state; a member role sees no create form on either tab; and a "Mark done" action appears for an open order but not for one already done.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npx vitest run src/pages/Sales.render.test.tsx
```

- [ ] **Step 3: Write the types**

Create `frontend/src/pages/sales/types.ts` with `Customer`, `SalesOrderLine`, `SalesOrder` interfaces mirroring the backend response models exactly. Quantities are Decimal strings, so reuse the display helper: import `trimQty` from `../planning/types` rather than writing a second one.

- [ ] **Step 4: Write the panels**

Create `frontend/src/pages/sales/CustomersPanel.tsx` and `frontend/src/pages/sales/OrdersPanel.tsx`, mirroring `frontend/src/pages/Suppliers.tsx` and `frontend/src/pages/PurchaseOrders.tsx` respectively. Read both first. Requirements:

- Export named components `CustomersPanel` and `OrdersPanel`, no props, as the buy-side panels do.
- Query keys: `["customers", search, activeOnly]` and `["sales-orders", search, statusFilter]`. For the order form's pickers, reuse `["customers", "active"]` and the existing `["items"]` key.
- Use `Field` + `Input`/`Select` for forms, `ItemSelect` for the product picker (props `{ id?, items, value, onChange, excludeId?, required? }`), and the `Table`/`TableWrap` set for lists. `EmptyState` with a `lucide-react` icon for empty lists. `Alert variant="destructive"` for errors.
- Order status badge via `Badge`; add a status-to-variant mapping in `frontend/src/lib/status.ts` beside the existing `poStatusVariant`, so the sell side reuses the house helper rather than inlining colours.
- Mark done goes behind a confirmation, since it moves stock and cannot be undone. Use the `Dialog` component the buy-side panels use for their submit and cancel confirmations, and say in the confirmation that the goods will come off stock.
- Surface the backend's `detail` string verbatim on failure; a refusal for insufficient stock is a 409 whose message names the shortfall and is written to be shown to the user.
- Gate the create forms and the mark-done and cancel actions on `useCurrentUser()`, showing them only for owner or manager.

- [ ] **Step 5: Write the hub**

Create `frontend/src/pages/Sales.tsx` mirroring `Procurement.tsx`: a `TABS` const array with `orders` and `customers`, `type Tab = (typeof TABS)[number]["value"]`, a `TAB_VALUES` set for validating `?tab=`, initial tab from `useSearchParams()` read once in the `useState` initializer, and `<PageHeader title="Sales" description=... actions={<PageTabs .../>} />` above a `<div key={tab} className="animate-fade-in">` wrapping the conditional panels.

- [ ] **Step 6: Register the route and nav entry**

In `frontend/src/App.tsx`, add the lazy page and the route wrapped in `RequireFeature`:

```tsx
const Sales = lazyPage(() => import("./pages/Sales"), "Sales");
```
```tsx
<Route path="/sales" element={<RequireFeature feature="sales"><Sales /></RequireFeature>} />
```

In `frontend/src/lib/nav.ts`, add to the Operations section, keeping the existing entries untouched:

```ts
      { to: "/sales", label: "Sales", icon: Receipt, keywords: ["orders", "customers", "sales orders", "so"], featureKey: "sales" },
```

Import `Receipt` from `lucide-react` in the existing alphabetical import block. Note `nav.ts` may carry unrelated uncommitted changes; stage only your hunks.

`frontend/nginx.conf.template` and `frontend/vite.config.ts`: `/sales` is BOTH an SPA route and a backend prefix (`/sales-orders` is a different prefix, but check whether the nginx location regex would catch `/sales`). Read the comments in both files and follow their stated rule: a path that the backend can answer must NOT be listed in nginx's frontend-only location block, and any backend prefix must be present in the vite proxy alternation. Add `sales-orders` and `customers` to the vite proxy alternation. State in your report what you concluded about `/sales` itself and why.

- [ ] **Step 7: Run the tests and type-check**

```bash
cd frontend && npx vitest run
cd frontend && npx tsc -b
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/Sales.tsx frontend/src/pages/sales/ frontend/src/pages/Sales.render.test.tsx frontend/src/App.tsx frontend/src/lib/nav.ts frontend/src/lib/status.ts frontend/vite.config.ts frontend/nginx.conf.template
git commit -m "feat: Sales page with orders and customers"
```

---

### Task 8: Committed demand on the grid, and the policy setting

**Files:**
- Modify: `frontend/src/pages/planning/types.ts`, `frontend/src/pages/planning/PlannedQtyInput.tsx`
- Modify: `frontend/src/pages/Planning.render.test.tsx`
- Modify: the org settings surface (find it: `frontend/src/pages/Approvals.tsx` reads and patches `adjust_approval_threshold` today)

**Interfaces:**
- Consumes: `committed_qty` from Task 6, `demand_policy` from Task 1.

- [ ] **Step 1: Write the failing tests**

Extend `frontend/src/pages/Planning.render.test.tsx`. Add `committed_qty` to the `GRID` fixture's products, then cover: a product with committed demand renders a line reading `40 ordered`; a product with `"0"` committed renders no such line; and a member-role user, whose cell renders read-only, still sees the ordered line. That last one matters: the read-only branch currently returns a bare span and nothing else, so it is the easiest case to miss.

Add a test for the settings surface asserting both policy choices are offered and the current one is reflected.

- [ ] **Step 2: Run to verify failure**

```bash
cd frontend && npx vitest run
```

- [ ] **Step 3: Add the field to the type**

In `frontend/src/pages/planning/types.ts`, add to `GridProduct`:

```ts
  /** What customers have actually ordered, as opposed to planned_qty which is
   * the estimate someone typed. */
  committed_qty: string;
```

- [ ] **Step 4: Render it in both branches**

In `frontend/src/pages/planning/PlannedQtyInput.tsx`, show the committed line whenever `Number(product.committed_qty) > 0`, in BOTH the `readOnly` early-return branch and the editable branch. The read-only branch currently returns only `<span>{qty}</span>`; wrap it so the ordered line sits beneath, matching the editable branch's `space-y-1` stacking. Keep it quiet and secondary (small, muted), and word it plainly: the number followed by `ordered`. Do not touch the quantity input, the date input, the scheduler, the mutation, or any existing handler.

- [ ] **Step 5: Add the setting to the UI**

Find where `adjust_approval_threshold` is edited today and add the demand policy beside it, as a two-option choice with the question worded plainly: when real orders come in, do they count towards your plan, or on top of it. Default shown is whatever the API returns. Patch it through the same `PATCH /orgs/me` call that surface already uses, and invalidate `["org-me"]` on success. The endpoint is owner-only, so gate the control the way that surface already gates its existing setting.

- [ ] **Step 6: Run tests and type-check**

```bash
cd frontend && npx vitest run
cd frontend && npx tsc -b
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/planning/types.ts frontend/src/pages/planning/PlannedQtyInput.tsx frontend/src/pages/Planning.render.test.tsx
git commit -m "feat: show committed customer demand on the planning grid"
```

---

## Post-implementation

- [ ] Run both suites: `cd backend && python -m pytest -q` and `cd frontend && npx vitest run`, plus `npx tsc -b`.
- [ ] Verify in a browser: the Sales page raises an order, the grid's ordered line appears for that product, the row goes red when stock is short, and marking the order done removes the stock. This needs a login, so it is the human's step.
- [ ] Run `graphify update .` to refresh the knowledge graph.
- [ ] Use `superpowers:requesting-code-review` for a whole-branch review before merging.
