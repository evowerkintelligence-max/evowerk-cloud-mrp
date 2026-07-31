# MRP-wide Cohesion Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate duplicated status-badge logic into one shared module, add the one missing cross-link the MRP suite is genuinely missing, fix a small PO-numbering terminology inconsistency, and add a "Late" badge to overdue Purchase Orders.

**Architecture:** Pure frontend change, no backend/migration work. All four pieces are independent edits to existing React/TypeScript files under `frontend/src`, each behavior-preserving except the two additive pieces (JobsPanel Inventory link, PO Late badge).

**Tech Stack:** React + TypeScript, TanStack Query, react-router-dom, Vitest + `react-dom/server`'s `renderToString` for render tests (no jsdom/RTL in this repo's existing tests — follow that pattern).

## Global Constraints

- No new dependencies. No backend or database changes — every task is `frontend/src` only.
- Match the existing "as const" / `Variant` typing style already in `frontend/src/lib/status.ts`.
- Existing render tests for `Production.tsx`, `Boms.tsx` (none exist today), `Approvals.tsx`, `Lots.tsx` (none exist today) must keep passing — the consolidation tasks are behavior-preserving (same status string → same color).
- Test runner: `npm test` (vitest run) from `frontend/`. Render tests use `renderToString` + a seeded `QueryClient`, per `frontend/src/pages/production/JobsPanel.render.test.tsx` and `frontend/src/pages/Production.render.test.tsx`.
- Spec: `docs/superpowers/specs/2026-07-31-mrp-cohesion-pass-design.md`.

---

## File Structure

- **Modify** `frontend/src/lib/status.ts` — add `lifecycleStatusVariant`, `approvalStatusVariant`, `lotStatusVariant`.
- **Create** `frontend/src/lib/status.test.ts` — unit tests for the three new functions.
- **Modify** `frontend/src/pages/Production.tsx` — delete local `statusVariant`, use `lifecycleStatusVariant` at its 4 call sites.
- **Modify** `frontend/src/pages/Boms.tsx` — delete local `bomStatusVariant`, use `lifecycleStatusVariant` at its 2 call sites.
- **Modify** `frontend/src/pages/Approvals.tsx` — delete local `approvalStatusVariant`, use the shared one at its 1 call site.
- **Modify** `frontend/src/pages/Lots.tsx` — delete local `lotStatusVariant`, use the shared one at its 2 call sites.
- **Modify** `frontend/src/pages/production/JobsPanel.tsx` — add a "View in Inventory" link per job row.
- **Modify** `frontend/src/pages/production/JobsPanel.render.test.tsx` — add a test for the new link.
- **Modify** `frontend/src/pages/planning/ToBuyPanel.tsx` and `frontend/src/pages/BuildCalculator.tsx` — `PO #{n}` → `PO-{n}`.
- **Modify** `frontend/src/pages/PurchaseOrders.tsx` — add `isLate` check and a `Late` badge on the list row and detail header.
- **Create** `frontend/src/pages/PurchaseOrders.render.test.tsx` — tests for the new Late badge.

---

### Task 1: Add shared status-variant functions to `lib/status.ts`

**Files:**
- Modify: `frontend/src/lib/status.ts`
- Test: `frontend/src/lib/status.test.ts` (new)

**Interfaces:**
- Produces: `lifecycleStatusVariant(status: string): Variant`, `approvalStatusVariant(status: string): Variant`, `lotStatusVariant(status: string): Variant` — all exported from `@/lib/status`, all returning the same `Variant` type (`NonNullable<BadgeProps["variant"]>`) already used by `poStatusVariant`/`soStatusVariant`/`woStatusVariant` in this file.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/status.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { approvalStatusVariant, lifecycleStatusVariant, lotStatusVariant } from "./status";

describe("lifecycleStatusVariant", () => {
  it("colors active as success", () => {
    expect(lifecycleStatusVariant("active")).toBe("success");
  });

  it("colors archived and retired as neutral", () => {
    expect(lifecycleStatusVariant("archived")).toBe("neutral");
    expect(lifecycleStatusVariant("retired")).toBe("neutral");
  });

  it("colors anything else (draft, inactive) as warning", () => {
    expect(lifecycleStatusVariant("draft")).toBe("warning");
    expect(lifecycleStatusVariant("inactive")).toBe("warning");
  });
});

