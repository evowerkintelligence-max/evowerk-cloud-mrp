# BOM Units, Editable Recipes, and Build Calculator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a recipe line be authored in a convenient unit (e.g. "500 g" on a kg-based item), let an active recipe be revised without re-entering every line, and let someone compute "I'm building N of this - what do I need to buy" directly into draft purchase orders, with a mandatory review step before anything is created.

**Architecture:** Three additive backend features sharing the existing BOM/MRP/procurement models, plus a refactor that extracts `compute_mrp_plan`'s explosion/netting core into a reusable helper so the new "build calculator" reuses the same, already-tested engine instead of duplicating it. No existing endpoint's request/response shape changes in a breaking way - all additions are new optional fields or new endpoints.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic on the backend, React + TanStack Query + Tailwind on the frontend, matching the existing codebase throughout.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-14-bom-recipe-redesign-design.md`.
- `BomLine.quantity` stays the single source of truth for downstream consumers (MRP, cost rollups) and is always in the component item's base unit - never trust a client-submitted `quantity` when a unit conversion is involved; the server always recomputes it.
- `POST /mrp/build-plan/generate-pos` must recompute the plan server-side from `{item_id, build_quantity, yield_loss_pct}` - never trust client-submitted line quantities, matching the existing `generate_po`/`generate_pos` pattern.
- Every new mutating endpoint requires the `owner` role (`Depends(require_role("owner"))`), matching every existing BOM/PO-mutating endpoint.
- Every new model gets `org_id` directly (not just via join), matching every existing model in this codebase.
- New alembic migration `down_revision` must chain from the current head, `d5f9a1c73e28` (bom_line_scrap_pct).
- Backend tests use `pytest -q` from `backend/`; frontend type-check uses `npx tsc --noEmit -p .` from `frontend/`.

---

### Task 1: Item units - data model, migration, and CRUD API

**Files:**
- Modify: `backend/app/models/bom.py` (add `ItemUnit` model)
- Create: `backend/alembic/versions/f3a8c1e92b47_item_units.py`
- Modify: `backend/app/api/routes/items.py` (add unit CRUD endpoints)
- Test: `backend/tests/test_bom.py` (add unit CRUD tests)

**Interfaces:**
- Consumes: `app.db.base.Base`, `app.api.deps.get_or_404`/`require_role`/`get_current_user`, existing `Item` model.
- Produces: `ItemUnit` model (`id, org_id, item_id, code, factor_to_base`) for Task 2 to reference from `BomLine`. `POST/GET/DELETE /items/{item_id}/units` endpoints.

- [ ] **Step 1: Add the `ItemUnit` model**

In `backend/app/models/bom.py`, add this class after `Item` (before `Bom`):

```python
class ItemUnit(Base):
    __tablename__ = "item_units"
    __table_args__ = (UniqueConstraint("item_id", "code", name="uq_item_units_item_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A label the recipe author picks, e.g. "box", "g" - only meaningful
    # together with factor_to_base, and only for this specific item (a box of
    # screws and a box of bolts don't hold the same count).
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    # 1 of this unit = factor_to_base * the item's own base unit_of_measure.
    factor_to_base: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
```

**Step 2: Write the migration**

Create `backend/alembic/versions/f3a8c1e92b47_item_units.py`:

```python
"""item units

Revision ID: f3a8c1e92b47
Revises: d5f9a1c73e28
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "f3a8c1e92b47"  # pragma: allowlist secret
down_revision = "d5f9a1c73e28"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("factor_to_base", sa.Numeric(18, 6), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "code", name="uq_item_units_item_code"),
    )
    op.create_index("ix_item_units_org_id", "item_units", ["org_id"])
    op.create_index("ix_item_units_item_id", "item_units", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_item_units_item_id", table_name="item_units")
    op.drop_index("ix_item_units_org_id", table_name="item_units")
    op.drop_table("item_units")
```

- [ ] **Step 3: Write the failing tests**

Add to `backend/tests/test_bom.py` (uses the existing `_register_and_login`/`_create_item` helpers already in that file):

```python
async def test_item_unit_create_list_and_duplicate_rejected(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="UNIT-1", name="Screws", item_type="raw_material")

    create = await client.post(
        f"/items/{item['id']}/units", json={"code": "box", "factor_to_base": "100"}, headers=headers
    )
    assert create.status_code == 201
    body = create.json()
    assert body["code"] == "box"
    assert body["factor_to_base"] == "100.000000"

    listed = await client.get(f"/items/{item['id']}/units", headers=headers)
    assert listed.status_code == 200
    assert [u["code"] for u in listed.json()] == ["box"]

    dupe = await client.post(
        f"/items/{item['id']}/units", json={"code": "box", "factor_to_base": "50"}, headers=headers
    )
    assert dupe.status_code == 409


async def test_item_unit_rejects_non_positive_factor(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="UNIT-2", name="Bolts", item_type="raw_material")

    res = await client.post(
        f"/items/{item['id']}/units", json={"code": "box", "factor_to_base": "0"}, headers=headers
    )
    assert res.status_code == 400


async def test_item_unit_delete_blocked_when_in_use(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="UNIT-FG", name="Widget", item_type="finished_good")
    comp = await _create_item(client, token, sku="UNIT-3", name="Screws", item_type="raw_material")

    unit = await client.post(
        f"/items/{comp['id']}/units", json={"code": "box", "factor_to_base": "100"}, headers=headers
    )
    unit_id = unit.json()["id"]

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": comp["id"],
                    "quantity": "1",
                    "sequence": 0,
                    "unit_id": unit_id,
                    "unit_quantity": "1",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )

    delete = await client.delete(f"/items/{comp['id']}/units/{unit_id}", headers=headers)
    assert delete.status_code == 409


async def test_item_unit_delete_succeeds_when_unused(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="UNIT-4", name="Nuts", item_type="raw_material")

    unit = await client.post(
        f"/items/{item['id']}/units", json={"code": "box", "factor_to_base": "100"}, headers=headers
    )
    unit_id = unit.json()["id"]

    delete = await client.delete(f"/items/{item['id']}/units/{unit_id}", headers=headers)
    assert delete.status_code == 204
```

The `test_item_unit_delete_blocked_when_in_use` test depends on Task 2's `unit_id`/`unit_quantity` support on `PATCH /boms/{id}` - write it now, it will fail for the wrong reason (400 "unexpected field" or similar) until Task 2 lands, then pass once both tasks are done. That's expected; note it and continue.

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_bom.py -k test_item_unit -v` (from `backend/`)
Expected: FAIL - `404 Not Found` for `/items/{id}/units` (route doesn't exist yet).

- [ ] **Step 5: Add the CRUD endpoints**

In `backend/app/api/routes/items.py`, add `ItemUnit` to the import from `app.models.bom` (line 18 becomes):

```python
from app.models.bom import BomLine, Item, ItemUnit
```

Add these schemas after `ItemResponse` (after line 89's `_to_response` function, before `_resolve_supplier_id`):

```python
class ItemUnitCreateRequest(BaseModel):
    code: str
    factor_to_base: Decimal


class ItemUnitResponse(BaseModel):
    id: str
    item_id: str
    code: str
    factor_to_base: Decimal


def _unit_to_response(unit: ItemUnit) -> ItemUnitResponse:
    return ItemUnitResponse(
        id=str(unit.id), item_id=str(unit.item_id), code=unit.code, factor_to_base=unit.factor_to_base
    )
```

Add these routes at the end of the file:

```python
@router.post("/{item_id}/units", response_model=ItemUnitResponse, status_code=status.HTTP_201_CREATED)
async def create_item_unit(
    item_id: uuid.UUID,
    body: ItemUnitCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    code = body.code.strip()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "code is required")
    if body.factor_to_base <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "factor_to_base must be positive")

    unit = ItemUnit(org_id=current.org_id, item_id=item_id, code=code, factor_to_base=body.factor_to_base)
    db.add(unit)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f'Unit "{code}" already exists for this item'
        ) from None
    await db.commit()
    return _unit_to_response(unit)


@router.get("/{item_id}/units", response_model=list[ItemUnitResponse])
async def list_item_units(
    item_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    result = await db.execute(
        select(ItemUnit)
        .where(ItemUnit.org_id == current.org_id, ItemUnit.item_id == item_id)
        .order_by(ItemUnit.code)
    )
    return [_unit_to_response(u) for u in result.scalars().all()]


@router.delete("/{item_id}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item_unit(
    item_id: uuid.UUID,
    unit_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    unit = await get_or_404(db, ItemUnit, current.org_id, unit_id, "Unit not found")
    if unit.item_id != item_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found")
    in_use = await db.scalar(select(BomLine.id).where(BomLine.unit_id == unit_id).limit(1))
    if in_use is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This unit is used by a BOM line and can't be deleted"
        )
    await db.delete(unit)
    await db.commit()
```

This references `BomLine.unit_id`, which Task 2 adds - `delete_item_unit` won't type-check/run correctly until Task 2 lands. That's fine; both tasks are committed close together and the full test run at the end of Task 2 covers this path.

- [ ] **Step 6: Run tests to verify create/list/duplicate/non-positive-factor pass**

Run: `pytest tests/test_bom.py -k "test_item_unit_create_list_and_duplicate_rejected or test_item_unit_rejects_non_positive_factor or test_item_unit_delete_succeeds_when_unused" -v`
Expected: PASS (the in-use-guard test still fails until Task 2 - that's expected, don't fix it here).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/bom.py backend/alembic/versions/f3a8c1e92b47_item_units.py backend/app/api/routes/items.py backend/tests/test_bom.py
git commit -m "Add per-item unit conversion catalog (item_units) with CRUD API"
```

---

### Task 2: BOM line unit conversion

**Files:**
- Modify: `backend/app/models/bom.py` (add `unit_id`/`unit_quantity` to `BomLine`)
- Create: `backend/alembic/versions/a7c92e4f18d5_bom_line_unit.py`
- Modify: `backend/app/api/routes/boms.py`
- Test: `backend/tests/test_bom.py`

**Interfaces:**
- Consumes: `ItemUnit` model from Task 1.
- Produces: `BomLine.unit_id`/`unit_quantity` columns; `BomLineInput`/`BomLineResponse` gain `unit_id`, `unit_quantity`, and (response-only) `unit_code` fields, consumed by Task 6 (frontend).

- [ ] **Step 1: Add columns to `BomLine`**

In `backend/app/models/bom.py`, add to the end of the `BomLine` class (after the `scrap_pct` column added previously):

```python
    # Optional authoring convenience: the line's quantity as typed in a unit
    # other than the component's base unit (e.g. "500" in unit "g" on a
    # kg-based item). `quantity` above is always the converted, canonical
    # base-unit figure - MRP/cost rollups never need to know units exist.
    # Both null together means "authored directly in the base unit".
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("item_units.id"), nullable=True
    )
    unit_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
```

- [ ] **Step 2: Write the migration**

Create `backend/alembic/versions/a7c92e4f18d5_bom_line_unit.py`:

```python
"""bom line unit conversion

Revision ID: a7c92e4f18d5
Revises: f3a8c1e92b47
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "a7c92e4f18d5"  # pragma: allowlist secret
down_revision = "f3a8c1e92b47"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bom_lines", sa.Column("unit_id", sa.Uuid(), nullable=True))
    op.add_column("bom_lines", sa.Column("unit_quantity", sa.Numeric(12, 4), nullable=True))
    op.create_foreign_key(
        "fk_bom_lines_unit_id", "bom_lines", "item_units", ["unit_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_bom_lines_unit_id", "bom_lines", type_="foreignkey")
    op.drop_column("bom_lines", "unit_quantity")
    op.drop_column("bom_lines", "unit_id")
```

- [ ] **Step 3: Write the failing test**

Add to `backend/tests/test_bom.py`:

```python
async def test_bom_line_unit_conversion_computes_base_quantity(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="CONV-FG", name="Widget", item_type="finished_good")
    comp = await _create_item(
        client, token, sku="CONV-1", name="Steel", item_type="raw_material", unit_of_measure="kg"
    )
    unit = await client.post(
        f"/items/{comp['id']}/units", json={"code": "g", "factor_to_base": "0.001"}, headers=headers
    )
    unit_id = unit.json()["id"]

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    lines = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": comp["id"],
                    # Deliberately wrong client-submitted quantity - the
                    # server must ignore it and recompute from unit_quantity.
                    "quantity": "999",
                    "sequence": 0,
                    "unit_id": unit_id,
                    "unit_quantity": "500",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert lines.status_code == 200
    line = lines.json()["lines"][0]
    assert line["quantity"] == "0.5000"
    assert line["unit_id"] == unit_id
    assert line["unit_quantity"] == "500.0000"
    assert line["unit_code"] == "g"


async def test_bom_line_unit_must_belong_to_its_component(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="CONV-FG2", name="Gadget", item_type="finished_good")
    comp_a = await _create_item(client, token, sku="CONV-A", name="Steel", item_type="raw_material")
    comp_b = await _create_item(client, token, sku="CONV-B", name="Copper", item_type="raw_material")
    unit_for_a = await client.post(
        f"/items/{comp_a['id']}/units", json={"code": "g", "factor_to_base": "0.001"}, headers=headers
    )

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    res = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": comp_b["id"],
                    "quantity": "1",
                    "sequence": 0,
                    "unit_id": unit_for_a.json()["id"],
                    "unit_quantity": "500",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert res.status_code == 400
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_bom.py -k "test_bom_line_unit" -v`
Expected: FAIL - `unit_id`/`unit_quantity` rejected as unexpected fields or missing from response.

- [ ] **Step 5: Implement**

In `backend/app/api/routes/boms.py`, add `ItemUnit` to the `app.models.bom` import (line 18):

```python
from app.models.bom import Bom, BomLine, Item, ItemUnit
```

Update `BomLineInput` (currently ends with `scrap_pct: Decimal = Decimal(0)`):

```python
class BomLineInput(BaseModel):
    component_item_id: str
    quantity: Decimal
    sequence: int = 0
    # User-set expected material loss for this component, as a percent - see
    # BomLine.scrap_pct for how MRP applies it.
    scrap_pct: Decimal = Decimal(0)
    # Optional: author this line in a unit other than the component's base
    # unit. Both null means "quantity is already in the base unit" - the
    # server ignores `quantity` and recomputes it from unit_quantity *
    # the unit's factor_to_base whenever unit_id is set.
    unit_id: str | None = None
    unit_quantity: Decimal | None = None
```

Update `BomLineResponse` (add after `scrap_pct: Decimal`):

```python
class BomLineResponse(BaseModel):
    id: str
    component_item_id: str
    component_sku: str
    component_name: str
    unit_of_measure: str
    quantity: Decimal
    sequence: int
    scrap_pct: Decimal
    unit_id: str | None
    unit_quantity: Decimal | None
    unit_code: str | None
```

Rewrite `update_bom_lines`'s validation loop and line-building. Replace the whole function body from `lines_in = body.lines` through the `bom.updated_at = ...` line (everything up to `await _flush_checking_version(db)`) with:

```python
    lines_in = body.lines
    bom = await get_or_404(db, Bom, current.org_id, bom_id, "BOM not found")
    if bom.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft BOMs can be edited")
    if ensure_utc(bom.updated_at) != ensure_utc(body.expected_updated_at):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This BOM was changed elsewhere since you loaded it - refresh and try again",
        )
    if not lines_in:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A BOM needs at least one line")

    component_ids: set[uuid.UUID] = set()
    unit_ids: set[uuid.UUID] = set()
    parsed: list[tuple[BomLineInput, uuid.UUID, uuid.UUID | None]] = []
    for line_in in lines_in:
        if line_in.scrap_pct < 0 or line_in.scrap_pct > MAX_SCRAP_PCT:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"scrap_pct must be between 0 and {MAX_SCRAP_PCT}")
        try:
            component_id = uuid.UUID(line_in.component_item_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid component_item_id") from None
        if component_id == bom.item_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "A BOM can't list its own item as a component")
        component_ids.add(component_id)

        unit_uuid: uuid.UUID | None = None
        if line_in.unit_id is not None:
            if line_in.unit_quantity is None or line_in.unit_quantity <= 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "unit_quantity must be positive when unit_id is set"
                )
            try:
                unit_uuid = uuid.UUID(line_in.unit_id)
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid unit_id") from None
            unit_ids.add(unit_uuid)
        parsed.append((line_in, component_id, unit_uuid))

    # One query for every distinct component instead of one per line - a BOM
    # with dozens of lines was otherwise issuing that many item lookups.
    found_result = await db.execute(
        select(Item.id).where(Item.org_id == current.org_id, Item.id.in_(component_ids))
    )
    if {row[0] for row in found_result.all()} != component_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")

    units_by_id: dict[uuid.UUID, ItemUnit] = {}
    if unit_ids:
        units_result = await db.execute(
            select(ItemUnit).where(ItemUnit.org_id == current.org_id, ItemUnit.id.in_(unit_ids))
        )
        units_by_id = {u.id: u for u in units_result.scalars().all()}
        if set(units_by_id) != unit_ids:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found")

    # Same check activate() runs, surfaced here so a cycle is caught the moment
    # it's drafted rather than after the user has finished building the BOM.
    # activate() re-checks anyway, since the active-BOM graph can shift between
    # this save and that activation.
    await _reject_if_cycle(db, current.org_id, bom.item_id, component_ids)

    final_lines: list[BomLine] = []
    for line_in, component_id, unit_uuid in parsed:
        if unit_uuid is not None:
            unit = units_by_id[unit_uuid]
            if unit.item_id != component_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "Unit does not belong to this line's component item"
                )
            quantity = line_in.unit_quantity * unit.factor_to_base
        else:
            quantity = line_in.quantity
        if quantity <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
        if quantity > MAX_LINE_QUANTITY:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"quantity must not exceed {MAX_LINE_QUANTITY}")
        final_lines.append(
            BomLine(
                org_id=current.org_id,
                bom_id=bom.id,
                component_item_id=component_id,
                quantity=quantity,
                sequence=line_in.sequence,
                scrap_pct=line_in.scrap_pct,
                unit_id=unit_uuid,
                unit_quantity=line_in.unit_quantity if unit_uuid is not None else None,
            )
        )

    await db.execute(BomLine.__table__.delete().where(BomLine.bom_id == bom.id))
    for line in final_lines:
        db.add(line)
    # bom_lines is a separate table, so nothing here dirties the boms row on
    # its own - bump updated_at explicitly or the CAS check above would never
    # actually detect this edit on a subsequent PATCH.
    bom.updated_at = datetime.now(timezone.utc)
    await _flush_checking_version(db)
