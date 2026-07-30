# Inventory Analytics UI (Phase 1 of the Inventory Analytics roadmap)

Date: 2026-07-31
Status: approved design, pending implementation plan
Part of: [[2026-07-31-mrp-inventory-analytics-roadmap]]

## Context

`backend/app/services/inventory_analytics.py` computes real, tested
per-item analytics — safety stock, reorder point, days-of-supply, ABC
(Pareto value) and XYZ (demand-variability) classification, and a status
flag (stockout/low/dead_stock/excess/slow_moving/healthy). `services/rca.py`
runs a chi-square test of independence to explain *why* items are
stocking out (by supplier/item_type/abc_class). `services/scenario.py`
answers "what if this item's lead time changed" without persisting
anything. All three are exposed via `backend/app/api/routes/inventory_analytics.py`
and covered by tests.

None of this is visible in the product. `frontend/src/pages/Inventory.tsx`
only renders on-hand quantities, a status badge, and a bare reorder-point
number on the detail view. `abc_class`, `xyz_class`, the RCA endpoint, and
the scenario endpoint have zero frontend callers today (only
`/inventory/morning-brief` is wired into `Dashboard.tsx`).

This phase wires the existing engine into the existing Inventory page,
following the same philosophy the Planning Grid established on
2026-07-26: hide engine vocabulary behind plain language and one level of
expansion, minimize new screens/nav, minimize required input.

## Goals

- Surface ABC/XYZ classification, RCA (why items are low), and
  lead-time scenario compare — today invisible — inside the existing
  Inventory page.
- Default view uses plain language only (no "Class A", no "chi²=14.2,
  p<0.01"); real numbers are one click away via the existing row-expansion
  pattern.
- No new nav item, no new page, no mandatory manual input.

## Non-goals

- No new backend statistical logic — ABC/XYZ/RCA/scenario computations
  are unchanged. This phase adds one small orchestration endpoint (see
  Backend) but no new math.
- No $ valuation, aging buckets, or turnover ratio (Phase 2 — those need
  new backend calculations, not just a new screen).
- No shelf-life/freshness/recall/cold-chain changes (Phase 1.5).
- No changes to Planning Grid, Sales Orders, or Work Orders.

## 1. Layout: what changes in `Inventory.tsx`

No new page, no new nav item. Three additions to the existing screen:

- **Insight banner** (new, top of page, above the existing stat tiles):
  auto-surfaced RCA finding, plain language, dismissible.
- **Main table**: structure unchanged. No new columns in the default
  view — ABC/XYZ classes affect tie-breaking in row order (see §3) but
  are not shown as letters anywhere by default.
- **Row expansion** (the existing detail card that opens on row click):
  gains a "Why this status" line and a "What if lead time were…" field.

## 2. The insight banner (RCA, auto-surfaced)

New backend endpoint: `GET /inventory/rca/auto`. It runs `compute_rca`
across all three dimensions (supplier, item_type, abc_class) server-side
and returns the single most statistically significant finding (highest
chi² among results where `is_significant=True`), or an empty result if
none clear the bar. This is orchestration only — it calls the existing
`compute_rca` three times and picks a winner; no new statistical logic.
Keeping the comparison server-side matches the existing project rule
(seen in `services/planning_grid.py`) that engine-adjacent logic doesn't
get re-implemented client-side.

Banner copy is templated from the winning result's top anomalous value:

> Items from **Acme Supplier** run low 3.2× more often than your other
> stock. [See why] [Check a different cause]

- **"See why"** expands the full RCA table inline (values, observed vs.
  expected counts, chi², p-value) — same expand interaction as the
  Planning Grid's row detail.
- **"Check a different cause"** is the one manual control: reveals a
  3-way dimension picker (supplier / item type / value class) that
  queries the existing `GET /inventory/rca?dimension=` directly.

If no dimension is significant, or there isn't enough low/stockout data
yet, the banner does not render.

## 3. ABC/XYZ classes: plain-language translation

Never shown as bare letters in the default view.

- ABC (value): A → "High-value item", B → "Mid-value item", C →
  "Lower-value item"
- XYZ (demand variability): X → "Steady demand", Y → "Somewhat
  variable", Z → "Erratic demand"

These strings appear only in row expansion (§4). The one place
classification affects the default view: among rows tied on urgency
(same status), Class A (high-value) items sort first — a stockout on a
high-value item matters more than one on a low-value item.

## 4. Row expansion: "Why this status" + "What if…"

Expanding a row adds two things beneath the existing location/transaction
detail:

- **Why this status**: one templated sentence built from the existing
  `/inventory/analytics` line, e.g. *"12 days of stock left, below your
  usual 21-day reorder point. High-value item, steady demand."* A "show
  the numbers" toggle reveals the raw `reorder_point`, `safety_stock`,
  `days_of_supply`, `abc_class`, `xyz_class` values — already returned by
  the existing endpoint and simply unused today.
- **What if lead time were ⟨N⟩ days?**: a single number input. On
  change, calls the existing `POST /inventory/scenario-compare` for that
  one item and live-updates a one-line delta, e.g. *"Reorder point would
  drop from 120 to 95 units; status would change from Low to Healthy."*
  No modal, no separate page.

## 5. Backend changes

One new endpoint, no new statistical logic:

**`GET /inventory/rca/auto`** (any member) — runs `compute_rca` for
`supplier`, `item_type`, and `abc_class`, returns the result with the
highest `chi2_statistic` among those with `is_significant=True` and at
least one value, or a `method: "no_significant_dimension"` empty result
if none qualify. Same response shape as the existing `RcaResponse` plus
a `dimension` field indicating which one won.

Everything else reuses existing, unchanged endpoints: `/inventory/analytics`,
`/inventory/rca` (manual dimension picker), `/inventory/scenario-compare`.

## 6. Cross-links with the Planning Grid

The Planning Grid (`/planning`, already shipped) and this Inventory
Analytics UI (`/inventory`) stay two separate screens with two separate
jobs — forward-looking "what to make/buy" vs. diagnostic "what's wrong
with current stock" — but they're one connected system, not two
unrelated features, and should read that way:

- **Planning Grid → Inventory Analytics**: each product row gets a small
  link (e.g. an info icon next to the SKU) to that item's row on
  `/inventory`, auto-expanded to its analytics detail (§4). Lets a user
  planning a build check *why* a material looks risky without leaving
  the planning flow to go hunting.
- **Inventory Analytics → Planning Grid**: the RCA insight banner (§2)
  gets a "View affected items in Planning" link that navigates to
  `/planning` filtered/highlighted to the SKUs behind the finding — e.g.
  clicking through on "Acme Supplier" highlights every product row whose
  materials depend on that supplier.

No new backend endpoints for this — both are client-side navigation with
an `item_id`/`skus` query param the existing pages read; the Planning
Grid and Inventory pages already fetch by item id so this is routing,
not new data.

## 7. Testing

- Backend: new test file for `GET /inventory/rca/auto` — picks the
  highest-chi² significant dimension, returns the no-significant-dimension
  case correctly, ties broken deterministically (by dimension name order).
- Frontend: extend the Inventory page's render test (create one following
  the `ProductionSetup.render.test.tsx` pattern if one doesn't already
  exist) for: banner render/dismiss, "check a different cause" dimension
  switch, row-expansion "why" line and numbers toggle, "what if" field and
  its live delta.
- No changes to existing inventory/MRP/RCA/scenario backend tests —
  everything reused is unchanged; only the new `/rca/auto` orchestration
  is new surface area.
