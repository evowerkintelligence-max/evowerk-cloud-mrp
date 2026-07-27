# Planning Grid: the one-screen MRP overhaul (Phase 1)

Date: 2026-07-26
Status: approved design, pending implementation plan
Supersedes: the three-tab Action Center design previously in this file.

## Context

The MRP feature works but is smeared across many screens (forecast CRUD,
plan table, generate-POs tab, build calculator) that each expose engine
concepts (horizons, gross/net, MOQ) directly. Target users are non-technical
manufacturers who will not learn that vocabulary. The user rejected a
tabbed reorganization: the requirement is one all-in-one screen in the
style of Katana, where a color-coded grid IS the to-do list and the only
input is a quantity.

The planning engine (`backend/app/services/mrp.py`) is sound and unchanged.
This overhaul is about collapsing the interface to it.

## Goals

- One screen. A non-technical user needs zero training: type how many you
  plan to make, the colors tell you what is wrong, one button fixes it.
- Kill the concept surface: no horizons, no forecast pages, no gross/net
  tables, no generate tab in the primary flow.
- Katana-style visual language: spreadsheet-like grid, colored availability
  chips (green in stock, amber arriving, red missing), urgent rows first.

## Non-goals

- No sales orders or work orders (Phases 2 and 3).
- No engine changes, no dismiss/snooze persistence.
- Build Calculator stays in Procurement unchanged; the only Procurement
  change is removing the "Generate from Plan" tab (section 3).

## 1. The screen

`/planning` renders a single grid. No tabs. One row per **product**: an
item that has an active BOM and is not a component of any other active BOM,
plus any item carrying a forecast in the window (covers purchased items
that are sold directly). Sub-assemblies never get rows; the engine handles
them invisibly.

Columns:

| Column | Content |
| --- | --- |
| Product | name + SKU |
| Planned | editable quantity, plus an editable needed-by date |
| In stock | current on-hand for the product |
| Materials | chip: green "All in stock", amber "Arriving", red "N missing" |
| Ready by | computed date the plan is achievable; red when later than needed-by |
| (detail) | row expands to the full requirement breakdown |

Above the grid: a one-line summary ("Red means materials are missing") and
one primary button, **"Order all missing materials"**. Below the grid: a
**"To buy"** panel listing every missing or soon-needed material with
quantity, supplier, and order-by date, red/amber dot per urgency.

Row order: red products first, then amber, then green; ties by ready-by
date ascending.

### The one input

"Planned" is pre-filled from consumption history (the existing
suggest-from-history engine) so most visits require typing nothing.
Editing the quantity writes the forecast behind the scenes; there is no
forecast page. The needed-by date defaults to 14 days out when a user
types a quantity without picking a date; history-derived pre-fills keep
their own dates. Setting Planned to 0 clears the item's demand in the
window.

The planning window is fixed at today + 90 days (the engine default) and
never shown to the user.

### The one button

"Order all missing materials" calls the existing `/mrp/generate-pos`
endpoint: draft POs grouped by each item's preferred supplier, quantities
recomputed server-side. The inline result lists the created drafts
(linking to Procurement) and any items with no preferred supplier, with a
nudge to assign one under Items. Owner-only (existing endpoint
requirement); the button hides for other roles. Drafting POs turns red
chips amber on the next refresh because `on_draft` offsets net
requirements in the engine, which also makes a second click safe.

### Row expansion (the survivor of the power views)

Clicking a row expands it in place to show the current plan-table detail
for that product and its components: gross/net, on hand, on order, MOQ
rounding, dates. The Demand and Plan pages die; this expansion is the only
place engine internals remain visible. CSV forecast import shrinks to a
small "Import" button in the grid header (existing import endpoint,
unchanged).

### Empty state

No products with BOMs and no forecasts: "Add a bill of materials to start
planning", linking to BOMs. All green: the button disables and the summary
reads "Everything is covered."

## 2. Backend

One new read endpoint and one new write endpoint in a new route module
(`backend/app/api/routes/planning_grid.py`). The grid's per-product
material status and ready-by need the BOM graph and the org-wide plan
joined server-side; doing that client-side would re-implement the engine.

**`GET /planning/grid`** (any member) returns:

- `products`: item id/SKU/name, `planned_qty` and `needed_by` (from
  forecasts in the window), `on_hand`, `materials_status`
  (`in_stock` | `arriving` | `missing`), `missing_materials` (SKUs),
  `ready_by`, and the expansion detail (that product's plan lines).
- `to_buy`: per purchasable shortfall item: quantity (MOQ-rounded, from
  the plan's `suggested_order_quantity`), preferred supplier name or null,
  `order_by` date, urgency tier.
- `summary`: red/amber/green counts.

Derivations, computed from one `compute_mrp_plan` run plus the active-BOM
graph:

- `materials_status`: over the components reachable from the product in
  the active-BOM graph: red if any has `net_requirement > 0`, else amber
  if any has open on-order/on-draft supply covering it, else green.
- `ready_by`: the latest constraint among reachable components: in stock
  counts as today; on order counts as today + that item's lead time
  (POs do not track promised dates yet; note as a known approximation);
  missing counts as today + lead time assuming it is ordered today.
  Products with everything in stock show "Now".

**`PUT /planning/grid/{item_id}`** (any member) with
`{quantity, needed_by}`: atomically replaces the item's forecasts inside
the window with a single row at `needed_by` (source `planning_grid`), or
deletes them when quantity is 0. Server-side replace avoids the
delete-then-insert race a client-composed version would have.

Existing endpoints reused unchanged: `/mrp/generate-pos` (order button),
`/forecasts/import` (CSV), `/forecasts/suggested` (pre-fill on first load
for products with no forecasts, accepted via the existing accept
endpoint).

## 3. What is removed or redirected

- `Planning.tsx` (forecast form, forecast table, plan table) is replaced
  by the grid. The `pages/planning/` suggest-from-history card and CSV
  section fold into the grid's pre-fill and Import button.
- Procurement loses its "Generate from Plan" tab; PO review stays.
  Build Calculator stays in Procurement, unchanged.
- Dashboard Morning Brief rows retarget from `/warehouse` to `/planning`.
- Nav: label stays "Planning"; keywords gain "what to do", "shortages",
  "materials".

## 4. Testing

- Backend: tests for `GET /planning/grid` (product-row selection rule,
  the three material statuses, ready-by derivation, to-buy contents) and
  `PUT /planning/grid/{item_id}` (replace-in-window semantics, zero
  clears, validation).
- Frontend: render test for the grid (chips, summary, to-buy panel, empty
  states) following `ProductionSetup.render.test.tsx`; a unit test for
  row sorting if it stays client-side.
- Existing forecast/MRP/PO tests must keep passing untouched: the engine
  and the reused endpoints do not change.