```

(`await _flush_checking_version(db)` through the end of the function - the `log_event`/`commit`/`return` - stays exactly as it is; only the body above it changes.)

Now update `get_bom`'s line-building block, which currently does:

```python
    lines_result = await db.execute(
        select(BomLine, Item)
        .join(Item, Item.id == BomLine.component_item_id)
        .where(BomLine.bom_id == bom.id)
        .order_by(BomLine.sequence)
    )
    lines = [
        BomLineResponse(
            id=str(line.id),
            component_item_id=str(line.component_item_id),
            component_sku=component.sku,
            component_name=component.name,
            unit_of_measure=component.unit_of_measure,
            quantity=line.quantity,
            sequence=line.sequence,
            scrap_pct=line.scrap_pct,
        )
        for line, component in lines_result.all()
    ]
```

Replace with a query that also outer-joins `ItemUnit` (a line's `unit_id` can be null):

```python
    lines_result = await db.execute(
        select(BomLine, Item, ItemUnit)
        .join(Item, Item.id == BomLine.component_item_id)
        .outerjoin(ItemUnit, ItemUnit.id == BomLine.unit_id)
        .where(BomLine.bom_id == bom.id)
        .order_by(BomLine.sequence)
    )
    lines = [
        BomLineResponse(
            id=str(line.id),
            component_item_id=str(line.component_item_id),
            component_sku=component.sku,
            component_name=component.name,
            unit_of_measure=component.unit_of_measure,
            quantity=line.quantity,
            sequence=line.sequence,
            scrap_pct=line.scrap_pct,
            unit_id=str(line.unit_id) if line.unit_id else None,
            unit_quantity=line.unit_quantity,
            unit_code=unit.code if unit else None,
        )
        for line, component, unit in lines_result.all()
    ]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_bom.py -v` (from `backend/`)
Expected: PASS - all of `test_bom.py`, including the two new tests here and the Task 1 in-use-guard test that depended on this.

- [ ] **Step 7: Run the full backend suite**

Run: `pytest -q` (from `backend/`)
Expected: PASS - this touched `update_bom_lines`/`get_bom`, both covered by `test_bom.py` and `test_mrp_explosion.py` (MRP reads `BomLine.quantity` directly, unaffected).

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/bom.py backend/alembic/versions/a7c92e4f18d5_bom_line_unit.py backend/app/api/routes/boms.py backend/tests/test_bom.py
git commit -m "Support authoring BOM line quantities in a per-item custom unit"
```

