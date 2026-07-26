# Planning Grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multi-screen MRP interface with one Katana-style grid where the only user input is a planned quantity per product, colors say what is wrong, and one button orders the missing materials.

**Architecture:** A new read service (`app/services/planning_grid.py`) runs the existing MRP engine once and reshapes its output into product rows (material status, ready-by date) plus a flat to-buy list. Two new endpoints (`GET /planning/grid`, `PUT /planning/grid/{item_id}`) serve it. The frontend replaces `Planning.tsx` with a single grid page. The planning engine, forecast storage, and PO generation are reused unchanged.

**Tech Stack:** FastAPI + SQLAlchemy async + Pydantic v2 (backend), React + TypeScript + TanStack Query + Tailwind (frontend), pytest + vitest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-planning-grid-design.md`. Read it before Task 1.
- No changes to the netting/explosion math in `app/services/mrp.py`. Tasks 1 only *extracts* two helpers there; the arithmetic must stay byte-identical.
- The planning window is fixed at today + 90 days and is never exposed in the UI or as a query parameter.
- Material status values are exactly `in_stock` | `arriving` | `missing`. Urgency values are exactly `overdue` | `soon` | `later`.
- No new dependencies, backend or frontend.
- Backend tests run from `backend/`: `python -m pytest tests/<file> -v` (pytest.ini sets `asyncio_mode = auto`, so no `@pytest.mark.asyncio` decorator per test is needed beyond the module-level `pytestmark`).
- Frontend tests run from `frontend/`: `npx vitest run <file>`.
- Copy rule for all user-visible strings: plain language, no MRP jargon (no "net requirement", "horizon", "explosion", "MOQ") outside the row-expansion detail.
- No em dashes in any user-visible copy or documentation prose.

---

### Task 1: Grid service and read endpoint (product rows)

**Files:**
- Modify: `backend/app/services/mrp.py:80` (rename `_load_bom_children` to `load_bom_children`), `backend/app/services/mrp.py:220-233` (extract `load_on_hand`)
- Create: `backend/app/services/planning_grid.py`
- Create: `backend/app/api/routes/planning_grid.py`
- Modify: `backend/app/main.py:129` (register router)
- Test: `backend/tests/test_planning_grid.py`

**Interfaces:**
- Consumes: `compute_mrp_plan(db, org_id, horizon_start, horizon_end) -> list[MrpPlanLine]` from `app.services.mrp`.
- Produces: `load_bom_children(db, org_id) -> dict[UUID, list[tuple[UUID, Decimal]]]`, `load_on_hand(db, org_id) -> dict[UUID, Decimal]`, `resolve_grid_window(as_of=None) -> tuple[date, date]`, `compute_planning_grid(db, org_id, as_of=None) -> PlanningGrid`, dataclasses `GridProduct` / `GridToBuy` / `PlanningGrid`, and `GET /planning/grid`. Later tasks add fields to `GridProduct` (`ready_by`, `suggested_qty`, `detail`) and populate `PlanningGrid.to_buy`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_planning_grid.py`:

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


async def _create_item(client, token, sku, name="Item", lead_time_days=None, min_order_qty=None):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "component",
            "unit_of_measure": "ea",
            "is_lot_tracked": False,
            "lead_time_days": lead_time_days,
            "min_order_qty": str(min_order_qty) if min_order_qty is not None else None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
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


