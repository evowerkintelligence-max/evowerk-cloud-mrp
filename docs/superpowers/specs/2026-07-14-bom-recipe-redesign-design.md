# BOM/Recipe Redesign: Units, Editability, Build Calculator

Status: Approved for planning
Date: 2026-07-14

## Problem

The BOM (recipe) feature already supports per-line quantity and a `scrap_pct`
"failure leeway" per component (see `backend/app/models/bom.py`,
`backend/app/api/routes/boms.py`, `backend/app/services/mrp.py`), and MRP
explodes forecasted demand through active BOMs into component requirements.

Three gaps make it too limited for real-world recipe authoring and shop-floor
use:

1. A recipe line's quantity is always in the component item's own base
   `unit_of_measure` - there's no way to author a line as "500 g" when the
   item's base unit is "kg".
2. An active BOM is immutable by design (correct - it's what past production
   runs point at), but there is no way to start a new version from the
   current one. Revising a recipe today means re-entering every line from
   scratch in a fresh draft.
3. There's no direct "I'm about to build N of this product - what do I need
   to buy" tool. The only path to a purchase order is the forecast-driven MRP
   plan (`compute_mrp_plan`), which requires creating demand forecast rows
   first.

## Scope

All three are designed and implemented together (one spec, one plan) since
they all live on the BOM/Procurement pages and share the explosion engine.
Out of scope: general-purpose alternate units of measure on purchase orders
or inventory - units here are scoped to recipe (BOM line) authoring only.
Downstream consumers (MRP, PO lines, cost rollups) keep working in the
item's base unit, unchanged.

## Section A: Unit conversion

### Data model

New table `item_units` (org- and item-scoped, since "1 box" means a
different quantity for different items):

```
item_units
  id             uuid pk
  org_id         uuid fk orgs.id, cascade delete
  item_id        uuid fk items.id, cascade delete
  code           varchar(20)   -- e.g. "box", "g", "pallet"
  factor_to_base numeric(18,6) -- 1 of this unit = factor_to_base * item's base unit
  unique (item_id, code)
```

`BomLine` gets two new nullable columns:

```
unit_id        uuid fk item_units.id, nullable  -- null = base unit
unit_quantity  numeric(12,4), nullable          -- the number as typed, in `unit_id`'s unit
```

`BomLine.quantity` (existing column) remains the single source of truth, and
is **always stored in the item's base unit**. On every line save, the server
recomputes `quantity = unit_quantity * unit.factor_to_base` (or
`quantity = unit_quantity` when `unit_id` is null) - the client-submitted
`quantity` in a non-base unit is never trusted directly. This means MRP
explosion (`_load_bom_children` / `compute_mrp_plan`), cost rollups, and
every other consumer of `BomLine.quantity` need **zero changes** - units are
purely an authoring/display convenience layered on top of the existing base-
unit invariant.

### API

- `POST /items/{item_id}/units` - `{code, factor_to_base}`, owner-only.
  Rejects duplicate `code` for the item (409) and non-positive
  `factor_to_base` (400).
- `GET /items/{item_id}/units` - list, any authenticated role.
- `DELETE /items/{item_id}/units/{unit_id}` - owner-only. Rejects (409) if
  any `BomLine` (draft or active) references the unit - deleting it out from
  under a recipe would silently change what the recipe's numbers mean.