---

### Task 3: Streamlined recipe revision (`POST /boms/{bom_id}/revise`)

**Files:**
- Modify: `backend/app/api/routes/boms.py`
- Test: `backend/tests/test_bom.py`

**Interfaces:**
- Consumes: existing `Bom`/`BomLine` models, `MAX_VERSION_ATTEMPTS`, `_bom_to_response` helper, all already in `boms.py`.
- Produces: `POST /boms/{bom_id}/revise` → `BomDetailResponse`, consumed by Task 7 (frontend "Edit" button).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_bom.py`:

```python
async def test_revise_active_bom_copies_lines_into_new_draft(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="REV-FG", name="Widget", item_type="finished_good")
    comp = await _create_item(client, token, sku="REV-1", name="Bolt", item_type="raw_material")

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {"component_item_id": comp["id"], "quantity": "4.5", "sequence": 0, "scrap_pct": "5"}
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200

    revise = await client.post(f"/boms/{bom_data['id']}/revise", headers=headers)
    assert revise.status_code == 201
    revised = revise.json()
    assert revised["status"] == "draft"
    assert revised["version"] == 2
    assert len(revised["lines"]) == 1
    assert revised["lines"][0]["component_sku"] == "REV-1"
    assert revised["lines"][0]["quantity"] == "4.5000"
    assert revised["lines"][0]["scrap_pct"] == "5.00"

    # Original active version is untouched.
    original = await client.get(f"/boms/{bom_data['id']}", headers=headers)
    assert original.json()["status"] == "active"
    assert len(original.json()["lines"]) == 1