async def _plan(client, token, item, qty, needed_by=None):
    """Set the grid's planned quantity for an item."""
    body = {"quantity": str(qty)}
    if needed_by is not None:
        body["needed_by"] = needed_by
    res = await client.put(
        f"/planning/grid/{item['id']}", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _grid(client, token):
    res = await client.get("/planning/grid", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


def _product(grid, sku):
    for row in grid["products"]:
        if row["item_sku"] == sku:
            return row
    raise AssertionError(f"{sku} not in grid: {[r['item_sku'] for r in grid['products']]}")


async def test_only_top_level_products_get_rows(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    sub = await _create_item(client, token, "SUB-1")
    pump = await _create_item(client, token, "PUMP-A2", name="Coolant pump")
    await _create_active_bom(client, token, sub, [(seal, 2)])
    await _create_active_bom(client, token, pump, [(sub, 1)])

    grid = await _grid(client, token)

    skus = {row["item_sku"] for row in grid["products"]}
    assert skus == {"PUMP-A2"}, "sub-assemblies and raw components must not get their own rows"
    assert _product(grid, "PUMP-A2")["item_name"] == "Coolant pump"


async def test_forecasted_item_without_bom_gets_a_row(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10)
    grid = await _grid(client, token)

    assert _product(grid, "WIDGET-1")["planned_qty"] == "10.0000"


async def test_missing_when_a_component_is_short(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "missing"
    assert row["missing_materials"] == ["SEAL-V2"]
    assert grid["summary"]["missing"] == 1


async def test_in_stock_when_components_are_covered(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    headers = {"Authorization": f"Bearer {token}"}
    adj = await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "500", "reason_code": "found_stock"},
        headers=headers,
    )
    assert adj.status_code == 200, adj.text
    # An owner never queues for approval, so the stock is on the shelf now. If
    # this ever fails, the seeding is the problem, not the grid.
    assert adj.json()["applied"] is True

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "in_stock"
    assert row["missing_materials"] == []
    assert grid["summary"]["in_stock"] == 1


async def test_arriving_when_a_component_is_only_covered_by_an_open_po(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await client.post("/suppliers", json={"name": "Sealtech"}, headers=headers)
    po = await client.post(
        "/purchase-orders", json={"supplier_id": supplier.json()["id"]}, headers=headers
    )
    po_id = po.json()["id"]
    await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": seal["id"], "quantity_ordered": "500", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    submit = await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    assert submit.status_code == 200, submit.text

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "arriving"
    assert row["missing_materials"] == []
    assert grid["summary"]["arriving"] == 1


async def test_purchased_product_judges_itself(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10)
    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["materials_status"] == "missing"
    assert row["missing_materials"] == ["WIDGET-1"]


async def test_missing_rows_sort_above_covered_rows(client):
    token = await _register_and_login(client)
    covered = await _create_item(client, token, "AAA-COVERED")
    short = await _create_item(client, token, "ZZZ-SHORT")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        f"/inventory/{covered['id']}/adjust",
        json={"quantity": "100", "reason_code": "found_stock"},
        headers=headers,
    )

    await _plan(client, token, covered, 5)
    await _plan(client, token, short, 5)
    grid = await _grid(client, token)

    assert [row["item_sku"] for row in grid["products"]] == ["ZZZ-SHORT", "AAA-COVERED"]


async def test_grid_is_org_scoped(client):
    token_a = await _register_and_login(client)
    widget = await _create_item(client, token_a, "WIDGET-1")
    await _plan(client, token_a, widget, 10)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    grid_b = await _grid(client, token_b)

    assert grid_b["products"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v
```

Expected: every test FAILS on the `PUT /planning/grid/...` or `GET /planning/grid` call returning 404 (routes do not exist yet).

- [ ] **Step 3: Extract the two reusable loaders in `mrp.py`**

In `backend/app/services/mrp.py`, rename `_load_bom_children` to `load_bom_children` (definition at line 80, call site at line 254, and the docstring mention at line 362), then add `load_on_hand` directly below it and use it inside `_explode_and_net`.

Replace the on-hand block currently at lines 220-233 of `_explode_and_net`:

```python
    on_hand = await load_on_hand(db, org_id)
```

And add this new function immediately after `load_bom_children`:

```python
async def load_on_hand(db: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """Total on-hand per item: active lot quantities for lot-tracked items plus
    the general inventory_balances for everything else. The two are disjoint (an
    item is one or the other - see Item.is_lot_tracked), so summing them is just
    "whichever applies". on_hold lots are excluded: they can't be consumed, so
    counting them would understate a real shortage.
    """
    on_hand = await _sum_by_item(
        db,
        select(Lot.item_id, func.sum(Lot.quantity_remaining))
        .where(Lot.org_id == org_id, Lot.status == "active")
        .group_by(Lot.item_id),
    )
    inventory_on_hand = await _sum_by_item(
        db,
        select(InventoryBalance.item_id, func.sum(InventoryBalance.quantity_on_hand))
        .where(InventoryBalance.org_id == org_id)
        .group_by(InventoryBalance.item_id),
    )
    for item_id, qty in inventory_on_hand.items():
        on_hand[item_id] = on_hand.get(item_id, Decimal(0)) + qty
    return on_hand
```

- [ ] **Step 4: Verify the extraction changed no behavior**

```bash
cd backend && python -m pytest tests/test_mrp_explosion.py -v
```

Expected: PASS, same count as before the edit. If anything fails, the extraction is wrong; fix before continuing.

- [ ] **Step 5: Write the grid service**

Create `backend/app/services/planning_grid.py`:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Item
from app.models.forecast import DemandForecast
from app.services.mrp import MrpPlanLine, compute_mrp_plan, load_bom_children, load_on_hand

# The grid never shows a horizon control - a non-technical user should not have
# to pick one. 90 days matches the engine default (services.mrp.resolve_horizon),
# so the grid and a raw /mrp/plan call agree.
GRID_HORIZON_DAYS = 90

_STATUS_RANK = {"missing": 0, "arriving": 1, "in_stock": 2}


@dataclass
class GridProduct:
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    # Sum of the item's forecast quantities inside the window - the one number
    # the user types. 0 means "not planning any right now".
    planned_qty: Decimal
    # Earliest forecast period inside the window, None when planned_qty is 0.
    needed_by: date | None
    on_hand: Decimal
    # in_stock | arriving | missing, judged over the components reachable from
    # this product through active BOMs (or the product itself when it has none).
    materials_status: str
    missing_materials: list[str]


@dataclass
class PlanningGrid:
    horizon_start: date
    horizon_end: date
    products: list[GridProduct]


def resolve_grid_window(as_of: date | None = None) -> tuple[date, date]:
    start = as_of if as_of is not None else datetime.now(timezone.utc).date()
    return start, start + timedelta(days=GRID_HORIZON_DAYS)


def _reachable_components(
    root: uuid.UUID, children: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]]
) -> set[uuid.UUID]:
    """Every component below root in the active-BOM graph, at any depth."""
    seen: set[uuid.UUID] = set()
    stack = [child for child, _ in children.get(root, [])]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(child for child, _ in children.get(node, []))
    return seen


async def compute_planning_grid(
    db: AsyncSession, org_id: uuid.UUID, as_of: date | None = None
) -> PlanningGrid:
    """One row per product a user plans, with a traffic-light material status.

    A "product" is an item with an active BOM that no other active BOM consumes
    (the top of a build tree), plus any item carrying a forecast in the window -
    that second half covers a purchased item sold as-is, which has no BOM but is
    still something the user plans. Sub-assemblies never get their own row: the
    engine handles them, and showing them would be exactly the disjointed detail
    this screen exists to hide.

    Status is judged over the components reachable from the product (or the
    product itself when it has no BOM, where "the material" is the thing you buy):

        missing   - some component still has a net requirement, so it must be ordered
        arriving  - nothing is short, but some component is only covered thanks to
                    an open or drafted PO rather than stock on the shelf
        in_stock  - every component is covered by stock on hand
    """
    horizon_start, horizon_end = resolve_grid_window(as_of)

    children = await load_bom_children(db, org_id)
    plan = await compute_mrp_plan(db, org_id, horizon_start, horizon_end)
    by_item = {uuid.UUID(line.item_id): line for line in plan}
    on_hand = await load_on_hand(db, org_id)

    forecast_rows = (
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
    planned = {item_id: (qty, first) for item_id, qty, first in forecast_rows}

    produced = set(children)
    components = {child for kids in children.values() for child, _ in kids}
    product_ids = (produced - components) | set(planned)
    if not product_ids:
        return PlanningGrid(horizon_start=horizon_start, horizon_end=horizon_end, products=[])

    wanted = product_ids | set(by_item)
    items = {
        item.id: item
        for item in (
            await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(wanted)))
        )
        .scalars()
        .all()
    }

    products: list[GridProduct] = []
    for product_id in product_ids:
        item = items.get(product_id)
        if item is None:
            continue
        qty, first_period = planned.get(product_id, (Decimal(0), None))
        evaluation = _reachable_components(product_id, children) or {product_id}

        missing_materials: list[str] = []
        arriving = False
        for component_id in evaluation:
            line = by_item.get(component_id)
            if line is None:
                continue
            if line.net_requirement > 0:
                missing_materials.append(line.item_sku)
            elif line.on_hand < line.gross_demand:
                arriving = True
        missing_materials.sort()

        if missing_materials:
            status = "missing"
        elif arriving:
            status = "arriving"
        else:
            status = "in_stock"

        products.append(
            GridProduct(
                item_id=product_id,
                item_sku=item.sku,
                item_name=item.name,
                planned_qty=qty,
                needed_by=first_period,
                on_hand=on_hand.get(product_id, Decimal(0)),
                materials_status=status,
                missing_materials=missing_materials,
            )
        )

    products.sort(key=lambda p: (_STATUS_RANK[p.materials_status], p.item_sku))
    return PlanningGrid(horizon_start=horizon_start, horizon_end=horizon_end, products=products)
```

- [ ] **Step 6: Write the route module**

Create `backend/app/api/routes/planning_grid.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services.planning_grid import GridProduct, compute_planning_grid

router = APIRouter(prefix="/planning", tags=["planning"])


class GridProductResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    planned_qty: Decimal
    needed_by: date | None
    on_hand: Decimal
    materials_status: str
    missing_materials: list[str]


class GridSummaryResponse(BaseModel):
    missing: int
    arriving: int
    in_stock: int


class PlanningGridResponse(BaseModel):
    horizon_start: date
    horizon_end: date
    products: list[GridProductResponse]
    summary: GridSummaryResponse


def _product_to_response(product: GridProduct) -> GridProductResponse:
    return GridProductResponse(
        item_id=str(product.item_id),
        item_sku=product.item_sku,
        item_name=product.item_name,
        planned_qty=product.planned_qty,
        needed_by=product.needed_by,
        on_hand=product.on_hand,
        materials_status=product.materials_status,
        missing_materials=product.missing_materials,
    )


@router.get("/grid", response_model=PlanningGridResponse)
async def get_planning_grid(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The whole planning screen in one read: a row per product with a
    traffic-light material status. See app.services.planning_grid."""
    grid = await compute_planning_grid(db, current.org_id)
    return PlanningGridResponse(
        horizon_start=grid.horizon_start,
        horizon_end=grid.horizon_end,
        products=[_product_to_response(p) for p in grid.products],
        summary=GridSummaryResponse(
            missing=sum(1 for p in grid.products if p.materials_status == "missing"),
            arriving=sum(1 for p in grid.products if p.materials_status == "arriving"),
            in_stock=sum(1 for p in grid.products if p.materials_status == "in_stock"),
        ),
    )
```

- [ ] **Step 7: Register the router**

In `backend/app/main.py`, add the import alongside the other route imports and register it directly after the two forecast routers (line 130), so it sits behind the same feature flag:

```python
app.include_router(planning_grid.router, dependencies=[Depends(require_feature("planning"))])
```

Add `planning_grid` to the `from app.api.routes import (...)` list in the same file (keep the list alphabetical if it already is).

- [ ] **Step 8: Add the PUT stub so the test helper works**

The tests seed demand through `PUT /planning/grid/{item_id}`, which Task 4 implements properly. Add the minimal version now, in `backend/app/api/routes/planning_grid.py`:

```python
class GridPlanRequest(BaseModel):
    quantity: Decimal
    needed_by: date | None = None


@router.put("/grid/{item_id}", response_model=GridProductResponse)
async def set_planned_quantity(
    item_id: uuid.UUID,
    body: GridPlanRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set how many of this product the user plans to make. Implemented in
    Task 4; this stub writes the forecast row the grid reads back."""
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    horizon_start, horizon_end = resolve_grid_window()
    needed_by = body.needed_by or horizon_start + timedelta(days=DEFAULT_NEEDED_BY_DAYS)
    await db.execute(
        delete(DemandForecast).where(
            DemandForecast.org_id == current.org_id,
            DemandForecast.item_id == item_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
    )
    if body.quantity > 0:
        db.add(
            DemandForecast(
                org_id=current.org_id,
                item_id=item_id,
                period_start=needed_by,
                quantity=body.quantity,
                source="planning_grid",
                created_by=current.id,
            )
        )
    await db.commit()
    grid = await compute_planning_grid(db, current.org_id)
    for product in grid.products:
        if product.item_id == item_id:
            return _product_to_response(product)
    return GridProductResponse(
        item_id=str(item_id),
        item_sku=item.sku,
        item_name=item.name,
        planned_qty=Decimal(0),
        needed_by=None,
        on_hand=Decimal(0),
        materials_status="in_stock",
        missing_materials=[],
    )
```

Add these imports at the top of the same file, extending (not duplicating) what Step 6 already wrote:

```python
import uuid
from datetime import timedelta

from sqlalchemy import delete

from app.api.deps import get_or_404
from app.models.bom import Item
from app.models.forecast import DemandForecast
```

and widen the existing `from app.services.planning_grid import ...` line to:

```python
from app.services.planning_grid import (
    DEFAULT_NEEDED_BY_DAYS,
    GridProduct,
    compute_planning_grid,
    resolve_grid_window,
)
```

And add the constant to `backend/app/services/planning_grid.py`, below `GRID_HORIZON_DAYS`:

```python
# Where a typed quantity lands when the user does not pick a date. Two weeks is
# far enough out that most purchased parts can still arrive in time.
DEFAULT_NEEDED_BY_DAYS = 14
```

- [ ] **Step 9: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 10: Run the full backend suite for regressions**

```bash
cd backend && python -m pytest -q
```

Expected: no new failures versus the pre-task baseline.

- [ ] **Step 11: Commit**

```bash
git add backend/app/services/mrp.py backend/app/services/planning_grid.py backend/app/api/routes/planning_grid.py backend/app/main.py backend/tests/test_planning_grid.py
git commit -m "feat: planning grid read endpoint with per-product material status"
```

---

### Task 2: Ready-by date and the to-buy list

**Files:**
- Modify: `backend/app/services/planning_grid.py`
- Modify: `backend/app/api/routes/planning_grid.py`
- Test: `backend/tests/test_planning_grid.py`

**Interfaces:**
- Consumes: `GridProduct`, `PlanningGrid`, `compute_planning_grid` from Task 1.
- Produces: `GridProduct.ready_by: date | None`, `GridToBuy` dataclass, `PlanningGrid.to_buy: list[GridToBuy]`, and `to_buy` in the endpoint response.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_planning_grid.py`:

```python
from datetime import date, timedelta


def _today() -> date:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date()


async def test_ready_by_is_todays_date_plus_the_longest_lead_time(client):
    token = await _register_and_login(client)
    fast = await _create_item(client, token, "FAST-1", lead_time_days=3)
    slow = await _create_item(client, token, "SLOW-1", lead_time_days=21)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(fast, 1), (slow, 1)])

    await _plan(client, token, pump, 10)
    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["ready_by"] == str(_today() + timedelta(days=21))


async def test_ready_by_is_today_when_everything_is_in_stock(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2", lead_time_days=30)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "500", "reason_code": "found_stock"},
        headers={"Authorization": f"Bearer {token}"},
    )

    await _plan(client, token, pump, 10)
    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["ready_by"] == str(_today())


async def test_ready_by_is_null_when_nothing_is_planned(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["ready_by"] is None


async def test_to_buy_lists_shortfalls_with_supplier_and_urgency(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await client.post("/suppliers", json={"name": "Sealtech"}, headers=headers)
    seal = await _create_item(client, token, "SEAL-V2", lead_time_days=5, min_order_qty=1000)
    patch = await client.patch(
        f"/items/{seal['id']}",
        json={"preferred_supplier_id": supplier.json()["id"]},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50, needed_by=str(_today() + timedelta(days=8)))
    grid = await _grid(client, token)

    assert len(grid["to_buy"]) == 1
    row = grid["to_buy"][0]
    assert row["item_sku"] == "SEAL-V2"
    assert row["supplier_name"] == "Sealtech"
    assert row["quantity"] == "1000.0000", "min order quantity must round the order up"
    assert row["order_by"] == str(_today() + timedelta(days=3))
    assert row["urgency"] == "soon"


async def test_to_buy_marks_a_past_order_date_overdue(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2", lead_time_days=30)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    await _plan(client, token, pump, 10, needed_by=str(_today() + timedelta(days=5)))
    grid = await _grid(client, token)

    assert grid["to_buy"][0]["urgency"] == "overdue"
    assert grid["to_buy"][0]["supplier_name"] is None


async def test_to_buy_excludes_manufactured_items(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    await _plan(client, token, pump, 10)
    grid = await _grid(client, token)

    assert [row["item_sku"] for row in grid["to_buy"]] == ["SEAL-V2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v -k "ready_by or to_buy"
```

Expected: FAIL with `KeyError: 'ready_by'` / `KeyError: 'to_buy'`.

- [ ] **Step 3: Add ready-by and to-buy to the service**

In `backend/app/services/planning_grid.py`, add `ready_by` to `GridProduct` (after `missing_materials`):

```python
    # Earliest date the planned quantity can actually be finished: today when
    # every component is on the shelf, otherwise today plus the longest lead
    # time among the components that must still arrive. None when nothing is
    # planned. POs do not carry a promised delivery date yet, so an already
    # -ordered component is assumed to take its full lead time again - the
    # honest pessimistic read until PO dates exist.
    ready_by: date | None
```

Add the to-buy dataclass and urgency ranking below `_STATUS_RANK`:

```python
_URGENCY_RANK = {"overdue": 0, "soon": 1, "later": 2}

# An order date inside this many days is "soon" - near enough that the user
# should act on this visit rather than the next one.
SOON_DAYS = 7


@dataclass
class GridToBuy:
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    # Already rounded up to the item's minimum order quantity by the engine.
    quantity: Decimal
    supplier_name: str | None
    order_by: date | None
    urgency: str  # overdue | soon | later
```

Add `to_buy` to `PlanningGrid`:

```python
@dataclass
class PlanningGrid:
    horizon_start: date
    horizon_end: date
    products: list[GridProduct]
    to_buy: list[GridToBuy]
```

In `compute_planning_grid`, import `Supplier` at the top of the module:

```python
from app.models.procurement import Supplier
```

Change the early return to carry the new field:

```python
    if not product_ids:
        return PlanningGrid(
            horizon_start=horizon_start, horizon_end=horizon_end, products=[], to_buy=[]
        )
```

Inside the per-product loop, replace the status block with one that also tracks the ready date:

```python
        missing_materials: list[str] = []
        arriving = False
        ready_by = horizon_start
        for component_id in evaluation:
            line = by_item.get(component_id)
            # A sub-assembly's own shortfall is a build instruction, not a
            # material anyone has to order, and the parts it is built from are
            # already in this same set. Only purchased lines set the colour and
            # the date, so a product whose every buyable part is on the shelf
            # reads green even when a sub-assembly still has to be built.
            if line is None or line.is_manufactured:
                continue
            component = items.get(component_id)
            lead_days = (component.lead_time_days or 0) if component is not None else 0
            if line.net_requirement > 0:
                missing_materials.append(line.item_sku)
                ready_by = max(ready_by, horizon_start + timedelta(days=lead_days))
            elif line.on_hand < line.gross_demand:
                arriving = True
                ready_by = max(ready_by, horizon_start + timedelta(days=lead_days))
        missing_materials.sort()
```

Pass it into the `GridProduct(...)` construction (after `missing_materials=missing_materials,`):

```python
                ready_by=ready_by if qty > 0 else None,
```

Then build the to-buy list after the product loop and before the sort/return:

```python
    supplier_names = {
        supplier.id: supplier.name
        for supplier in (
            await db.execute(select(Supplier).where(Supplier.org_id == org_id))
        )
        .scalars()
        .all()
    }
    to_buy: list[GridToBuy] = []
    for line in plan:
        if line.is_manufactured or line.suggested_order_quantity <= 0:
            continue
        line_item = items.get(uuid.UUID(line.item_id))
        supplier_name = None
        if line_item is not None and line_item.preferred_supplier_id is not None:
            supplier_name = supplier_names.get(line_item.preferred_supplier_id)
        order_by = line.suggested_order_date
        if order_by is None:
            urgency = "later"
        elif order_by < horizon_start:
            urgency = "overdue"
        elif order_by <= horizon_start + timedelta(days=SOON_DAYS):
            urgency = "soon"
        else:
            urgency = "later"
        to_buy.append(
            GridToBuy(
                item_id=uuid.UUID(line.item_id),
                item_sku=line.item_sku,
                item_name=line.item_name,
                quantity=line.suggested_order_quantity,
                supplier_name=supplier_name,
                order_by=order_by,
                urgency=urgency,
            )
        )
    to_buy.sort(key=lambda b: (_URGENCY_RANK[b.urgency], b.order_by or date.max, b.item_sku))
```

Update the product sort to break ties on the ready date, and the return:

```python
    products.sort(
        key=lambda p: (_STATUS_RANK[p.materials_status], p.ready_by or date.max, p.item_sku)
    )
    return PlanningGrid(
        horizon_start=horizon_start, horizon_end=horizon_end, products=products, to_buy=to_buy
    )
```

- [ ] **Step 4: Expose both in the response**

In `backend/app/api/routes/planning_grid.py`, add `ready_by: date | None` to `GridProductResponse` (after `missing_materials`) and `ready_by=product.ready_by,` to `_product_to_response`. Add the to-buy model and wire it into the response:

```python
class GridToBuyResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    quantity: Decimal
    supplier_name: str | None
    order_by: date | None
    urgency: str


class PlanningGridResponse(BaseModel):
    horizon_start: date
    horizon_end: date
    products: list[GridProductResponse]
    to_buy: list[GridToBuyResponse]
    summary: GridSummaryResponse
```

In `get_planning_grid`, add to the returned object:

```python
        to_buy=[
            GridToBuyResponse(
                item_id=str(row.item_id),
                item_sku=row.item_sku,
                item_name=row.item_name,
                quantity=row.quantity,
                supplier_name=row.supplier_name,
                order_by=row.order_by,
                urgency=row.urgency,
            )
            for row in grid.to_buy
        ],
```

In the PUT stub's fallback `GridProductResponse(...)`, add `ready_by=None,`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v
```

Expected: all tests PASS (8 from Task 1 plus 6 new).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/planning_grid.py backend/app/api/routes/planning_grid.py backend/tests/test_planning_grid.py
git commit -m "feat: ready-by dates and to-buy list on the planning grid"
```

---

### Task 3: Row detail and history suggestions

**Files:**
- Modify: `backend/app/services/planning_grid.py`
- Modify: `backend/app/api/routes/planning_grid.py`
- Test: `backend/tests/test_planning_grid.py`

**Interfaces:**
- Consumes: `GridProduct`, `compute_planning_grid` from Tasks 1-2; `compute_weekly_suggestions(db, org_id, *, horizon_days, as_of, item_ids) -> list[ItemWeeklySuggestion]` from `app.services.demand_forecasting`, whose `points` are `ForecastPoint(period_start: date, yhat: float, ...)`.
- Produces: `GridProduct.detail: list[MrpPlanLine]`, `GridProduct.suggested_qty: Decimal | None`, and both in the endpoint response.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_planning_grid.py`:

```python
async def test_detail_carries_the_product_and_its_components(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    detail = _product(grid, "PUMP-A2")["detail"]
    assert [line["item_sku"] for line in detail] == ["PUMP-A2", "SEAL-V2"]
    assert detail[0]["is_manufactured"] is True
    # Compare the number, not its string form: BOM explosion multiplies through
    # quantity * (1 + scrap_pct/100), so the engine's Decimal carries more
    # places than a DB-round-tripped value would.
    assert Decimal(detail[1]["gross_demand"]) == Decimal("100")
    assert Decimal(detail[1]["net_requirement"]) == Decimal("100")


async def test_detail_is_empty_when_nothing_is_planned(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["detail"] == []


async def test_suggested_qty_is_absent_without_consumption_history(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, widget, 5)

    grid = await _grid(client, token)

    assert _product(grid, "WIDGET-1")["suggested_qty"] is None


async def test_suggested_qty_is_offered_only_while_nothing_is_planned(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    widget = await _create_item(client, token, "WIDGET-1")
    await client.post(
        f"/inventory/{widget['id']}/adjust",
        json={"quantity": "1000", "reason_code": "found_stock"},
        headers=headers,
    )
    for _ in range(10):
        consume = await client.post(
            f"/inventory/{widget['id']}/consume", json={"quantity": "7"}, headers=headers
        )
        assert consume.status_code == 200, consume.text

    grid = await _grid(client, token)
    suggested = _product(grid, "WIDGET-1")["suggested_qty"]
    assert suggested is not None and Decimal(suggested) > 0

    await _plan(client, token, widget, 5)
    grid_after = await _grid(client, token)
    assert _product(grid_after, "WIDGET-1")["suggested_qty"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v -k "detail or suggested"
```

Expected: FAIL with `KeyError: 'detail'` / `KeyError: 'suggested_qty'`.

- [ ] **Step 3: Add both fields to the service**

In `backend/app/services/planning_grid.py`, extend the imports:

```python
from app.services.demand_forecasting import compute_weekly_suggestions
from app.services.demand_history import daily_consumption_by_item
```

First widen the product-row rule so a suggestion has a row to land on. An item
the user consumes but has never planned (a purchased good sold as-is) is
currently invisible until someone types a quantity, which is exactly the
typing the suggestion exists to avoid. Items that are components of an active
BOM stay excluded, so raw materials never surface as products:

```python
    consumed = set(
        await daily_consumption_by_item(
            db, org_id, datetime.now(timezone.utc) - timedelta(days=CONSUMPTION_LOOKBACK_DAYS)
        )
    )
    product_ids = ((produced | consumed) - components) | set(planned)
```

with this constant beside `GRID_HORIZON_DAYS`:

```python
# How far back a consumed item counts as something the user still plans. Longer
# than the suggestion engine's own lookback would surface long-dead SKUs.
CONSUMPTION_LOOKBACK_DAYS = 90
```

`daily_consumption_by_item(db, org_id, since)` lives in
`app.services.demand_history` and returns `dict[item_id, dict[date, Decimal]]`,
merged across both the lot-tracked and non-lot-tracked ledgers. It is the same
helper `compute_weekly_suggestions` builds on, so an item with a suggestion
always has a row.

Add the two fields to `GridProduct`, after `ready_by`:

```python
    # What history says this product's demand will be over the window, offered
    # only when the user has not planned anything yet - a hint to accept, never
    # a silent write. None when there is no consumption history to learn from.
    suggested_qty: Decimal | None
    # The engine's own numbers for this product and its components, shown when
    # the user expands the row. The only place MRP vocabulary stays visible.
    detail: list[MrpPlanLine]
```

In `compute_planning_grid`, build the detail list inside the product loop, just before constructing `GridProduct`:

```python
        detail = sorted(
            (by_item[i] for i in {product_id, *evaluation} if i in by_item),
            key=lambda ln: (ln.item_id != str(product_id), ln.item_sku),
        )
```

and pass `detail=detail,` plus `suggested_qty=None,` into the `GridProduct(...)` call (suggestions are filled in below, once, for every unplanned product at the same time).

After the product loop, before building `to_buy`, add:

```python
    unplanned = {p.item_id for p in products if p.planned_qty <= 0}
    if unplanned:
        suggestions = await compute_weekly_suggestions(
            db, org_id, horizon_days=GRID_HORIZON_DAYS, as_of=horizon_start, item_ids=unplanned
        )
        suggested_by_item = {
            suggestion.item_id: Decimal(str(round(sum(p.yhat for p in suggestion.points), 4)))
            for suggestion in suggestions
        }
        for product in products:
            suggested = suggested_by_item.get(product.item_id)
            if suggested is not None and suggested > 0:
                product.suggested_qty = suggested
```

- [ ] **Step 4: Expose both in the response**

In `backend/app/api/routes/planning_grid.py`, add the detail model above `GridProductResponse`:

```python
class GridDetailLineResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    is_manufactured: bool
    gross_demand: Decimal
    on_hand: Decimal
    on_order: Decimal
    on_draft: Decimal
    net_requirement: Decimal
    suggested_order_quantity: Decimal
    need_by_date: date | None
    suggested_order_date: date | None
```

Add to `GridProductResponse`:

```python
    suggested_qty: Decimal | None
    detail: list[GridDetailLineResponse]
```

And in `_product_to_response`:

```python
        suggested_qty=product.suggested_qty,
        detail=[
            GridDetailLineResponse(
                item_id=line.item_id,
                item_sku=line.item_sku,
                item_name=line.item_name,
                is_manufactured=line.is_manufactured,
                gross_demand=line.gross_demand,
                on_hand=line.on_hand,
                on_order=line.on_order,
                on_draft=line.on_draft,
                net_requirement=line.net_requirement,
                suggested_order_quantity=line.suggested_order_quantity,
                need_by_date=line.need_by_date,
                suggested_order_date=line.suggested_order_date,
            )
            for line in product.detail
        ],
```

In the PUT stub's fallback `GridProductResponse(...)`, add `suggested_qty=None,` and `detail=[],`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v
```

Expected: all tests PASS (18 total).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/planning_grid.py backend/app/api/routes/planning_grid.py backend/tests/test_planning_grid.py
git commit -m "feat: row detail and history suggestions on the planning grid"
```

---

### Task 4: Harden the planned-quantity write

**Files:**
- Modify: `backend/app/api/routes/planning_grid.py`
- Test: `backend/tests/test_planning_grid.py`

**Interfaces:**
- Consumes: the Task 1 stub of `PUT /planning/grid/{item_id}`.
- Produces: the final endpoint contract: 400 on a negative quantity, 400 on an out-of-window date, 404 on an unknown item, audit logging, and replace-in-window semantics.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_planning_grid.py`:

```python
async def test_setting_a_quantity_replaces_the_previous_one(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10, needed_by=str(_today() + timedelta(days=5)))
    await _plan(client, token, widget, 25, needed_by=str(_today() + timedelta(days=30)))
    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["planned_qty"] == "25.0000", "the window must hold one number, not a running total"
    assert row["needed_by"] == str(_today() + timedelta(days=30))


async def test_zero_clears_the_plan(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, widget, 10)

    await _plan(client, token, widget, 0)
    grid = await _grid(client, token)

    assert _product(grid, "WIDGET-1")["planned_qty"] == "0.0000"
    assert _product(grid, "WIDGET-1")["needed_by"] is None


async def test_quantity_defaults_to_two_weeks_out(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10)
    grid = await _grid(client, token)

    assert _product(grid, "WIDGET-1")["needed_by"] == str(_today() + timedelta(days=14))


async def test_negative_quantity_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "-5"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_date_outside_the_window_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5", "needed_by": str(_today() + timedelta(days=400))},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_unknown_item_is_rejected(client):
    token = await _register_and_login(client)

    res = await client.put(
        "/planning/grid/00000000-0000-0000-0000-000000000000",
        json={"quantity": "5"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 404


async def test_another_orgs_item_cannot_be_planned(client):
    token_a = await _register_and_login(client)
    widget = await _create_item(client, token_a, "WIDGET-1")
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert res.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v -k "replaces or clears or two_weeks or rejected or another_orgs"
```

Expected: the validation tests FAIL (the stub returns 200 or 500 instead of 400); the replace/clear/default tests may already pass from the stub.

- [ ] **Step 3: Replace the stub with the final implementation**

In `backend/app/api/routes/planning_grid.py`, replace the whole `set_planned_quantity` function:

```python
@router.put("/grid/{item_id}", response_model=GridProductResponse)
async def set_planned_quantity(
    item_id: uuid.UUID,
    body: GridPlanRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    """Set how many of this product the user plans to make, and when they need
    it. The only write the planning screen makes.

    Owner or manager, matching the other planning-level writes: this replaces a
    whole window of forecasts, so a read-only member typing 0 must not be able
    to wipe an imported forecast. Reading the grid stays open to everyone.

    The window holds exactly one number per item: this replaces whatever was
    there rather than adding to it, because the input is a single box the user
    overwrites, not a ledger of buckets. The delete and the insert share one
    transaction so a failed write can never leave the item with no plan at all.
    Quantity 0 means "not making these right now" and clears the item.
    """
    if body.quantity < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Quantity cannot be negative")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    horizon_start, horizon_end = resolve_grid_window()
    needed_by = body.needed_by or horizon_start + timedelta(days=DEFAULT_NEEDED_BY_DAYS)
    if not horizon_start <= needed_by <= horizon_end:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Pick a date between {horizon_start} and {horizon_end}",
        )

    await db.execute(
        delete(DemandForecast).where(
            DemandForecast.org_id == current.org_id,
            DemandForecast.item_id == item_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
    )
    if body.quantity > 0:
        db.add(
            DemandForecast(
                org_id=current.org_id,
                item_id=item_id,
                period_start=needed_by,
                quantity=body.quantity,
                source="planning_grid",
                created_by=current.id,
            )
        )
    await log_event(
        db,
        action="forecast.planned_via_grid",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
    )
    await db.commit()

    grid = await compute_planning_grid(db, current.org_id)
    for product in grid.products:
        if product.item_id == item_id:
            return _product_to_response(product)
    # Cleared to zero and the item has no BOM, so it is no longer a product row.
    return GridProductResponse(
        item_id=str(item_id),
        item_sku=item.sku,
        item_name=item.name,
        planned_qty=Decimal(0),
        needed_by=None,
        on_hand=Decimal(0),
        materials_status="in_stock",
        missing_materials=[],
        ready_by=None,
        suggested_qty=None,
        detail=[],
    )
```

Add the imports this needs:

```python
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_role
from app.middleware.audit import log_event
```

Note: `require_role("owner", "manager")` is already in place from the Task 1
fix wave, so this step keeps it rather than introducing it.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_planning_grid.py -v
```

Expected: all tests PASS (25 total).

- [ ] **Step 5: Run the full backend suite**

```bash
cd backend && python -m pytest -q
```

Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/planning_grid.py backend/tests/test_planning_grid.py
git commit -m "feat: validate and audit planned-quantity writes from the grid"
```

---

### Task 5: The grid page

**Files:**
- Create: `frontend/src/pages/planning/types.ts`
- Modify: `frontend/src/pages/Planning.tsx` (full replacement)
- Test: `frontend/src/pages/Planning.render.test.tsx`

**Interfaces:**
- Consumes: `GET /planning/grid` from Tasks 1-3.
- Produces: `frontend/src/pages/planning/types.ts` exporting `GridProduct`, `GridToBuy`, `GridDetailLine`, `PlanningGridResponse`, and `materialsStatusLabel(status)` / `materialsStatusVariant(status)`; `Planning` page reading the `["planning-grid"]` query key.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/Planning.render.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Planning } from "./Planning";

const GRID = {
  horizon_start: "2026-07-26",
  horizon_end: "2026-10-24",
  products: [
    {
      item_id: "p-1",
      item_sku: "PUMP-A2",
      item_name: "Coolant pump",
      planned_qty: "50.0000",
      needed_by: "2026-08-12",
      on_hand: "8.0000",
      materials_status: "missing",
      missing_materials: ["ALU-6061", "SEAL-V2"],
      ready_by: "2026-08-19",
      suggested_qty: null,
      detail: [],
    },
    {
      item_id: "p-2",
      item_sku: "HOUS-H1",
      item_name: "Housing kit",
      planned_qty: "200.0000",
      needed_by: "2026-08-30",
      on_hand: "240.0000",
      materials_status: "in_stock",
      missing_materials: [],
      ready_by: "2026-07-26",
      suggested_qty: null,
      detail: [],
    },
  ],
  to_buy: [],
  summary: { missing: 1, arriving: 0, in_stock: 1 },
};

function render(seed: (qc: QueryClient) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  return renderToString(
    createElement(
      MemoryRouter,
      null,
      createElement(QueryClientProvider, { client: qc }, createElement(Planning)),
    ),
  )
    .split("<!-- -->")
    .join("");
}

describe("Planning grid", () => {
  it("renders without data without crashing", () => {
    const html = render();
    expect(html).toContain("Planning");
  });

  it("shows a row per product with its material status", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("PUMP-A2");
    expect(html).toContain("Coolant pump");
    expect(html).toContain("2 missing");
    expect(html).toContain("HOUS-H1");
    expect(html).toContain("All in stock");
  });

  it("summarises the counts above the grid", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("1 product needs materials");
  });

  it("names the missing materials on the row", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("ALU-6061, SEAL-V2");
  });

  it("shows the planned quantity in an editable input", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain('value="50"');
    expect(html).toContain('value="200"');
  });

  it("says everything is covered when no product is short", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [GRID.products[1]],
        summary: { missing: 0, arriving: 0, in_stock: 1 },
      }),
    );
    expect(html).toContain("Everything is covered");
  });

  it("invites the user to add a bill of materials when there is nothing to plan", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [],
        summary: { missing: 0, arriving: 0, in_stock: 0 },
      }),
    );
    expect(html).toContain("Add a bill of materials to start planning");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: FAIL - the current `Planning` renders the old forecast page, so "2 missing" and the other strings are absent.

- [ ] **Step 3: Write the shared types**

Create `frontend/src/pages/planning/types.ts`:

```ts
import type { BadgeProps } from "@/components/ui/badge";

export interface GridDetailLine {
  item_id: string;
  item_sku: string;
  item_name: string;
  is_manufactured: boolean;
  gross_demand: string;
  on_hand: string;
  on_order: string;
  on_draft: string;
  net_requirement: string;
  suggested_order_quantity: string;
  need_by_date: string | null;
  suggested_order_date: string | null;
}

export interface GridProduct {
  item_id: string;
  item_sku: string;
  item_name: string;
  planned_qty: string;
  needed_by: string | null;
  on_hand: string;
  materials_status: "in_stock" | "arriving" | "missing";
  missing_materials: string[];
  ready_by: string | null;
  suggested_qty: string | null;
  detail: GridDetailLine[];
}

export interface GridToBuy {
  item_id: string;
  item_sku: string;
  item_name: string;
  quantity: string;
  supplier_name: string | null;
  order_by: string | null;
  urgency: "overdue" | "soon" | "later";
}

export interface PlanningGridResponse {
  horizon_start: string;
  horizon_end: string;
  products: GridProduct[];
  to_buy: GridToBuy[];
  summary: { missing: number; arriving: number; in_stock: number };
}

/** Plain-language status wording - the grid never says "net requirement". */
export function materialsStatusLabel(product: GridProduct): string {
  if (product.materials_status === "missing") {
    const count = product.missing_materials.length;
    return `${count} missing`;
  }
  if (product.materials_status === "arriving") return "Arriving";
  return "All in stock";
}

export function materialsStatusVariant(
  status: GridProduct["materials_status"],
): NonNullable<BadgeProps["variant"]> {
  if (status === "missing") return "danger";
  if (status === "arriving") return "warning";
  return "success";
}

/** "50.0000" -> "50", "1.5000" -> "1.5". Trailing zeros are engine noise. */
export function trimQty(value: string): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : value;
}
```

- [ ] **Step 4: Replace the Planning page**

Replace the entire contents of `frontend/src/pages/Planning.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { CalendarRange } from "lucide-react";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import {
  materialsStatusLabel,
  materialsStatusVariant,
  trimQty,
  type PlanningGridResponse,
} from "./planning/types";

function summaryLine(summary: PlanningGridResponse["summary"]): string {
  if (summary.missing > 0) {
    const noun = summary.missing === 1 ? "product needs" : "products need";
    return `${summary.missing} ${noun} materials ordered.`;
  }
  if (summary.arriving > 0) {
    const noun = summary.arriving === 1 ? "product is" : "products are";
    return `${summary.arriving} ${noun} waiting on a delivery.`;
  }
  return "Everything is covered.";
}

export function Planning() {
  const grid = useQuery<PlanningGridResponse>({
    queryKey: ["planning-grid"],
    queryFn: async () => {
      const res = await apiFetch("/planning/grid");
      if (!res.ok) throw new Error("Failed to load the planning grid");
      return res.json();
    },
  });

  return (
    <div>
      <PageHeader
        title="Planning"
        description="Type how many you plan to make. Red means materials are missing."
      />

      {grid.isError && <Alert variant="destructive">{(grid.error as Error).message}</Alert>}
      {grid.isLoading && <Skeleton className="h-40 w-full" />}

      {grid.data && grid.data.products.length === 0 && (
        <EmptyState
          icon={<CalendarRange />}
          title="Add a bill of materials to start planning"
          description="Products you build appear here automatically once they have a recipe."
        />
      )}

      {grid.data && grid.data.products.length > 0 && (
        <>
          <p className="mb-4 text-sm font-medium">{summaryLine(grid.data.summary)}</p>
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Product</TH>
                  <TH>Planned</TH>
                  <TH>In stock</TH>
                  <TH>Materials</TH>
                  <TH>Ready by</TH>
                </TR>
              </THead>
              <TBody>
                {grid.data.products.map((product) => (
                  <TR key={product.item_id}>
                    <TD>
                      <span className="font-medium">{product.item_name}</span>
                      <span className="block text-xs text-muted-foreground">{product.item_sku}</span>
                    </TD>
                    <TD>
                      <Input
                        type="number"
                        min="0"
                        step="1"
                        className="w-24 text-right"
                        aria-label={`Planned quantity for ${product.item_name}`}
                        defaultValue={trimQty(product.planned_qty)}
                      />
                    </TD>
                    <TD>{trimQty(product.on_hand)}</TD>
                    <TD>
                      <Badge variant={materialsStatusVariant(product.materials_status)}>
                        {materialsStatusLabel(product)}
                      </Badge>
                      {product.missing_materials.length > 0 && (
                        <span className="block text-xs text-muted-foreground">
                          {product.missing_materials.join(", ")}
                        </span>
                      )}
                    </TD>
                    <TD>{product.ready_by ?? "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Verify the app still type-checks and builds**

```bash
cd frontend && npx tsc -b
```

Expected: errors only in files that still import the removed forecast pieces (`SuggestFromHistory`, `ImportForecastsCsv` are still on disk, so this should be clean). If `tsc` reports an error in `Planning.tsx`, fix it before committing.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/planning/types.ts frontend/src/pages/Planning.tsx frontend/src/pages/Planning.render.test.tsx
git commit -m "feat: single-grid planning page replacing the forecast workspace"
```

---

### Task 6: Editing the planned quantity

**Files:**
- Modify: `frontend/src/pages/Planning.tsx`
- Create: `frontend/src/pages/planning/PlannedQtyInput.tsx`
- Test: `frontend/src/pages/Planning.render.test.tsx`

**Interfaces:**
- Consumes: `PUT /planning/grid/{item_id}` from Task 4; `GridProduct`, `trimQty` from Task 5.
- Produces: `PlannedQtyInput({ product })` component that owns the debounce, the save mutation, and the suggestion chip.

- [ ] **Step 1: Write the failing test**

Append to the `describe` block in `frontend/src/pages/Planning.render.test.tsx`:

```tsx
  it("offers the history suggestion when nothing is planned", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[0],
            planned_qty: "0.0000",
            needed_by: null,
            suggested_qty: "60.0000",
          },
        ],
      }),
    );
    expect(html).toContain("Use 60 from history");
  });

  it("does not offer a suggestion once a quantity is planned", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).not.toContain("from history");
  });

  it("shows the needed-by date next to the quantity", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain('value="2026-08-12"');
  });
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: FAIL on "Use 60 from history" and the date input value.

- [ ] **Step 3: Write the input component**

Create `frontend/src/pages/planning/PlannedQtyInput.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { trimQty, type GridProduct } from "./types";

// Long enough that typing "120" saves once rather than three times, short
// enough that the colours update while the user is still looking at the row.
const SAVE_DEBOUNCE_MS = 600;

export function PlannedQtyInput({
  product,
  onError,
}: {
  product: GridProduct;
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [qty, setQty] = useState(trimQty(product.planned_qty));
  const [neededBy, setNeededBy] = useState(product.needed_by ?? "");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The row can be re-fetched under us (another save, another user). Adopt the
  // server's value unless the user is mid-edit, which the pending timer marks.
  useEffect(() => {
    if (timer.current === null) {
      setQty(trimQty(product.planned_qty));
      setNeededBy(product.needed_by ?? "");
    }
  }, [product.planned_qty, product.needed_by]);

  const save = useMutation({
    mutationFn: async (next: { quantity: string; needed_by: string | null }) => {
      const res = await apiFetch(`/planning/grid/${product.item_id}`, {
        method: "PUT",
        body: JSON.stringify(next),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Could not save the plan");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["planning-grid"] }),
    onError: (e: Error) => onError(e.message),
  });

  function scheduleSave(nextQty: string, nextDate: string) {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      save.mutate({ quantity: nextQty || "0", needed_by: nextDate || null });
    }, SAVE_DEBOUNCE_MS);
  }

  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
  }, []);

  return (
    <div className="space-y-1">
      <Input
        type="number"
        min="0"
        step="1"
        className="w-24 text-right"
        aria-label={`Planned quantity for ${product.item_name}`}
        value={qty}
        onChange={(e) => {
          setQty(e.target.value);
          scheduleSave(e.target.value, neededBy);
        }}
      />
      <Input
        type="date"
        className="w-36"
        aria-label={`Needed by for ${product.item_name}`}
        value={neededBy}
        onChange={(e) => {
          setNeededBy(e.target.value);
          scheduleSave(qty, e.target.value);
        }}
      />
      {Number(product.planned_qty) <= 0 && product.suggested_qty !== null && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            const suggested = trimQty(product.suggested_qty as string);
            setQty(suggested);
            save.mutate({ quantity: suggested, needed_by: neededBy || null });
          }}
        >
          Use {trimQty(product.suggested_qty)} from history
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Use it in the grid**

In `frontend/src/pages/Planning.tsx`, add the imports:

```tsx
import { useState } from "react";
import { PlannedQtyInput } from "./planning/PlannedQtyInput";
```

Add error state inside `Planning`, above the `useQuery`:

```tsx
  const [error, setError] = useState<string | null>(null);
```

Render it under the existing query error, directly after the `PageHeader`:

```tsx
      {error && <Alert variant="destructive">{error}</Alert>}
```

Replace the whole `<TD>` holding the quantity `<Input>` with:

```tsx
                    <TD>
                      <PlannedQtyInput product={product} onError={setError} />
                    </TD>
```

Remove the now-unused `Input` import from `Planning.tsx`.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: all 10 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/planning/PlannedQtyInput.tsx frontend/src/pages/Planning.tsx frontend/src/pages/Planning.render.test.tsx
git commit -m "feat: edit planned quantities inline on the planning grid"
```

---

### Task 7: Order button and to-buy panel

**Files:**
- Create: `frontend/src/pages/planning/ToBuyPanel.tsx`
- Modify: `frontend/src/pages/Planning.tsx`
- Test: `frontend/src/pages/Planning.render.test.tsx`

**Interfaces:**
- Consumes: `POST /mrp/generate-pos` (existing, owner-only, body `{}` for the default horizon), `GET /orgs/me` for the role, `GridToBuy` from Task 5.
- Produces: `ToBuyPanel({ rows, canOrder })` rendering the list, the order button, and the result.

- [ ] **Step 1: Write the failing test**

Append to the `describe` block in `frontend/src/pages/Planning.render.test.tsx`:

```tsx
  const TO_BUY = [
    {
      item_id: "m-1",
      item_sku: "SEAL-V2",
      item_name: "Viton seal kit",
      quantity: "84.0000",
      supplier_name: "Sealtech",
      order_by: "2026-07-29",
      urgency: "overdue" as const,
    },
    {
      item_id: "m-2",
      item_sku: "FAST-M8",
      item_name: "M8 fastener",
      quantity: "5000.0000",
      supplier_name: null,
      order_by: "2026-09-02",
      urgency: "later" as const,
    },
  ];

  it("lists what to buy with supplier and order-by date", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], { ...GRID, to_buy: TO_BUY });
      qc.setQueryData(["org-me"], { role: "owner", adjust_approval_threshold: "0" });
    });
    expect(html).toContain("To buy for the plan above");
    expect(html).toContain("Viton seal kit");
    expect(html).toContain("order 84 from Sealtech");
    expect(html).toContain("2026-07-29");
    expect(html).toContain("no supplier set");
  });

  it("shows the order button to owners", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], { ...GRID, to_buy: TO_BUY });
      qc.setQueryData(["org-me"], { role: "owner", adjust_approval_threshold: "0" });
    });
    expect(html).toContain("Order all missing materials");
  });

  it("hides the order button from non-owners", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], { ...GRID, to_buy: TO_BUY });
      qc.setQueryData(["org-me"], { role: "operator", adjust_approval_threshold: "0" });
    });
    expect(html).not.toContain("Order all missing materials");
  });

  it("hides the to-buy panel when there is nothing to buy", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], GRID);
      qc.setQueryData(["org-me"], { role: "owner", adjust_approval_threshold: "0" });
    });
    expect(html).not.toContain("To buy for the plan above");
  });
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: FAIL on "To buy for the plan above".

- [ ] **Step 3: Write the panel**

Create `frontend/src/pages/planning/ToBuyPanel.tsx`:

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import { trimQty, type GridToBuy } from "./types";

interface GeneratePosResult {
  created: { po_id: string; po_number: number; supplier_name: string; line_count: number }[];
  unassigned: { item_id: string; item_sku: string }[];
}

const URGENCY_DOT: Record<GridToBuy["urgency"], string> = {
  overdue: "bg-destructive",
  soon: "bg-warning",
  later: "bg-muted-foreground",
};

export function ToBuyPanel({ rows, canOrder }: { rows: GridToBuy[]; canOrder: boolean }) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<GeneratePosResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const order = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/mrp/generate-pos", { method: "POST", body: JSON.stringify({}) });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Could not create the purchase orders");
      }
      return (await res.json()) as GeneratePosResult;
    },
    onSuccess: (data) => {
      setResult(data);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  if (rows.length === 0) return null;

  return (
    <Card className="mt-8">
      <CardHeader className="flex-row items-center justify-between gap-4">
        <CardTitle>To buy for the plan above</CardTitle>
        {canOrder && (
          <Button onClick={() => order.mutate()} loading={order.isPending}>
            Order all missing materials
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Alert variant="destructive">{error}</Alert>}
        <ul className="space-y-2">
          {rows.map((row) => (
            <li key={row.item_id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="flex items-center gap-2">
                <span className={cn("size-2 rounded-full", URGENCY_DOT[row.urgency])} aria-hidden />
                <span className="font-medium">{row.item_name}</span>
                <span className="text-xs text-muted-foreground">{row.item_sku}</span>
              </span>
              <span className="text-muted-foreground">
                {row.supplier_name
                  ? `order ${trimQty(row.quantity)} from ${row.supplier_name}`
                  : `order ${trimQty(row.quantity)}, no supplier set`}
                {row.order_by ? ` · by ${row.order_by}` : ""}
              </span>
            </li>
          ))}
        </ul>
        {result && (
          <div className="space-y-2 rounded-lg border border-border p-4">
            {result.created.length > 0 ? (
              <ul className="space-y-1 text-sm">
                {result.created.map((po) => (
                  <li key={po.po_id} className="flex items-center gap-2">
                    <Badge variant="success">Draft PO #{po.po_number}</Badge>
                    {po.supplier_name} · {po.line_count} line{po.line_count === 1 ? "" : "s"}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Nothing to order.</p>
            )}
            {result.unassigned.length > 0 && (
              <Alert variant="warning">
                No supplier set for: {result.unassigned.map((u) => u.item_sku).join(", ")}. Set one
                under <Link to="/items" className="underline">Items</Link> and order again.
              </Alert>
            )}
            <p className="text-sm text-muted-foreground">
              Review and send them under{" "}
              <Link to="/procurement" className="text-primary hover:underline">
                Procurement
              </Link>
              .
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: Mount it on the page**

In `frontend/src/pages/Planning.tsx`, add the imports:

```tsx
import { ToBuyPanel } from "./planning/ToBuyPanel";
```

Add the role query next to the grid query:

```tsx
  const org = useQuery<{ role: string }>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load organization");
      return res.json();
    },
  });
```

Render the panel directly after the closing `</TableWrap>`, inside the same fragment:

```tsx
          <ToBuyPanel rows={grid.data.to_buy} canOrder={org.data?.role === "owner"} />
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: all 14 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/planning/ToBuyPanel.tsx frontend/src/pages/Planning.tsx frontend/src/pages/Planning.render.test.tsx
git commit -m "feat: to-buy panel and one-click ordering on the planning grid"
```

---

### Task 8: Row expansion

**Files:**
- Create: `frontend/src/pages/planning/ProductDetail.tsx`
- Modify: `frontend/src/pages/Planning.tsx`
- Test: `frontend/src/pages/Planning.render.test.tsx`

**Interfaces:**
- Consumes: `GridProduct.detail` (`GridDetailLine[]`) from Task 3/5.
- Produces: `ProductDetail({ lines })` rendering the engine breakdown table.

- [ ] **Step 1: Write the failing test**

Append to the `describe` block in `frontend/src/pages/Planning.render.test.tsx`:

```tsx
  it("hides the engine detail until the row is expanded", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[0],
            detail: [
              {
                item_id: "p-1",
                item_sku: "PUMP-A2",
                item_name: "Coolant pump",
                is_manufactured: true,
                gross_demand: "50.0000",
                on_hand: "8.0000",
                on_order: "0.0000",
                on_draft: "0.0000",
                net_requirement: "42.0000",
                suggested_order_quantity: "42.0000",
                need_by_date: "2026-08-12",
                suggested_order_date: null,
              },
            ],
          },
        ],
      }),
    );
    expect(html).toContain("Show working");
    expect(html).not.toContain("Still needed");
  });
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: FAIL on "Show working".