describe("approvalStatusVariant", () => {
  it("colors approved as success, rejected as danger, pending as warning", () => {
    expect(approvalStatusVariant("approved")).toBe("success");
    expect(approvalStatusVariant("rejected")).toBe("danger");
    expect(approvalStatusVariant("pending")).toBe("warning");
  });
});

describe("lotStatusVariant", () => {
  it("colors on_hold as warning, depleted as neutral, active as success", () => {
    expect(lotStatusVariant("on_hold")).toBe("warning");
    expect(lotStatusVariant("depleted")).toBe("neutral");
    expect(lotStatusVariant("active")).toBe("success");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npm test -- status.test.ts`
Expected: FAIL — `lifecycleStatusVariant`/`approvalStatusVariant`/`lotStatusVariant` are not exported from `./status`.

- [ ] **Step 3: Add the three functions to `frontend/src/lib/status.ts`**

Append this block right after the existing `itemTypeVariant` function (i.e., right before the `/** "partially_received" -> "Partially Received" */` comment and `humanize` function at the bottom of the file):

```ts
const LIFECYCLE_STATUS: Record<string, Variant> = {
  active: "success",
  archived: "neutral",
  retired: "neutral",
};

/** Shared by any page whose status is just active/archived-or-retired/draft
 * (work centers, machines, operators, routings, BOMs) - one lifecycle shape
 * reused instead of each page re-deriving it. */
export function lifecycleStatusVariant(status: string): Variant {
  return LIFECYCLE_STATUS[status] ?? "warning"; // draft / inactive
}

// pending is amber (needs attention), approved green, rejected red - same
// success/warning/danger semantics the rest of the app uses for statuses.
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm test -- status.test.ts`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/status.ts frontend/src/lib/status.test.ts
git commit -m "feat: add shared lifecycle/approval/lot status-variant helpers"
```

---

### Task 2: Consolidate `Production.tsx` onto `lifecycleStatusVariant`

**Files:**
- Modify: `frontend/src/pages/Production.tsx:18` (import), `:78-82` (delete local fn), `:245`, `:478`, `:553`, `:991` (call sites)

**Interfaces:**
- Consumes: `lifecycleStatusVariant` from Task 1.

- [ ] **Step 1: Update the import**

In `frontend/src/pages/Production.tsx`, change:

```ts
import { humanize } from "@/lib/status";
```

to:

```ts
import { humanize, lifecycleStatusVariant } from "@/lib/status";
```

- [ ] **Step 2: Delete the local function**

Delete these lines (currently at `Production.tsx:78-82`):

```ts
function statusVariant(status: string) {
  if (status === "active") return "success" as const;
  if (status === "retired" || status === "archived") return "neutral" as const;
  return "warning" as const; // inactive / draft
}
```

- [ ] **Step 3: Replace all 4 call sites**

Replace each of these four occurrences of `statusVariant(` with `lifecycleStatusVariant(` (same argument in each case):
- `<Badge variant={statusVariant(wc.status)}>{humanize(wc.status)}</Badge>` (work centers row)
- `<Badge variant={statusVariant(m.status)}>{humanize(m.status)}</Badge>` (machines row)
- `<Badge variant={statusVariant(operator.status)}>{humanize(operator.status)}</Badge>` (operators row)
- `<Badge variant={statusVariant(routing.status)}>{humanize(routing.status)}</Badge>` (routings row)

- [ ] **Step 4: Run the existing Production tests to confirm nothing broke**

Run (from `frontend/`): `npm test -- Production.render.test.tsx`
Expected: PASS — same assertions as before (e.g. `expect(html).toContain("Active")`), since `lifecycleStatusVariant` returns the same colors `statusVariant` did.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Production.tsx
git commit -m "refactor: use shared lifecycleStatusVariant in Production.tsx"
```

---

### Task 3: Consolidate `Boms.tsx` onto `lifecycleStatusVariant`

**Files:**
- Modify: `frontend/src/pages/Boms.tsx:26` (import), `:78-82` (delete local fn), `:687`, `:701` (call sites)

**Interfaces:**
- Consumes: `lifecycleStatusVariant` from Task 1.

- [ ] **Step 1: Update the import**

Change:

```ts
import { humanize } from "@/lib/status";
```

to:

```ts
import { humanize, lifecycleStatusVariant } from "@/lib/status";
```

- [ ] **Step 2: Delete the local function**

Delete (currently at `Boms.tsx:78-82`):

```ts
function bomStatusVariant(status: string) {
  if (status === "active") return "success" as const;
  if (status === "archived") return "neutral" as const;
  return "warning" as const; // draft
}
```

- [ ] **Step 3: Replace both call sites**

- `<Badge variant={bomStatusVariant(bom.status)}>{humanize(bom.status)}</Badge>` → `bomStatusVariant` becomes `lifecycleStatusVariant`.
- `<Badge variant={bomStatusVariant(bomDetail.data.status)}>` → same swap.

- [ ] **Step 4: Run the app's full frontend type-check/build to confirm no dangling references**

Run (from `frontend/`): `npm run build`
Expected: PASS with no TypeScript errors (no test file exists for `Boms.tsx` today, so a clean build is the verification for this task).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Boms.tsx
git commit -m "refactor: use shared lifecycleStatusVariant in Boms.tsx"
```

---

### Task 4: Consolidate `Approvals.tsx` onto the shared `approvalStatusVariant`

**Files:**
- Modify: `frontend/src/pages/Approvals.tsx:14` (import), `:17-23` (delete local fn), `:228` (call site)
- Test: `frontend/src/pages/Approvals.render.test.tsx` (existing — must still pass)

**Interfaces:**
- Consumes: `approvalStatusVariant` from Task 1.

- [ ] **Step 1: Update the import**

Change:

```ts
import { humanize } from "@/lib/status";
```

to:

```ts
import { approvalStatusVariant, humanize } from "@/lib/status";
```

- [ ] **Step 2: Delete the local function and its comment**

Delete (currently at `Approvals.tsx:17-23`):

```ts
// pending is amber (needs attention), approved green, rejected red — same
// success/warning/danger semantics the rest of the app uses for statuses.
function approvalStatusVariant(status: string) {
  if (status === "approved") return "success" as const;
  if (status === "rejected") return "danger" as const;
  return "warning" as const;
}
```

(This exact logic now lives in `lib/status.ts` from Task 1 — the call site below needs no change since the function name is unchanged, only its import source.)

- [ ] **Step 3: Run the existing Approvals tests**

Run (from `frontend/`): `npm test -- Approvals.render.test.tsx`
Expected: PASS — unchanged behavior.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Approvals.tsx
git commit -m "refactor: import approvalStatusVariant from lib/status instead of redefining it"
```

---

### Task 5: Consolidate `Lots.tsx` onto the shared `lotStatusVariant`

**Files:**
- Modify: `frontend/src/pages/Lots.tsx:15` (import), `:71-75` (delete local fn), `:452`, `:492` (call sites)

**Interfaces:**
- Consumes: `lotStatusVariant` from Task 1.

- [ ] **Step 1: Update the import**

Change:

```ts
import { humanize } from "@/lib/status";
```

to:

```ts
import { humanize, lotStatusVariant } from "@/lib/status";
```

- [ ] **Step 2: Delete the local function**

Delete (currently at `Lots.tsx:71-75`):

```ts
function lotStatusVariant(status: string) {
  if (status === "on_hold") return "warning" as const;
  if (status === "depleted") return "neutral" as const;
  return "success" as const;
}
```

(Function name is unchanged — both call sites at `:452` and `:492` need no edits, only the import source changed.)

- [ ] **Step 3: Run the frontend build to confirm no dangling references**

Run (from `frontend/`): `npm run build`
Expected: PASS with no TypeScript errors (no test file exists for `Lots.tsx` today).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Lots.tsx
git commit -m "refactor: use shared lotStatusVariant in Lots.tsx"
```

---

### Task 6: Add a "View in Inventory" link to each Work Order row

**Files:**
- Modify: `frontend/src/pages/production/JobsPanel.tsx:1` (import), `:262-265` (row markup)
- Test: `frontend/src/pages/production/JobsPanel.render.test.tsx` (add a case)

**Interfaces:**
- Consumes: `WorkOrder` type from `./types` (already imported), specifically its `item_id: string` field (confirmed present via the `RUNNING`/`SCHEDULED` test fixtures using `item_id: "item-1"`).
- Produces: nothing new consumed elsewhere — this is a leaf UI addition.

- [ ] **Step 1: Write the failing test**

In `frontend/src/pages/production/JobsPanel.render.test.tsx`, add a new test inside the existing `describe("JobsPanel", ...)` block:

```tsx
it("links a job's item to its Inventory page", () => {
  const html = renderJobs((qc) => qc.setQueryData(["work-orders", "", ""], [RUNNING]));
  expect(html).toContain('href="/warehouse?item=item-1"');
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npm test -- production/JobsPanel.render.test.tsx`
Expected: FAIL — no such `href` in the rendered output.

- [ ] **Step 3: Add the `Link` import**

In `frontend/src/pages/production/JobsPanel.tsx`, change:

```ts
import { FormEvent, useState } from "react";
```

to:

```ts
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
```

(Add it as its own new import line right after the `react` import, before the `lucide-react` import already at line 2.)

- [ ] **Step 4: Add the link to the row markup**

In `JobRow`, change:

```tsx
<TD>
  {job.item_name}
  <span className="block text-xs text-muted-foreground">{job.item_sku}</span>
</TD>
```

to:

```tsx
<TD>
  {job.item_name}
  <span className="block text-xs text-muted-foreground">
    {job.item_sku}{" "}
    <Link to={`/warehouse?item=${job.item_id}`} className="underline">
      View in Inventory
    </Link>
  </span>
</TD>
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm test -- production/JobsPanel.render.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full Production suite to confirm the surrounding page still renders correctly**

Run: `npm test -- Production.render.test.tsx`
Expected: PASS — `Production.tsx` embeds `JobsPanel`, so this confirms the new link doesn't break any existing assertion there.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/production/JobsPanel.tsx frontend/src/pages/production/JobsPanel.render.test.tsx
git commit -m "feat: link each work order's item to its Inventory page"
```

---

### Task 7: Fix PO-numbering terminology (`PO #12` → `PO-12`)

**Files:**
- Modify: `frontend/src/pages/planning/ToBuyPanel.tsx:103`
- Modify: `frontend/src/pages/BuildCalculator.tsx:312`

**Interfaces:**
- None — pure display-string change, no behavior, no new tests needed (trivial one-line text edits; grepped confirmed no test asserts on the old `"PO #"` text).

- [ ] **Step 1: Fix `ToBuyPanel.tsx`**

Change:

```tsx
<Badge variant="success">Draft PO #{po.po_number}</Badge>
```

to:

```tsx
<Badge variant="success">Draft PO-{po.po_number}</Badge>
```

- [ ] **Step 2: Fix `BuildCalculator.tsx`**

Change the identical line at `BuildCalculator.tsx:312`:

```tsx
<Badge variant="success">Draft PO #{po.po_number}</Badge>
```

to:

```tsx
<Badge variant="success">Draft PO-{po.po_number}</Badge>
```

- [ ] **Step 3: Run the frontend build to confirm no errors**

Run (from `frontend/`): `npm run build`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/planning/ToBuyPanel.tsx frontend/src/pages/BuildCalculator.tsx
git commit -m "fix: use PO-N form consistently for generated purchase orders"
```

---

### Task 8: Add a "Late" badge to overdue Purchase Orders

**Files:**
- Modify: `frontend/src/pages/PurchaseOrders.tsx:57-58` (near `RECEIVABLE_STATUSES`), `:329-333` (list row), `:366-371` (detail header)
- Test: `frontend/src/pages/PurchaseOrders.render.test.tsx` (new)

**Interfaces:**
- Consumes: `PurchaseOrderResponse` type (already defined in this file at line 32-39), specifically `status: string` and `expected_delivery_date: string | null`.
- Produces: `isLate(po): boolean`, used only within this file.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/pages/PurchaseOrders.render.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PurchaseOrdersPanel } from "./PurchaseOrders";

const YESTERDAY = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
const TOMORROW = new Date(Date.now() + 86400000).toISOString().slice(0, 10);

const LATE_PO = {
  id: "po-1",
  supplier_id: "sup-1",
  supplier_name: "Sealtech",
  po_number: 12,
  status: "submitted",
  expected_delivery_date: YESTERDAY,
};

const ON_TIME_PO = {
  id: "po-2",
  supplier_id: "sup-1",
  supplier_name: "Sealtech",
  po_number: 13,
  status: "submitted",
  expected_delivery_date: TOMORROW,
};

const DRAFT_PO = {
  id: "po-3",
  supplier_id: "sup-1",
  supplier_name: "Sealtech",
  po_number: 14,
  status: "draft",
  expected_delivery_date: YESTERDAY,
};

function renderPos(rows: unknown[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["purchase-orders", "", 0], rows);
  qc.setQueryData(["items"], []);
  qc.setQueryData(["suppliers", "active"], []);
  return renderToString(
    createElement(QueryClientProvider, { client: qc }, createElement(PurchaseOrdersPanel)),
  ).split("<!-- -->").join("");
}

describe("PurchaseOrdersPanel", () => {
  it("badges a submitted PO past its expected delivery date as late", () => {
    const html = renderPos([LATE_PO]);
    expect(html).toContain("PO-12");
    expect(html).toContain("Late");
  });

  it("does not badge a submitted PO still due in the future", () => {
    const html = renderPos([ON_TIME_PO]);
    expect(html).toContain("PO-13");
    expect(html).not.toContain("Late");
  });

  it("does not badge a draft PO even if its expected date has passed", () => {
    // A draft hasn't been sent to the supplier yet, so "late" doesn't apply.
    const html = renderPos([DRAFT_PO]);
    expect(html).toContain("PO-14");
    expect(html).not.toContain("Late");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npm test -- PurchaseOrders.render.test.tsx`
Expected: FAIL — no "Late" badge exists yet, so the first test fails (`expect(html).toContain("Late")` finds nothing).

- [ ] **Step 3: Add the `isLate` function**

In `frontend/src/pages/PurchaseOrders.tsx`, right after the existing constants:

```ts
const CANCELLABLE_STATUSES = ["draft", "submitted"];
const RECEIVABLE_STATUSES = ["submitted", "partially_received"];
```

add:

```ts
/** A submitted or partially-received PO whose expected delivery date has
 * passed. Same treatment JobsPanel gives an overdue work order - a draft PO
 * is excluded because it was never sent to the supplier in the first place. */
function isLate(po: Pick<PurchaseOrderResponse, "status" | "expected_delivery_date">): boolean {
  if (!RECEIVABLE_STATUSES.includes(po.status)) return false;
  if (po.expected_delivery_date === null) return false;
  return po.expected_delivery_date < new Date().toISOString().slice(0, 10);
}
```

- [ ] **Step 4: Add the badge to the list row**

Change:

```tsx
                  <TD className="font-medium">PO-{po.po_number}</TD>
                  <TD>{po.supplier_name}</TD>
                  <TD>
                    <Badge variant={poStatusVariant(po.status)}>{humanize(po.status)}</Badge>
                  </TD>
```

to:

```tsx
                  <TD className="font-medium">PO-{po.po_number}</TD>
                  <TD>{po.supplier_name}</TD>
                  <TD>
                    <div className="flex flex-wrap items-center gap-1">
                      <Badge variant={poStatusVariant(po.status)}>{humanize(po.status)}</Badge>
                      {isLate(po) && <Badge variant="danger">Late</Badge>}
                    </div>
                  </TD>
```

- [ ] **Step 5: Add the badge to the detail header**

Change:

```tsx
            <CardTitle className="flex items-center gap-2">
              PO-{poDetail.data.po_number} · {poDetail.data.supplier_name}
              <Badge variant={poStatusVariant(poDetail.data.status)}>
                {humanize(poDetail.data.status)}
              </Badge>
            </CardTitle>
```

to:

```tsx
            <CardTitle className="flex items-center gap-2">
              PO-{poDetail.data.po_number} · {poDetail.data.supplier_name}
              <Badge variant={poStatusVariant(poDetail.data.status)}>
                {humanize(poDetail.data.status)}
              </Badge>
              {isLate(poDetail.data) && <Badge variant="danger">Late</Badge>}
            </CardTitle>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm test -- PurchaseOrders.render.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/PurchaseOrders.tsx frontend/src/pages/PurchaseOrders.render.test.tsx
git commit -m "feat: badge overdue purchase orders as Late"
```

---

## Final Verification

- [ ] **Run the full frontend test suite**

Run (from `frontend/`): `npm test`
Expected: PASS, all suites green.

- [ ] **Run the full frontend build**

Run (from `frontend/`): `npm run build`
Expected: PASS, no TypeScript errors.