async def test_revise_rejects_non_active_bom(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="REV-2", name="Draft Only", item_type="finished_good")
    bom = await client.post("/boms", json={"item_id": item["id"]}, headers=headers)

    revise = await client.post(f"/boms/{bom.json()['id']}/revise", headers=headers)
    assert revise.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bom.py -k test_revise -v`
Expected: FAIL - `404 Not Found` (route doesn't exist).

- [ ] **Step 3: Implement**

Add to `backend/app/api/routes/boms.py`, after `activate_bom` and before `delete_bom`:

```python
@router.post("/{bom_id}/revise", response_model=BomDetailResponse, status_code=status.HTTP_201_CREATED)
async def revise_bom(
    bom_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Clone an active BOM's lines into a new draft version, so revising a
    recipe doesn't mean re-entering every line from scratch. The source BOM
    stays active and untouched - a past production run still points at
    exactly the recipe it used. Same version allocation as create_bom."""
    source = await get_or_404(db, Bom, current.org_id, bom_id, "BOM not found")
    if source.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active BOM can be revised")

    source_lines_result = await db.execute(
        select(BomLine).where(BomLine.bom_id == source.id).order_by(BomLine.sequence)
    )
    source_lines = source_lines_result.scalars().all()

    for attempt in range(MAX_VERSION_ATTEMPTS):
        max_version = await db.scalar(
            select(func.max(Bom.version)).where(Bom.org_id == current.org_id, Bom.item_id == source.item_id)
        )
        new_bom = Bom(
            org_id=current.org_id,
            item_id=source.item_id,
            version=(max_version or 0) + 1,
            status="draft",
            created_by=current.id,
        )
        db.add(new_bom)
        try:
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
            if attempt == MAX_VERSION_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Could not allocate a BOM version, please retry"
                ) from None

    for line in source_lines:
        db.add(
            BomLine(
                org_id=current.org_id,
                bom_id=new_bom.id,
                component_item_id=line.component_item_id,
                quantity=line.quantity,
                sequence=line.sequence,
                scrap_pct=line.scrap_pct,
                unit_id=line.unit_id,
                unit_quantity=line.unit_quantity,
            )
        )
    await db.flush()

    await log_event(
        db,
        action="bom.revised",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(new_bom.id),
        metadata={"source_bom_id": str(source.id), "item_id": str(source.item_id), "version": new_bom.version},
    )
    await db.commit()
    return await get_bom(new_bom.id, current, db)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bom.py -v`
Expected: PASS - all tests including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/boms.py backend/tests/test_bom.py
git commit -m "Add POST /boms/{id}/revise to clone an active recipe into an editable draft"
```

---

### Task 4: Refactor MRP explosion into a reusable helper

**Files:**
- Modify: `backend/app/services/mrp.py`
- Test: `backend/tests/test_mrp_explosion.py` (regression only - no new test file; this is a pure refactor)

**Interfaces:**
- Consumes: nothing new - restructures existing `compute_mrp_plan` internals.
- Produces: `_explode_and_net(db, org_id, seed_gross: dict[UUID, Decimal], seed_due: dict[UUID, date] | None) -> list[MrpPlanLine]`, consumed by Task 5's `compute_build_plan`.

- [ ] **Step 1: Confirm current behavior is the regression baseline**

Run: `pytest tests/test_mrp_explosion.py -v` (from `backend/`)
Expected: PASS (all existing tests, unchanged) - this is the baseline the refactor must not break.

- [ ] **Step 2: Refactor `compute_mrp_plan`**

In `backend/app/services/mrp.py`, replace the entire `compute_mrp_plan` function (from `async def compute_mrp_plan(` through its final `return lines`) with:

```python
async def compute_mrp_plan(
    db: AsyncSession, org_id: uuid.UUID, horizon_start: date, horizon_end: date
) -> list[MrpPlanLine]:
    """Material Requirements Planning: net demand against supply, exploding
    forecasts through active BOMs so a forecast of a finished good becomes
    concrete requirements for the components it's built from.

        net_requirement = max(0, gross_demand - on_hand - on_order - on_draft)
        gross_demand    = independent_demand + dependent_demand

    - independent_demand: the item's own forecast buckets whose period_start is
      in [horizon_start, horizon_end].
    - dependent_demand: quantity_per x parent net_requirement, summed over every
      parent assembly that lists this item in its active BOM.
    - on_hand: for lot-tracked items, sum of *active* lot quantity_remaining
      (on_hold lots - QA hold/recall - are excluded; they can't be consumed, so
      counting them would understate a real shortage). For non-lot-tracked
      items, the general inventory_balances on-hand. The two are disjoint (an
      item is one or the other), so summing them is just "whichever applies".
    - on_order: open PO supply, quantity_ordered - quantity_received across PO
      lines on submitted/partially_received POs.
    - on_draft: outstanding quantity on open *draft* POs - planned but not yet
      committed supply. Offsets the requirement like on_order (so drafting an
      order removes the shortfall and a repeat generation can't double-order),
      but reported separately so submitted vs still-a-draft stays visible.

    The explosion/netting engine itself lives in _explode_and_net, seeded here
    from DemandForecast rows - see that function for the full algorithm.
    Reused as-is (different seed) by services.mrp.compute_build_plan for the
    build calculator, which has no forecast rows to read.
    """
    independent = await _sum_by_item(
        db,
        select(DemandForecast.item_id, func.sum(DemandForecast.quantity))
        .where(
            DemandForecast.org_id == org_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
        .group_by(DemandForecast.item_id),
    )
    if not independent:
        return []

    # Earliest in-horizon forecast date per item - seeds need_by below. A
    # separate query (not reused from `independent`) because this needs MIN,
    # not SUM, over the same rows.
    need_by = await _min_date_by_item(
        db,
        select(DemandForecast.item_id, func.min(DemandForecast.period_start))
        .where(
            DemandForecast.org_id == org_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
        .group_by(DemandForecast.item_id),
    )
    return await _explode_and_net(db, org_id, independent, need_by)


async def _explode_and_net(
    db: AsyncSession,
    org_id: uuid.UUID,
    seed_gross: dict[uuid.UUID, Decimal],
    seed_due: dict[uuid.UUID, date] | None,
) -> list[MrpPlanLine]:
    """The shared explosion/netting core behind compute_mrp_plan (seeded from
    forecasts) and compute_build_plan (seeded from a single build quantity).

    This is *netted* explosion, not gross: each assembly is netted against its
    own on-hand/on-order first, and only the resulting net requirement explodes
    to its components. Finished-good and sub-assembly stock therefore correctly
    reduces how much of each raw component you actually need to buy. Items are
    processed parents-before-components (topological order over the active-BOM
    DAG) so an assembly's net is final before it explodes downward.

    Demand-driven: items reached by neither `seed_gross` nor an explosion
    aren't planning actions, so they're omitted.

    Lead-time offsetting and MOQ rounding are modelled on top of the netting
    above:
    - need_by_date: the earliest date (from seed_due) reaching this item,
      propagated down through explosion. None if seed_due is None/empty or
      this item is never reached by a dated seed (e.g. every build-calculator
      call, which has no dates at all).
    - suggested_order_date = need_by_date - item.lead_time_days, when both are
      known and the item is purchased (not manufactured).
    - suggested_order_quantity rounds net_requirement up to item.min_order_qty
      for purchased items when the MOQ is higher. Explosion always uses the
      raw net, never the MOQ-rounded figure.
    """
    if not seed_gross:
        return []
    need_by = dict(seed_due) if seed_due else {}

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
    on_order = await _sum_by_item(
        db,
        select(
            PurchaseOrderLine.item_id,
            func.sum(PurchaseOrderLine.quantity_ordered - PurchaseOrderLine.quantity_received),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrder.org_id == org_id, PurchaseOrder.status.in_(OPEN_PO_STATUSES))
        .group_by(PurchaseOrderLine.item_id),
    )
    on_draft = await _sum_by_item(
        db,
        select(
            PurchaseOrderLine.item_id,
            func.sum(PurchaseOrderLine.quantity_ordered - PurchaseOrderLine.quantity_received),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrder.org_id == org_id, PurchaseOrder.status == "draft")
        .group_by(PurchaseOrderLine.item_id),
    )
    children = await _load_bom_children(db, org_id)

    relevant: set[uuid.UUID] = set()
    stack = list(seed_gross.keys())
    while stack:
        node = stack.pop()
        if node in relevant:
            continue
        relevant.add(node)
        for child_id, _ in children.get(node, []):
            stack.append(child_id)

    indegree: dict[uuid.UUID, int] = dict.fromkeys(relevant, 0)
    for parent_id in relevant:
        for child_id, _ in children.get(parent_id, []):
            if child_id in relevant:
                indegree[child_id] += 1
    queue = deque(node for node in relevant if indegree[node] == 0)
    order: list[uuid.UUID] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for child_id, _ in children.get(node, []):
            if child_id in relevant:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    queue.append(child_id)
    if len(order) != len(relevant):
        order.extend(node for node in relevant if node not in set(order))

    items = {
        item.id: item
        for item in (await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(relevant))))
        .scalars()
        .all()
    }

    gross: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for item_id, qty in seed_gross.items():
        gross[item_id] += qty

    lines: list[MrpPlanLine] = []
    for item_id in order:
        item = items.get(item_id)
        if item is None:
            continue
        total_gross = gross[item_id]
        hand = on_hand.get(item_id, Decimal(0))
        order_qty = max(Decimal(0), on_order.get(item_id, Decimal(0)))
        draft_qty = max(Decimal(0), on_draft.get(item_id, Decimal(0)))
        net = max(Decimal(0), total_gross - hand - order_qty - draft_qty)
        due = need_by.get(item_id)

        for child_id, qty_per in children.get(item_id, []):
            gross[child_id] += net * qty_per
            if due is not None:
                existing_due = need_by.get(child_id)
                need_by[child_id] = due if existing_due is None else min(existing_due, due)

        if total_gross <= 0:
            continue
        indep = seed_gross.get(item_id, Decimal(0))
        is_manufactured = item_id in children

        suggested_qty = net
        suggested_date = None
        if not is_manufactured and net > 0:
            if item.min_order_qty is not None and item.min_order_qty > net:
                suggested_qty = item.min_order_qty
            if due is not None and item.lead_time_days is not None:
                suggested_date = due - timedelta(days=item.lead_time_days)

        lines.append(
            MrpPlanLine(
                item_id=str(item_id),
                item_sku=item.sku,
                item_name=item.name,
                is_manufactured=is_manufactured,
                independent_demand=indep,
                dependent_demand=total_gross - indep,
                gross_demand=total_gross,
                on_hand=hand,
                on_order=order_qty,
                on_draft=draft_qty,
                net_requirement=net,
                suggested_order_quantity=suggested_qty,
                need_by_date=due,
                suggested_order_date=suggested_date,
            )
        )

    lines.sort(key=lambda ln: (-ln.net_requirement, ln.item_sku))
    return lines
```

This is a pure refactor: `compute_mrp_plan`'s external behavior is unchanged (same inputs produce the same outputs), it just delegates its explosion/netting to `_explode_and_net`.

- [ ] **Step 3: Run tests to verify nothing broke**

Run: `pytest tests/test_mrp_explosion.py -v` (from `backend/`)
Expected: PASS - every test unchanged from Step 1's baseline.

Run: `pytest -q` (from `backend/`)
Expected: PASS - full suite, confirming no other module was affected (nothing else calls `compute_mrp_plan` differently).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/mrp.py
git commit -m "Refactor MRP explosion/netting into a reusable _explode_and_net helper"
```

---

### Task 5: Build calculator - `compute_build_plan` and API endpoints

**Files:**
- Modify: `backend/app/services/mrp.py` (add `compute_build_plan`)
- Modify: `backend/app/api/routes/forecasts.py` (shared PO-grouping helper + two new endpoints)
- Test: `backend/tests/test_build_calculator.py` (new file)

**Interfaces:**
- Consumes: `_explode_and_net` from Task 4; `MrpPlanLine`, `_load_bom_children`'s active-BOM check pattern; existing `_new_draft_po`/`_add_po_lines`/`latest_unit_prices`/`GeneratePosResponse`/`GeneratedPoSummary`/`UnassignedItem` from `forecasts.py`.
- Produces: `compute_build_plan(db, org_id, item_id, build_quantity, yield_loss_pct) -> list[MrpPlanLine]`; `POST /mrp/build-plan` and `POST /mrp/build-plan/generate-pos`, consumed by Task 8 (frontend Build Calculator tab).

- [ ] **Step 1: Add `compute_build_plan` to `mrp.py`**

In `backend/app/services/mrp.py`, add after `_explode_and_net` (at the end of the file):

```python
async def compute_build_plan(
    db: AsyncSession,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    build_quantity: Decimal,
    yield_loss_pct: Decimal,
) -> list[MrpPlanLine]:
    """The build calculator: 'I'm about to build build_quantity of item_id -
    what do I need to buy'. Reuses _explode_and_net (the same engine
    compute_mrp_plan uses) seeded from a single ad hoc quantity instead of
    demand forecasts - so it nets against real on-hand/on-order/on-draft
    stock exactly like planning does, and each component's own recipe-level
    scrap_pct (see BomLine.scrap_pct / _load_bom_children) still applies.

    yield_loss_pct is a *different* scrap concept from BomLine.scrap_pct:
    it's the expected fraction of finished-good ATTEMPTS that fail entirely
    (QA rejects, scrapped builds), so it inflates the build quantity itself
    before exploding through the recipe - "I need build_quantity GOOD units,
    but only (1 - yield_loss_pct) of what I attempt will pass QA, so attempt
    build_quantity / (1 - yield_loss_pct/100)".

    No seed_due is passed - an ad hoc build has no forecast date, so every
    resulting line's need_by_date/suggested_order_date is None.
    """
    if build_quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "build_quantity must be positive")
    if yield_loss_pct < 0 or yield_loss_pct >= 100:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "yield_loss_pct must be at least 0 and less than 100"
        )
    has_active_bom = await db.scalar(
        select(Bom.id).where(Bom.org_id == org_id, Bom.item_id == item_id, Bom.status == "active")
    )
    if has_active_bom is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This item has no active BOM - nothing to build")

    attempted_qty = build_quantity / (1 - yield_loss_pct / 100)
    return await _explode_and_net(db, org_id, {item_id: attempted_qty}, None)
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_build_calculator.py`:

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


async def _create_item(client, token, sku, name="Item", min_order_qty=None):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "component",
            "unit_of_measure": "ea",
            "min_order_qty": str(min_order_qty) if min_order_qty is not None else None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_active_bom(client, token, produced_item, lines):
    headers = {"Authorization": f"Bearer {token}"}
    bom = await client.post("/boms", json={"item_id": produced_item["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {"component_item_id": comp["id"], "quantity": str(qty), "sequence": i}
                for i, (comp, qty) in enumerate(lines)
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post("/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    return res.json()


def _dec(v) -> Decimal:
    return Decimal(str(v))


async def test_build_plan_no_yield_loss(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG", "Widget")
    rod = await _create_item(client, token, "BLD-ROD", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 2)])

    res = await client.post(
        "/mrp/build-plan", json={"item_id": fg["id"], "build_quantity": "100"}, headers=headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["item_sku"] == "BLD-FG"
    lines_by_sku = {line["item_sku"]: line for line in body["lines"]}
    assert _dec(lines_by_sku["BLD-FG"]["net_requirement"]) == Decimal("100")
    assert _dec(lines_by_sku["BLD-ROD"]["net_requirement"]) == Decimal("200")


async def test_build_plan_applies_yield_loss(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG2", "Widget")
    rod = await _create_item(client, token, "BLD-ROD2", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 1)])

    # Need 95 good units, expect 5% of attempts to fail -> attempt 95/0.95 = 100.
    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": fg["id"], "build_quantity": "95", "yield_loss_pct": "5"},
        headers=headers,
    )
    assert res.status_code == 200
    lines_by_sku = {line["item_sku"]: line for line in res.json()["lines"]}
    assert _dec(lines_by_sku["BLD-FG2"]["net_requirement"]) == Decimal("100")
    assert _dec(lines_by_sku["BLD-ROD2"]["net_requirement"]) == Decimal("100")


async def test_build_plan_rejects_yield_loss_of_100(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG3", "Widget")
    rod = await _create_item(client, token, "BLD-ROD3", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 1)])

    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": fg["id"], "build_quantity": "10", "yield_loss_pct": "100"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_build_plan_rejects_item_with_no_active_bom(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    raw = await _create_item(client, token, "BLD-RAW", "Raw Material")

    res = await client.post(
        "/mrp/build-plan", json={"item_id": raw["id"], "build_quantity": "10"}, headers=headers
    )
    assert res.status_code == 400


async def test_build_plan_nets_against_on_hand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG4", "Widget")
    rod = await _create_item(client, token, "BLD-ROD4", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 2)])
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    plines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": rod["id"], "quantity_ordered": "50", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    assert plines.status_code == 200
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    res = await client.post(
        "/mrp/build-plan", json={"item_id": fg["id"], "build_quantity": "100"}, headers=headers
    )
    lines_by_sku = {line["item_sku"]: line for line in res.json()["lines"]}
    # 200 rods needed, 50 already on order -> net 150.
    assert _dec(lines_by_sku["BLD-ROD4"]["on_order"]) == Decimal("50")
    assert _dec(lines_by_sku["BLD-ROD4"]["net_requirement"]) == Decimal("150")


async def test_generate_pos_from_build_plan_groups_by_preferred_supplier(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG5", "Widget")
    rod = await _create_item(client, token, "BLD-ROD5", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 2)])
    supplier = await _create_supplier(client, token)

    set_supplier = await client.patch(
        f"/items/{rod['id']}", json={"preferred_supplier_id": supplier["id"]}, headers=headers
    )
    assert set_supplier.status_code == 200

    res = await client.post(
        "/mrp/build-plan/generate-pos",
        json={"item_id": fg["id"], "build_quantity": "100"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["line_count"] == 1
    assert body["unassigned"] == []

    pos = await client.get("/purchase-orders", headers=headers)
    assert any(po["po_number"] == body["created"][0]["po_number"] for po in pos.json())


async def test_generate_pos_from_build_plan_reports_unassigned(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG6", "Widget")
    rod = await _create_item(client, token, "BLD-ROD6", "Steel Rod")  # no preferred supplier
    await _create_active_bom(client, token, fg, [(rod, 2)])

    res = await client.post(
        "/mrp/build-plan/generate-pos",
        json={"item_id": fg["id"], "build_quantity": "100"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == []
    assert len(body["unassigned"]) == 1
    assert body["unassigned"][0]["item_sku"] == "BLD-ROD6"


async def test_build_plan_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    fg_a = await _create_item(client, token_a, "BLD-ISO", "Widget")

    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": fg_a["id"], "build_quantity": "10"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    # Org B can't see org A's item at all.
    assert res.status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_build_calculator.py -v` (from `backend/`)
Expected: FAIL - `404 Not Found` for `/mrp/build-plan` (routes don't exist yet).

- [ ] **Step 4: Extract the shared PO-grouping helper and add the endpoints**

In `backend/app/api/routes/forecasts.py`, add `compute_build_plan` to the import from `app.services.mrp` (line 21):

```python
from app.services.mrp import MrpPlanLine, compute_build_plan, compute_mrp_plan, resolve_horizon
```

Add these two request/response schemas after `GeneratePosResponse` (after line 148):

```python
class BuildPlanRequest(BaseModel):
    item_id: str
    build_quantity: Decimal
    yield_loss_pct: Decimal = Decimal(0)


class BuildPlanResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    build_quantity: Decimal
    yield_loss_pct: Decimal
    lines: list[MrpPlanLine]
```

Replace the `generate_pos` function (from `@mrp_router.post("/generate-pos"...)` through its closing `return GeneratePosResponse(created=created, unassigned=unassigned)`) with:

```python
async def _create_pos_grouped_by_supplier(
    db: AsyncSession,
    org_id: uuid.UUID,
    current: CurrentUser,
    orderable: list[MrpPlanLine],
    audit_action: str,
) -> GeneratePosResponse:
    """Shared by generate_pos (forecast-driven MRP plan) and
    generate_pos_from_build_plan (build-quantity-driven): group purchased
    shortfalls by preferred supplier and create one draft PO per supplier.
    Items with no preferred supplier can't be routed automatically and come
    back as `unassigned`. audit_action distinguishes the two call sites in
    the audit log.
    """
    if not orderable:
        return GeneratePosResponse(created=[], unassigned=[])

    item_uuids = {uuid.UUID(line.item_id) for line in orderable}
    preferred = {
        item_id: supplier_id
        for item_id, supplier_id in (
            await db.execute(
                select(Item.id, Item.preferred_supplier_id).where(
                    Item.org_id == org_id, Item.id.in_(item_uuids)
                )
            )
        ).all()
    }

    groups: dict[uuid.UUID, list[MrpPlanLine]] = defaultdict(list)
    unassigned: list[UnassignedItem] = []
    for line in orderable:
        supplier_id = preferred.get(uuid.UUID(line.item_id))
        if supplier_id is None:
            unassigned.append(
                UnassignedItem(
                    item_id=line.item_id,
                    item_sku=line.item_sku,
                    item_name=line.item_name,
                    net_requirement=line.net_requirement,
                )
            )
        else:
            groups[supplier_id].append(line)

    prices = await latest_unit_prices(db, org_id, item_uuids)
    suppliers = {
        s.id: s
        for s in (
            await db.execute(select(Supplier).where(Supplier.org_id == org_id, Supplier.id.in_(groups)))
        )
        .scalars()
        .all()
    }

    # Defensive: a preferred_supplier_id always references a live org supplier
    # (validated when set, FK with ondelete SET NULL on supplier deletion), so
    # every group key should resolve. But if a supplier row is somehow missing,
    # route its items to `unassigned` rather than KeyError into a 500.
    for supplier_id in [sid for sid in groups if sid not in suppliers]:
        for line in groups.pop(supplier_id):
            unassigned.append(
                UnassignedItem(
                    item_id=line.item_id,
                    item_sku=line.item_sku,
                    item_name=line.item_name,
                    net_requirement=line.net_requirement,
                )
            )

    created: list[GeneratedPoSummary] = []
    # Deterministic order (by supplier name) so po_number allocation is stable.
    for supplier_id in sorted(groups, key=lambda sid: suppliers[sid].name):
        group_lines = groups[supplier_id]
        po = await _new_draft_po(db, org_id, supplier_id, current.id)
        await _add_po_lines(db, org_id, po, group_lines, prices)
        created.append(
            GeneratedPoSummary(
                po_id=str(po.id),
                po_number=po.po_number,
                supplier_id=str(supplier_id),
                supplier_name=suppliers[supplier_id].name,
                line_count=len(group_lines),
            )
        )

    if created:
        await log_event(
            db,
            action=audit_action,
            org_id=org_id,
            actor_user_id=current.id,
            metadata={"po_count": len(created), "unassigned_count": len(unassigned)},
        )
    await db.commit()
    return GeneratePosResponse(created=created, unassigned=unassigned)


@mrp_router.post("/generate-pos", response_model=GeneratePosResponse)
async def generate_pos(
    body: GeneratePosRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Automatic PO generation: take every purchased shortfall in the plan,
    group it by the item's preferred supplier, and create one draft PO per
    supplier. Purchased shortfalls with no preferred supplier can't be routed,
    so they're returned as `unassigned` for the user to assign a supplier (or
    order manually via generate-po). Manufactured items are a build action and
    are never ordered. Like generate-po, the POs are drafts - review and submit
    them in the PO editor.
    """
    hs, he = resolve_horizon(body.horizon_start, body.horizon_end)
    org_id = current.org_id

    orderable = [
        line
        for line in await compute_mrp_plan(db, org_id, hs, he)
        if not line.is_manufactured and line.net_requirement > 0
    ]
    return await _create_pos_grouped_by_supplier(
        db, org_id, current, orderable, "po.auto_generated_from_plan"
    )


@mrp_router.post("/build-plan", response_model=BuildPlanResponse)
async def run_build_plan(
    body: BuildPlanRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read-only build calculator: given a quantity to build and an optional
    expected yield loss, explode the item's active BOM (netted against real
    stock) into what needs to be bought. See services.mrp.compute_build_plan.
    """
    item_uuid = _parse_item_id(body.item_id)
    item = await get_or_404(db, Item, current.org_id, item_uuid, "Item not found")
    lines = await compute_build_plan(
        db, current.org_id, item_uuid, body.build_quantity, body.yield_loss_pct
    )
    return BuildPlanResponse(
        item_id=str(item.id),
        item_sku=item.sku,
        item_name=item.name,
        build_quantity=body.build_quantity,
        yield_loss_pct=body.yield_loss_pct,
        lines=lines,
    )


@mrp_router.post("/build-plan/generate-pos", response_model=GeneratePosResponse)
async def generate_pos_from_build_plan(
    body: BuildPlanRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Commit a build calculation into draft POs, grouped by preferred
    supplier - the same grouping generate_pos uses. Recomputes the plan
    server-side from body rather than trusting any client-submitted
    quantities, matching generate_po/generate_pos.
    """
    item_uuid = _parse_item_id(body.item_id)
    await get_or_404(db, Item, current.org_id, item_uuid, "Item not found")
    lines = await compute_build_plan(
        db, current.org_id, item_uuid, body.build_quantity, body.yield_loss_pct
    )
    orderable = [line for line in lines if not line.is_manufactured and line.net_requirement > 0]
    return await _create_pos_grouped_by_supplier(
        db, current.org_id, current, orderable, "po.generated_from_build_plan"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_build_calculator.py -v` (from `backend/`)
Expected: PASS - all 8 tests.

- [ ] **Step 6: Run the full backend suite**

Run: `pytest -q` (from `backend/`)
Expected: PASS - confirms `generate_pos`'s refactor into `_create_pos_grouped_by_supplier` didn't change its behavior (covered by existing `test_generate_po.py`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/mrp.py backend/app/api/routes/forecasts.py backend/tests/test_build_calculator.py
git commit -m "Add build calculator: build-quantity-driven MRP explosion and PO generation"
```

---

### Task 6: Frontend - unit picker on BOM lines

**Files:**
- Modify: `frontend/src/pages/Boms.tsx`

**Interfaces:**
- Consumes: `POST/GET /items/{item_id}/units` (Task 1), `unit_id`/`unit_quantity`/`unit_code` on `BomLineInput`/`BomLineResponse` (Task 2).
- Produces: no new interfaces for other tasks - this is a leaf UI change.

- [ ] **Step 1: Add the `ItemUnit` type and a units query**

In `frontend/src/pages/Boms.tsx`, add after the `ItemResponse` interface (line 24):

```typescript
interface ItemUnitResponse {
  id: string;
  item_id: string;
  code: string;
  factor_to_base: string;
}
```

Update `BomLineResponse` (currently ends with `sequence: number;`):

```typescript
interface BomLineResponse {
  id: string;
  component_item_id: string;
  component_sku: string;
  component_name: string;
  unit_of_measure: string;
  quantity: string;
  sequence: number;
  scrap_pct: string;
  unit_id: string | null;
  unit_quantity: string | null;
  unit_code: string | null;
}
```

- [ ] **Step 2: Add unit state and a units query scoped to the selected component**

In the `Boms()` component, after the existing `lineScrapPct` state (added previously):

```typescript
  const [lineUnitId, setLineUnitId] = useState("");
  const [lineUnitQuantity, setLineUnitQuantity] = useState("");
  const [showNewUnit, setShowNewUnit] = useState(false);
  const [newUnitCode, setNewUnitCode] = useState("");
  const [newUnitFactor, setNewUnitFactor] = useState("");
```

After the existing `items` query, add:

```typescript
  const componentUnits = useQuery<ItemUnitResponse[]>({
    queryKey: ["item-units", lineComponentId],
    enabled: !!lineComponentId && lineComponentId !== "__new__",
    queryFn: async () => {
      const res = await apiFetch(`/items/${lineComponentId}/units`);
      if (!res.ok) throw new Error("Failed to load units");
      return res.json();
    },
  });

  const createUnit = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/items/${lineComponentId}/units`, {
        method: "POST",
        body: JSON.stringify({ code: newUnitCode, factor_to_base: newUnitFactor }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create unit");
      }
      return res.json() as Promise<ItemUnitResponse>;
    },
    onSuccess: (unit) => {
      queryClient.invalidateQueries({ queryKey: ["item-units", lineComponentId] });
      setLineUnitId(unit.id);
      setShowNewUnit(false);
      setNewUnitCode("");
      setNewUnitFactor("");
    },
    onError: (e: Error) => setError(e.message),
  });