- [ ] **Step 3: Write the detail component**

Create `frontend/src/pages/planning/ProductDetail.tsx`:

```tsx
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { trimQty, type GridDetailLine } from "./types";

/** The engine's own numbers. The only place MRP vocabulary is allowed, and
 * only behind an explicit "Show working" click. */
export function ProductDetail({ lines }: { lines: GridDetailLine[] }) {
  if (lines.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing planned for this product yet.</p>;
  }
  return (
    <TableWrap>
      <Table>
        <THead>
          <TR>
            <TH>Item</TH>
            <TH>Needed</TH>
            <TH>In stock</TH>
            <TH>On order</TH>
            <TH>Still needed</TH>
            <TH>Order by</TH>
          </TR>
        </THead>
        <TBody>
          {lines.map((line) => (
            <TR key={line.item_id}>
              <TD className="font-medium">
                {line.item_sku} - {line.item_name}
                {line.is_manufactured && (
                  <Badge variant="accent" className="ml-2">
                    make
                  </Badge>
                )}
              </TD>
              <TD>{trimQty(line.gross_demand)}</TD>
              <TD>{trimQty(line.on_hand)}</TD>
              <TD>{trimQty(String(Number(line.on_order) + Number(line.on_draft)))}</TD>
              <TD className="font-semibold">{trimQty(line.net_requirement)}</TD>
              <TD>{line.suggested_order_date ?? "—"}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </TableWrap>
  );
}
```

