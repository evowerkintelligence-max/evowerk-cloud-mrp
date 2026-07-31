# MRP-wide cohesion pass

Date: 2026-07-31
Status: approved

## Context

`docs/superpowers/specs/2026-07-31-mrp-inventory-analytics-roadmap.md` named
this "Phase 8," sequenced last after Phases 1–7 (Inventory Analytics UI,
lot/freshness, valuation, multi-UOM, GST, barcode, materialized analytics)
finished adding pages. In practice, Phase 1 (Inventory Analytics UI) shipped
and was then fully reverted the same day (`85ea66d`, "remove inventory
analysis features"), before any later phase started. Phases 1.5–7 never
happened.

That means Phase 8's original brief is partly stale: the `INVENTORY_STATUS`
color clash (`dead_stock` vs `low` both amber) and the "stockout" vs
"out of stock" terminology question it named no longer apply — that code
was deleted in the revert. This spec re-scopes the cohesion pass to what
actually exists in the codebase today, verified by reading the current
source rather than trusting the 5-day-old roadmap doc.

## What was verified

- `frontend/src/lib/status.ts` holds `poStatusVariant`, `soStatusVariant`,
  `woStatusVariant`, `itemTypeVariant`, `humanize`. `PurchaseOrders.tsx` and
  `production/JobsPanel.tsx` already import and use these correctly.
- `Production.tsx:78` (`statusVariant`, for work centers/machines/operators)
  and `Boms.tsx:78` (`bomStatusVariant`) are **identical logic**: active →
  success, archived/retired → neutral, else (draft/inactive) → warning.
  Genuine duplication that can silently drift.
- `Approvals.tsx:19` (`approvalStatusVariant`: approved/rejected/pending) and
  `Lots.tsx:71` (`lotStatusVariant`: on_hold/depleted/active) are different
  domains but the same repeated pattern of a hand-rolled 3-way variant
  switch living outside `lib/status.ts`.
- No "stockout"/"out of stock"/"dead stock" terminology exists anywhere in
  `frontend/src/pages` today — that language left with the Phase 1 revert.
- Existing cross-links: `Planning.tsx` → `/production` (jobs needing
  attention) and → `/warehouse?item=` (per product); `Inventory.tsx` →
  `/planning`; `ToBuyPanel.tsx` → `/procurement` and → `/items`.
  `production/JobsPanel.tsx` (Work Orders) and `sales/OrdersPanel.tsx`
  (Sales Orders) link to nothing outside their own page.
- PO number formatting is inconsistent: `PurchaseOrders.tsx` renders
  `PO-{po_number}` (matching `WO-{wo_number}` / `SO-{so_number}` everywhere
  else), but `ToBuyPanel.tsx:103` and `BuildCalculator.tsx:312` render the
  same freshly-created PO as `PO #{po_number}`.
- `PurchaseOrders.tsx` fetches `expected_delivery_date` but never displays
  it or checks it against today. `production/JobsPanel.tsx` already has an
  `isLate()` check (scheduled + `scheduled_start` in the past → red "Late"
  badge) with no PO equivalent, so a supplier running late is currently
  invisible anywhere in the UI.

## Scope

Four independent, small changes. Each is reversible on its own and none
depends on another.

### 1. Status-badge consolidation

Add to `frontend/src/lib/status.ts`:

```ts
const LIFECYCLE_STATUS: Record<string, Variant> = {
  active: "success",
  archived: "neutral",
  retired: "neutral",
};

export function lifecycleStatusVariant(status: string): Variant {
  return LIFECYCLE_STATUS[status] ?? "warning"; // draft / inactive
}

export function approvalStatusVariant(status: string): Variant {
  if (status === "approved") return "success";
  if (status === "rejected") return "danger";
  return "warning"; // pending
}

export function lotStatusVariant(status: string): Variant {
  if (status === "on_hold") return "warning";
  if (status === "depleted") return "neutral";
  return "success"; // active
}
```

- `Production.tsx` deletes its local `statusVariant` and imports
  `lifecycleStatusVariant` from `@/lib/status` in its place (work center,
  machine, and operator status badges all use it).
- `Boms.tsx` deletes its local `bomStatusVariant`, imports
  `lifecycleStatusVariant` in its place.
- `Approvals.tsx` deletes its local `approvalStatusVariant`, imports the
  moved version.
- `Lots.tsx` deletes its local `lotStatusVariant`, imports the moved
  version.
- No behavior change anywhere — same status strings map to the same
  colors. Any existing render tests that check badge variants/classes
  should still pass unmodified.

### 2. One missing cross-link

`production/JobsPanel.tsx`'s `JobRow` gets the same "View in Inventory"
link `Planning.tsx` already uses, since a Work Order has exactly one
`item_id`:

```tsx
<Link to={`/warehouse?item=${job.item_id}`} className="text-xs underline text-muted-foreground">
  View in Inventory
</Link>
```

Placed under the item name/SKU cell, matching `Planning.tsx`'s existing
placement pattern.

Sales Orders and Purchase Orders are explicitly **not** getting a new
cross-link: both are multi-line per order, so there is no single natural
link target, and a link on every line would be clutter rather than
cohesion.

### 3. Terminology fix

`ToBuyPanel.tsx:103` and `BuildCalculator.tsx:312` change
`Draft PO #{po.po_number}` to `Draft PO-{po.po_number}`, matching the
hyphen form used by `PurchaseOrders.tsx` itself and by every `WO-`/`SO-`
reference app-wide.

### 4. Small functional upgrade: PO lateness

`PurchaseOrders.tsx` adds an `isLate` check mirroring
`production/JobsPanel.tsx:22`'s existing one, same string-comparison idiom
(`expected_delivery_date` is already an ISO `YYYY-MM-DD` string, same shape
`scheduled_start` is compared against):

```ts
function isLate(po: PurchaseOrderSummary): boolean {
  if (po.status !== "submitted" && po.status !== "partially_received") return false;
  if (po.expected_delivery_date === null) return false;
  return po.expected_delivery_date < new Date().toISOString().slice(0, 10);
}
```

A red `Late` badge renders next to the status badge on both the PO list
row and the PO detail header, exactly where `JobsPanel.tsx` places its own
`Late` badge next to `woStatusVariant`. Frontend-only: `expected_delivery_date`
is already fetched, no backend or migration change needed.

## Out of scope

- Phases 1.5–7 of the original roadmap (lot/freshness, valuation, multi-UOM,
  GST, barcode, materialized analytics) — not resumed by this pass.
- Any backend traceability field linking a PO/WO back to the Planning row
  that generated it — no such field exists today and adding one is a schema
  change, not a cohesion fix.
- Sales Order / Purchase Order cross-links (see §2 rationale).

## Testing

- Existing render tests for `Production.tsx`, `Boms.tsx`, `Approvals.tsx`,
  `Lots.tsx` must still pass after the import swap (behavior-preserving).
- New/updated render-test coverage for: `JobsPanel.tsx`'s new Inventory
  link, `PurchaseOrders.tsx`'s new Late badge (one case late, one case not
  late from an expected date in the future).