```

Reset the unit selection whenever the component changes - add this right after `setLineComponentId` is defined as state (find `const [lineComponentId, setLineComponentId] = useState("");` and change the `ItemSelect`'s `onChange` in the add-line form in Step 4 below to also clear unit state; no separate effect needed since the form's `onChange` is the only place `lineComponentId` changes).

- [ ] **Step 3: Include unit fields when adding a line, and preserve them on every other line mutation**

In `addLine`'s `mutationFn`, the `existingLines` mapping and the merged line construction currently look like this (after Task-context edits from the scrap_pct feature):

```typescript
      const existingLines = bomDetail.data.lines.map((l) => ({
        component_item_id: l.component_item_id,
        quantity: l.quantity,
        scrap_pct: l.scrap_pct,
      }));
      const merged = [
        ...existingLines,
        { component_item_id: lineComponentId, quantity: lineQuantity, scrap_pct: lineScrapPct || "0" },
      ].map((l, i) => ({ ...l, sequence: i }));
```

Replace with:

```typescript
      const existingLines = bomDetail.data.lines.map((l) => ({
        component_item_id: l.component_item_id,
        quantity: l.quantity,
        scrap_pct: l.scrap_pct,
        unit_id: l.unit_id,
        unit_quantity: l.unit_quantity,
      }));
      const merged = [
        ...existingLines,
        {
          component_item_id: lineComponentId,
          quantity: lineUnitId ? "0" : lineQuantity,
          scrap_pct: lineScrapPct || "0",
          unit_id: lineUnitId || null,
          unit_quantity: lineUnitId ? lineUnitQuantity : null,
        },
      ].map((l, i) => ({ ...l, sequence: i }));