- [ ] **Step 4: Wire the expansion into the grid**

In `frontend/src/pages/Planning.tsx`, add the imports:

```tsx
import { ProductDetail } from "./planning/ProductDetail";
import { Button } from "@/components/ui/button";
```

Add the expansion state next to the error state:

```tsx
  const [expanded, setExpanded] = useState<string | null>(null);
```

Add a sixth header cell after `<TH>Ready by</TH>`:

```tsx
                  <TH className="text-right">Detail</TH>
```

Then replace the row body so the detail row can follow it. Replace the entire `{grid.data.products.map((product) => (...))}` body with this, which is the Task 5/6/7 row plus the toggle cell and the expansion row:

```tsx
                {grid.data.products.map((product) => (
                  <Fragment key={product.item_id}>
                    <TR>
                      <TD>
                        <span className="font-medium">{product.item_name}</span>
                        <span className="block text-xs text-muted-foreground">{product.item_sku}</span>
                      </TD>
                      <TD>
                        <PlannedQtyInput product={product} onError={setError} />
                      </TD>
                      <TD>{trimQty(product.on_hand)}</TD>
                      <TD>
                        <Badge variant={materialsStatusVariant(product.materials_status)}>
                          {materialsStatusLabel(product)}
                        </Badge>
                        {product.missing_materials.length > 0 && (
                          <span className="block text-xs text-muted-foreground">
                            {product.missing_materials.join(", ")}
                          </span>
                        )}
                      </TD>
                      <TD>{product.ready_by ?? "—"}</TD>
                      <TD className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            setExpanded(expanded === product.item_id ? null : product.item_id)
                          }
                        >
                          {expanded === product.item_id ? "Hide working" : "Show working"}
                        </Button>
                      </TD>
                    </TR>
                    {expanded === product.item_id && (
                      <TR>
                        <TD colSpan={6}>
                          <ProductDetail lines={product.detail} />
                        </TD>
                      </TR>
                    )}
                  </Fragment>
                ))}
```