`BomLineInput`/`BomLineResponse` (in `boms.py`) gain `unit_id` (optional) and
`unit_quantity` (optional, required together with `unit_id` when non-null).
`update_bom_lines` resolves the unit (must belong to the line's
`component_item_id` and the caller's org), computes `quantity` server-side,
and stores all three fields.

### UI

Each BOM line's quantity input gets a unit dropdown next to it, scoped to
the selected component item, defaulting to "base unit". The dropdown
includes a "+ New unit..." inline option (same inline-create pattern already
used for "+ New item..." in `Boms.tsx`) that opens a small `code` +
`factor_to_base` form, scoped to the currently selected component.

## Section B: Editable recipe (streamlined re-version)

Versioning itself is correct and stays: an active BOM is what a past
production run actually used, so it must stay immutable and auditable. The
actual friction is that revising one means starting a new draft from
scratch - there is no "copy the current active lines into a new draft" path
today.

### API

`POST /boms/{bom_id}/revise` (owner-only). `bom_id` must reference a `bom`
with `status == "active"`. Creates a new draft `Bom` for the same
`item_id` at `version = max + 1` (same allocation-race guard as
`create_bom`), and copies every current line - `component_item_id`,
`quantity`, `sequence`, `scrap_pct`, `unit_id`, `unit_quantity` - into the
new draft. Returns the new draft's `BomDetailResponse`. Logs
`bom.revised` via the existing audit `log_event` helper, metadata
`{source_bom_id, new_bom_id, version}`.

### UI

On an active BOM's detail card, the header actions gain an "Edit" button
alongside the existing (draft-only) Delete/Activate ones. Clicking it calls
`/revise`, then selects the returned draft - the user lands in the same line
editor already used for drafts (add/remove/reorder/paste-from-spreadsheet),
pre-populated instead of empty. "Activate" on the new draft works exactly as
it does today, superseding the old version (which stays archived).

No changes to activation, the cycle-check, or the one-active-per-item
invariant - `revise` only automates cloning lines into a fresh draft.

## Section C: Build calculator

### Backend

Refactor `compute_mrp_plan` (`backend/app/services/mrp.py`) to extract its
core loop - topological explosion over the active-BOM graph, netting each
node against on-hand/on-order/on-draft - into a shared internal helper that
takes a seed gross-demand dict (and optional seed need-by dates) instead of
reading `DemandForecast` rows directly:

```
async def _explode_and_net(
    db, org_id, seed_gross: dict[UUID, Decimal], seed_due: dict[UUID, date] | None
) -> list[MrpPlanLine]
```

`compute_mrp_plan` becomes: build `seed_gross`/`seed_due` from
`DemandForecast` rows in the horizon (as today), then call
`_explode_and_net`. This is a pure refactor - `compute_mrp_plan`'s external
behavior and all existing tests are unchanged.

New `compute_build_plan(db, org_id, item_id, build_quantity, yield_loss_pct)`:
- Rejects (400) if `item_id` has no active BOM (nothing to build) or if
  `yield_loss_pct` is outside `[0, 100)` (100 is undefined - infinite
  attempted quantity).
- `attempted_qty = build_quantity / (1 - yield_loss_pct / 100)` (equal to
  `build_quantity` when `yield_loss_pct` is 0).
- Seeds `seed_gross = {item_id: attempted_qty}`, calls `_explode_and_net`.
  Every line's `net_requirement` therefore already reflects: build-quantity
  yield loss (this step) compounded with each component's own recipe-level
  `scrap_pct` (already folded into `_load_bom_children`'s effective
  quantity-per, per the existing feature). No `need_by_date`/
  `suggested_order_date` - there's no forecast date in an ad hoc build.

Endpoints (added to `forecasts.py`'s existing `mrp_router`, alongside
`run_mrp_plan`/`generate_po`/`generate_pos`):
- `POST /mrp/build-plan` - `{item_id, build_quantity, yield_loss_pct}`, any
  authenticated role (read-only). Returns `list[MrpPlanLine]` for on-screen
  review.
- `POST /mrp/build-plan/generate-pos` - same request body, owner-only.
  **Recomputes `compute_build_plan` server-side** - never trusts
  client-submitted quantities, matching the existing `generate_po`/
  `generate_pos` pattern. Reuses the existing supplier-grouping + draft-PO-
  creation logic (factored out of `generate_pos` into a shared helper
  parameterized by a list of orderable `MrpPlanLine`s, so both endpoints
  call the same PO-creation code). Logs `po.generated_from_build_plan`.
  Returns the same shape as `generate_pos` today: `{created: [...],
  unassigned: [...]}`.

### Frontend

New "Build Calculator" tab in Procurement (alongside the existing Purchase
Orders / Suppliers / Generate from Plan tabs):

1. Pick a manufactured item (one with an active BOM - non-manufactured
   items aren't offered), enter build quantity and yield loss %.
2. **Calculate** (`POST /mrp/build-plan`) - shows the material table: item,
   on-hand, net requirement, unit, assigned supplier (or "unassigned").
3. **Review & confirm** - "Generate purchase orders" does not commit
   immediately. It shows a confirmation summary itemized *by the PO it's
   about to create*: one card per supplier with its line items, quantities,
   and a dollar total (using the same `latest_unit_prices` lookup
   `generate_po` already uses), plus unassigned items called out
   separately. Only an explicit "Confirm & create" click calls
   `POST /mrp/build-plan/generate-pos`.

## Error handling

- Unit conversion: duplicate unit code (409), non-positive factor (400),
  deleting an in-use unit (409), unit not belonging to the line's component
  item (400).
- Revise: reject if `bom_id` isn't active (409, matches existing "only
  draft BOMs can be edited" style messaging for the inverse case).
- Build calculator: no active BOM for `item_id` (400), `yield_loss_pct` out
  of `[0, 100)` (400), non-positive `build_quantity` (400). Commit endpoint
  re-validates everything server-side regardless of what the review screen
  showed.

## Testing

- `test_bom.py`: item unit CRUD (create/list/delete, duplicate code
  rejected, in-use delete rejected), BOM line save with a non-base unit
  converts and stores `quantity` correctly, `revise` copies lines
  (including `scrap_pct`/`unit_id`) into a new draft and rejects a
  non-active source.
- `test_mrp_explosion.py`: add a case asserting `_explode_and_net`
  refactor doesn't change `compute_mrp_plan` behavior (existing tests
  already cover this if they keep passing unchanged).
- New `test_build_calculator.py`: yield-loss math (0% and >0% cases),
  netting against existing on-hand/on-order, no-active-BOM rejection,
  `yield_loss_pct` boundary (100 rejected), generate-pos-from-build-plan
  creates correctly-grouped draft POs, cross-tenant isolation.