```

(`quantity: "0"` when a unit is selected is a placeholder the server ignores and recomputes from `unit_quantity` - see Task 2's `update_bom_lines`. Sending a real number there would be misleading since it's never used.)

Update `patchLines`'s type signature to match:

```typescript
  function patchLines(
    lines: {
      component_item_id: string;
      quantity: string;
      sequence: number;
      scrap_pct: string;
      unit_id: string | null;
      unit_quantity: string | null;
    }[],
  ) {
```

In `addLine`'s `onSuccess`, reset the new unit fields alongside the existing resets:

```typescript
    onSuccess: () => {
      setLineComponentId("");
      setLineQuantity("1");
      setLineScrapPct("0");
      setLineUnitId("");
      setLineUnitQuantity("");
      queryClient.invalidateQueries({ queryKey: ["boms", selectedBomId] });
    },
```

In `removeLine`'s `remaining` mapping, add the two unit fields (same shape as `existingLines` above):

```typescript
      const remaining = bomDetail.data.lines
        .filter((l) => l.id !== lineId)
        .map((l, i) => ({
          component_item_id: l.component_item_id,
          quantity: l.quantity,
          scrap_pct: l.scrap_pct,
          unit_id: l.unit_id,
          unit_quantity: l.unit_quantity,
          sequence: i,
        }));
```

In `reorderLines`'s `mutationFn`, add the two unit fields:

```typescript
      const res = await patchLines(
        lines.map((l, i) => ({
          component_item_id: l.component_item_id,
          quantity: l.quantity,
          scrap_pct: l.scrap_pct,
          unit_id: l.unit_id,
          unit_quantity: l.unit_quantity,
          sequence: i,
        })),
      );
```

In `bulkAddLines`'s `existingLines` mapping, add the two unit fields (bulk-pasted lines themselves stay base-unit only - `unit_id`/`unit_quantity` are `null` for freshly parsed rows, matching today's paste format which has no unit column):

```typescript
      const existingLines = bomDetail.data.lines.map((l) => ({
        component_item_id: l.component_item_id,
        quantity: l.quantity,
        scrap_pct: l.scrap_pct,
        unit_id: l.unit_id,
        unit_quantity: l.unit_quantity,
        sequence: l.sequence,
      }));
```

and the `parsed.push(...)` call in the same function needs the two new fields added as `null`:

```typescript
        parsed.push({
          component_item_id: item.id,
          quantity,
          scrap_pct: scrapPct,
          unit_id: null,
          unit_quantity: null,
          sequence: 0,
        });
```

(the `parsed` array's type annotation just above must gain `unit_id: string | null; unit_quantity: string | null;` too.)

- [ ] **Step 4: Add the unit dropdown, inline "+ New unit" form, and a Unit column**

In the add-line `<form>` (the one with `Field label="Component"`, `Field label="Quantity"`, `Field label="Scrap %"`), add a fourth field and widen the grid. Change:

```typescript
                <form
                  onSubmit={onAddLine}
                  className="grid gap-4 sm:grid-cols-[1fr_140px_140px_auto] sm:items-end"
                >
```

to:

```typescript
                <form
                  onSubmit={onAddLine}
                  className="grid gap-4 sm:grid-cols-[1fr_140px_140px_160px_auto] sm:items-end"
                >
```

and update the `ItemSelect`'s `onChange` to also clear unit selection when the component changes:

```typescript
                    <ItemSelect
                      id="component"
                      items={items.data}
                      value={lineComponentId}
                      onChange={(id) => {
                        setLineComponentId(id);
                        setLineUnitId("");
                        setLineUnitQuantity("");
                      }}
                      excludeId={bomDetail.data.item_id}
                      required
                    />
```

Add a new `Field` for the unit, between the `Scrap %` field and the `Add line` button:

```typescript
                  <Field label="Unit" htmlFor="line-unit">
                    {showNewUnit ? (
                      <div className="flex gap-1">
                        <Input
                          placeholder="code"
                          value={newUnitCode}
                          onChange={(e) => setNewUnitCode(e.target.value)}
                        />
                        <Input
                          type="number"
                          step="0.000001"
                          min="0.000001"
                          placeholder="factor"
                          value={newUnitFactor}
                          onChange={(e) => setNewUnitFactor(e.target.value)}
                        />
                        <Button
                          type="button"
                          size="icon"
                          variant="secondary"
                          disabled={!newUnitCode || !newUnitFactor}
                          loading={createUnit.isPending}
                          onClick={() => createUnit.mutate()}
                        >
                          <Layers className="size-4" />
                        </Button>
                      </div>
                    ) : (
                      <select
                        id="line-unit"
                        className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                        disabled={!lineComponentId}
                        value={lineUnitId}
                        onChange={(e) => {
                          if (e.target.value === "__new__") {
                            setShowNewUnit(true);
                            setLineUnitId("");
                          } else {
                            setLineUnitId(e.target.value);
                          }
                        }}
                      >
                        <option value="">base unit</option>
                        {componentUnits.data?.map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.code}
                          </option>
                        ))}
                        <option value="__new__">+ New unit...</option>
                      </select>
                    )}
                  </Field>