Import `Fragment` from react: `import { Fragment, useState } from "react";`

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: all 15 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/planning/ProductDetail.tsx frontend/src/pages/Planning.tsx frontend/src/pages/Planning.render.test.tsx
git commit -m "feat: expandable per-product working on the planning grid"
```

---

### Task 9: Retire the replaced surfaces

**Files:**
- Modify: `frontend/src/pages/Procurement.tsx` (drop the "Generate from Plan" tab)
- Delete: `frontend/src/pages/GeneratePos.tsx`, `frontend/src/pages/planning/SuggestFromHistory.tsx`, `frontend/src/pages/planning/suggestLogic.ts`, `frontend/src/pages/planning/suggestLogic.test.ts`
- Modify: `frontend/src/pages/Planning.tsx` (CSV import behind a button)
- Modify: `frontend/src/pages/Dashboard.tsx:252` (brief rows link to `/planning`), `frontend/src/pages/Dashboard.tsx:98` (explore card copy)
- Modify: `frontend/src/lib/nav.ts:68` (keywords)
- Test: `frontend/src/pages/Planning.render.test.tsx`

**Interfaces:**
- Consumes: `ImportForecastsCsvSection({ items })` from `frontend/src/pages/planning/ImportForecastsCsv.tsx` (kept, unchanged).
- Produces: no new exports. `GeneratePosPanel` stops existing; nothing may import it afterwards.

- [ ] **Step 1: Write the failing test**

Append to the `describe` block in `frontend/src/pages/Planning.render.test.tsx`:

```tsx
  it("keeps CSV import available behind a button", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("Import from CSV");
  });
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npx vitest run src/pages/Planning.render.test.tsx
```

Expected: FAIL on "Import from CSV".

- [ ] **Step 3: Put CSV import behind a button on the grid**

In `frontend/src/pages/Planning.tsx`, add the imports:

```tsx
import { ImportForecastsCsvSection } from "./planning/ImportForecastsCsv";
```

Add the toggle state and the items query (fetched only when the section is open, since nothing else on this page needs the item catalog):

```tsx
  const [showImport, setShowImport] = useState(false);

  const items = useQuery<{ id: string; sku: string }[]>({
    queryKey: ["items"],
    enabled: showImport,
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });
```

Give the `PageHeader` an action:

```tsx
        actions={
          <Button variant="secondary" size="sm" onClick={() => setShowImport((v) => !v)}>
            Import from CSV
          </Button>
        }
```

And render the section under the header when open:

```tsx
      {showImport && <ImportForecastsCsvSection items={items.data ?? []} />}
```

- [ ] **Step 4: Remove the Generate from Plan tab**

In `frontend/src/pages/Procurement.tsx`, delete the `GeneratePosPanel` import, remove `{ value: "generate", label: "Generate from Plan" }` from `TABS`, and delete the whole `{tab === "generate" && (...)}` block. The `useSearchParams` call stays (the Build Calculator tab still uses `?tab=`), but the `horizonStart` / `horizonEnd` params it passed are gone with the panel.

Then delete the panel itself:

```bash
git rm frontend/src/pages/GeneratePos.tsx
```

- [ ] **Step 5: Remove the suggest-from-history card**

The grid's per-row suggestion chip (Task 6) replaces it:

```bash
git rm frontend/src/pages/planning/SuggestFromHistory.tsx frontend/src/pages/planning/suggestLogic.ts frontend/src/pages/planning/suggestLogic.test.ts
```

- [ ] **Step 6: Point the Dashboard at the grid**

In `frontend/src/pages/Dashboard.tsx`, change the brief line's `to="/warehouse"` (line 252) to `to="/planning"`, and update the Planning explore card (line 98) description from `"Forecasts, MRP, and auto-POs"` to `"What to make and what to order"`.

- [ ] **Step 7: Update the nav keywords**

In `frontend/src/lib/nav.ts` line 68, replace the Planning entry's keywords:

```ts
      { to: "/planning", label: "Planning", icon: CalendarRange, keywords: ["mrp", "forecast", "demand", "what to do", "shortages", "materials"], featureKey: "planning" },