```

Update the `Quantity` field so it reads from `lineUnitQuantity` instead of `lineQuantity` when a unit is selected, and its label reflects that:

```typescript
                  <Field label={lineUnitId ? "Quantity (in unit)" : "Quantity"} htmlFor="qty">
                    <Input
                      id="qty"
                      type="number"
                      step="0.0001"
                      min="0.0001"
                      max={lineUnitId ? undefined : MAX_LINE_QUANTITY}
                      value={lineUnitId ? lineUnitQuantity : lineQuantity}
                      onChange={(e) =>
                        lineUnitId ? setLineUnitQuantity(e.target.value) : setLineQuantity(e.target.value)
                      }
                      required
                    />
                  </Field>
```

Add a "Unit" column to the lines table header and to `BomLineRow`. In the `<THead>`:

```typescript
                  <TR>
                    {bomDetail.data.status === "draft" && <TH />}
                    <TH>Component</TH>
                    <TH>Quantity</TH>
                    <TH>UoM</TH>
                    <TH>Unit</TH>
                    <TH>Scrap %</TH>
                    {bomDetail.data.status === "draft" && <TH />}
                  </TR>
```

In `BomLineRow`'s `<TR>`, add a cell between the UoM `<TD>` and the Scrap % `<TD>`:

```typescript
        <TD>{line.quantity}</TD>
        <TD className="text-muted-foreground">{line.unit_of_measure}</TD>
        <TD className="text-muted-foreground">
          {line.unit_code ? `${line.unit_quantity} ${line.unit_code}` : "-"}
        </TD>
        <TD className="text-muted-foreground">{Number(line.scrap_pct) > 0 ? `${line.scrap_pct}%` : "-"}</TD>
```

- [ ] **Step 5: Type-check**

Run: `npx tsc --noEmit -p .` (from `frontend/`)
Expected: no errors.

- [ ] **Step 6: Manual browser verification**

1. `preview_start` the `backend-dev` and `frontend` configs from `.claude/launch.json`.
2. Log in, open Bills of Materials, select a draft BOM.
3. Pick a component in the add-line form, open the Unit dropdown, choose "+ New unit...", enter `code=g`, `factor=0.001`, create it.
4. Confirm the dropdown now shows "g" selected; enter a Quantity (in unit) of `500`; click Add line.
5. Confirm the new row shows `500 g` in the Unit column and the Quantity column shows the base-unit figure (e.g. `0.5000` if the item's base unit is kg).
6. Add a second line without touching the unit dropdown (base unit) and confirm both rows persist correctly after a page refresh (proves `existingLines` round-trips `unit_id`/`unit_quantity` - the bug class fixed in Step 3).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/Boms.tsx
git commit -m "Add per-item unit conversion picker to the BOM line editor"
```

---

### Task 7: Frontend - "Edit" button on active recipes

**Files:**
- Modify: `frontend/src/pages/Boms.tsx`

**Interfaces:**
- Consumes: `POST /boms/{bom_id}/revise` (Task 3).
- Produces: none - leaf UI change.

- [ ] **Step 1: Add the revise mutation**

In `frontend/src/pages/Boms.tsx`, after the `activateBom` mutation, add:

```typescript
  const reviseBom = useMutation({
    mutationFn: async () => {
      if (!bomDetail.data) throw new Error("No BOM selected");
      const res = await apiFetch(`/boms/${bomDetail.data.id}/revise`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to revise BOM");
      }
      return res.json() as Promise<BomDetailResponse>;
    },
    onSuccess: (newDraft) => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["boms"] });
      setSelectedBomId(newDraft.id);
    },
    onError: (e: Error) => setError(e.message),
  });
```

- [ ] **Step 2: Add the "Edit" button next to Activate/Delete**

In the BOM detail `<CardHeader>`, the action buttons are currently gated on `bomDetail.data.status === "draft"`. Add an `"Edit"` button gated on `status === "active"` alongside it - find the closing `)}` after the existing draft-only action `<div>` and add immediately after it:

```typescript
            {bomDetail.data.status === "active" && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => reviseBom.mutate()}
                loading={reviseBom.isPending}
              >
                Edit (new version)
              </Button>
            )}
```

- [ ] **Step 3: Type-check**

Run: `npx tsc --noEmit -p .` (from `frontend/`)
Expected: no errors.

- [ ] **Step 4: Manual browser verification**

1. With the same preview servers from Task 6, activate a BOM.
2. On the now-active BOM, click "Edit (new version)".
3. Confirm a new draft (version + 1) is created, selected, and pre-populated with the active version's lines (including any unit/scrap% values from Task 6).
4. Edit a line, save, activate the new draft.
5. Confirm the previous version now shows status "Archived" in the version list and its lines are unchanged (open it and check).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Boms.tsx
git commit -m "Add one-click 'Edit (new version)' action for active recipes"
```

---

### Task 8: Frontend - Build Calculator tab

**Files:**
- Create: `frontend/src/pages/BuildCalculator.tsx`
- Modify: `frontend/src/pages/Procurement.tsx`

**Interfaces:**
- Consumes: `POST /mrp/build-plan` and `POST /mrp/build-plan/generate-pos` (Task 5).
- Produces: none - leaf UI change.

- [ ] **Step 1: Create the panel component**

Create `frontend/src/pages/BuildCalculator.tsx`, modeled on the existing `GeneratePosPanel` in `frontend/src/pages/GeneratePos.tsx`:

```typescript
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { ItemSelect } from "@/components/ItemSelect";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  item_type: string;
  unit_of_measure: string;
}

interface MrpPlanLine {
  item_id: string;
  item_sku: string;
  item_name: string;
  is_manufactured: boolean;
  on_hand: string;
  net_requirement: string;
  suggested_order_quantity: string;
}

interface BuildPlanResponse {
  item_id: string;
  item_sku: string;
  item_name: string;
  build_quantity: string;
  yield_loss_pct: string;
  lines: MrpPlanLine[];
}

interface GeneratePosResult {
  created: { po_id: string; po_number: number; supplier_name: string; line_count: number }[];
  unassigned: { item_id: string; item_sku: string; item_name: string; net_requirement: string }[];
}

export function BuildCalculatorPanel() {
  const [error, setError] = useState<string | null>(null);
  const [itemId, setItemId] = useState("");
  const [buildQuantity, setBuildQuantity] = useState("");
  const [yieldLossPct, setYieldLossPct] = useState("0");
  const [plan, setPlan] = useState<BuildPlanResponse | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<GeneratePosResult | null>(null);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const calculate = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/mrp/build-plan", {
        method: "POST",
        body: JSON.stringify({
          item_id: itemId,
          build_quantity: buildQuantity,
          yield_loss_pct: yieldLossPct || "0",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to calculate build plan");
      }
      return res.json() as Promise<BuildPlanResponse>;
    },
    onSuccess: (data) => {
      setPlan(data);
      setResult(null);
      setConfirming(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  const commit = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/mrp/build-plan/generate-pos", {
        method: "POST",
        body: JSON.stringify({
          item_id: itemId,
          build_quantity: buildQuantity,
          yield_loss_pct: yieldLossPct || "0",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create purchase orders");
      }
      return res.json() as Promise<GeneratePosResult>;
    },
    onSuccess: (data) => {
      setResult(data);
      setConfirming(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  const orderableLines = (plan?.lines ?? []).filter(
    (line) => !line.is_manufactured && Number(line.net_requirement) > 0,
  );

  function onCalculate() {
    setError(null);
    if (!itemId) {
      setError("Pick a product to build");
      return;
    }
    if (!buildQuantity || Number(buildQuantity) <= 0) {
      setError("Enter a build quantity greater than 0");
      return;
    }
    calculate.mutate();
  }

  return (
    <div className="space-y-8">
      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Build calculator</CardTitle>
          <CardDescription>
            Pick a manufactured item and how many you plan to build - this explodes its active recipe
            (with each component's own scrap %) into what you need to buy, netted against stock on hand.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3 lg:max-w-2xl">
            <Field label="Product" htmlFor="bc-item">
              <ItemSelect id="bc-item" items={items.data} value={itemId} onChange={setItemId} required />
            </Field>
            <Field label="Build quantity" htmlFor="bc-qty">
              <Input
                id="bc-qty"
                type="number"
                step="0.0001"
                min="0.0001"
                value={buildQuantity}
                onChange={(e) => setBuildQuantity(e.target.value)}
              />
            </Field>
            <Field label="Expected yield loss %" htmlFor="bc-yield">
              <Input
                id="bc-yield"
                type="number"
                step="0.01"
                min="0"
                max="99.99"
                value={yieldLossPct}
                onChange={(e) => setYieldLossPct(e.target.value)}
                title="Percent of build attempts expected to fail QA/scrap entirely - inflates the build quantity before exploding the recipe"
              />
            </Field>
          </div>
          <Button onClick={onCalculate} loading={calculate.isPending}>
            Calculate
          </Button>

          {plan && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Item</TH>
                    <TH>On hand</TH>
                    <TH>Net requirement</TH>
                    <TH>Suggested action</TH>
                  </TR>
                </THead>
                <TBody>
                  {plan.lines.map((line) => (
                    <TR key={line.item_id}>
                      <TD className="font-medium">
                        {line.item_sku} - {line.item_name}
                        {line.is_manufactured && (
                          <Badge variant="accent" className="ml-2">
                            make
                          </Badge>
                        )}
                      </TD>
                      <TD>{line.on_hand}</TD>
                      <TD className="font-semibold">{line.net_requirement}</TD>
                      <TD>
                        {!line.is_manufactured && Number(line.net_requirement) > 0 ? (
                          <Badge variant="info">Order {line.suggested_order_quantity}</Badge>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>

      {plan && orderableLines.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Generate purchase orders</CardTitle>
            <CardDescription>
              Creates one draft PO per preferred supplier from the shortfalls above. Nothing is created
              until you confirm below.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!confirming && !result && (
              <Button onClick={() => setConfirming(true)}>Review and generate purchase orders</Button>
            )}
            {confirming && (
              <div className="space-y-3 rounded-lg border border-border p-4">
                <p className="text-sm font-semibold">
                  About to order {orderableLines.length} item{orderableLines.length === 1 ? "" : "s"}:
                </p>
                <ul className="space-y-1 text-sm text-muted-foreground">
                  {orderableLines.map((line) => (
                    <li key={line.item_id}>
                      {line.item_sku} - {line.item_name}: {line.suggested_order_quantity}
                    </li>
                  ))}
                </ul>
                <div className="flex gap-2">
                  <Button onClick={() => commit.mutate()} loading={commit.isPending}>
                    Confirm &amp; create
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirming(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
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
                  <p className="text-sm text-muted-foreground">No POs created.</p>
                )}
                {result.unassigned.length > 0 && (
                  <Alert variant="warning">
                    No preferred supplier for: {result.unassigned.map((u) => u.item_sku).join(", ")}. Assign
                    one under Items, or order manually from the Generate from Plan tab.
                  </Alert>
                )}
                <p className="text-sm text-muted-foreground">
                  Find the new drafts under the Purchase Orders tab above.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire the new tab into Procurement**

In `frontend/src/pages/Procurement.tsx`, add the import:

```typescript
import { BuildCalculatorPanel } from "./BuildCalculator";
```

Add a tab entry to `TABS`:

```typescript
const TABS = [
  { value: "purchase-orders", label: "Purchase Orders" },
  { value: "suppliers", label: "Suppliers" },
  { value: "generate", label: "Generate from Plan" },
  { value: "build-calculator", label: "Build Calculator" },
] as const;
```

Render the panel:

```typescript
      {tab === "build-calculator" && <BuildCalculatorPanel />}
```

(add this line alongside the existing `{tab === "generate" && (...)}` block, after it closes).

- [ ] **Step 3: Type-check**

Run: `npx tsc --noEmit -p .` (from `frontend/`)
Expected: no errors.

- [ ] **Step 4: Manual browser verification**

1. With the preview servers running, create a manufactured item with an active BOM (or reuse one from earlier tasks) whose component has a preferred supplier set.
2. Open Procurement → Build Calculator tab.
3. Pick the product, enter a build quantity and a yield loss % (e.g. 10), click Calculate.
4. Confirm the material table shows a net requirement larger than `build_quantity × recipe quantity` (yield loss inflated it).
5. Click "Review and generate purchase orders" - confirm the review card lists items/quantities and nothing is created yet (check the Purchase Orders tab still shows the same count).
6. Click "Confirm & create" - confirm a new draft PO appears (check the Purchase Orders tab), and repeat with a component that has no preferred supplier to confirm it's reported under "unassigned" instead of silently dropped.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/BuildCalculator.tsx frontend/src/pages/Procurement.tsx
git commit -m "Add Build Calculator tab: build quantity -> reviewed -> draft purchase orders"
```