```

- [ ] **Step 8: Run the frontend suite**

```bash
cd frontend && npx vitest run
```

Expected: PASS, including `Planning.render.test.tsx` (16 tests). The deleted `suggestLogic.test.ts` no longer runs; `csvImport.test.ts` still does.

- [ ] **Step 9: Type-check and build**

```bash
cd frontend && npx tsc -b
```

Expected: clean. Any error naming `GeneratePos`, `SuggestFromHistoryCard`, or `suggestLogic` means a leftover import: remove it.

- [ ] **Step 10: Verify in the browser**

Start the dev server with the preview tooling (never `npm run dev` in a shell), open `/planning`, and confirm: the grid renders, typing a quantity turns the material chip red or green within a second, "Show working" expands the detail, and the to-buy panel lists materials. Then check `/procurement` no longer shows a "Generate from Plan" tab and `/` (Dashboard) brief rows link to Planning. Capture a screenshot of `/planning` for the commit message reviewer.

- [ ] **Step 11: Commit**

```bash
git add -A frontend/src
git commit -m "refactor: retire the forecast workspace and generate-from-plan tab"
```

---

## Post-implementation

- [ ] Run both suites one final time: `cd backend && python -m pytest -q` and `cd frontend && npx vitest run`.
- [ ] Run `graphify update .` to refresh the knowledge graph (new service and route modules, deleted pages).
- [ ] Use `superpowers:requesting-code-review` before merging.
