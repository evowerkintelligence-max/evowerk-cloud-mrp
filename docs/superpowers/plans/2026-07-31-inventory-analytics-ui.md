# Inventory Analytics UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the existing, tested inventory-analytics engine (ABC/XYZ classification, RCA root-cause, lead-time scenario compare) inside the existing Warehouse → Inventory tab, in plain language, with light cross-links to the Planning Grid.

**Architecture:** One new backend orchestration endpoint (`GET /inventory/rca/auto`) that picks the most significant RCA dimension server-side. Three new frontend files under `frontend/src/pages/inventory/` (pure helpers, an insight banner, a row-detail panel), wired into the existing `InventoryPanel` component and cross-linked from `Planning.tsx`. No new statistical or business logic beyond the one orchestration function — every number displayed already exists and is already tested.

**Tech Stack:** FastAPI + SQLAlchemy (backend, Python), React + TanStack Query + react-router-dom (frontend, TypeScript), pytest (backend tests), vitest (frontend tests, `renderToString`-based render tests — this codebase does not use `@testing-library/react` or interaction simulation; tests assert on rendered HTML strings for given seeded data, not on click/keyboard events).

## Global Constraints

- No new backend statistical logic. `compute_rca`, `compute_inventory_analytics`, and `compare_lead_time_scenario` are unchanged; the only new backend code is orchestration (calling existing functions and picking a winner).
- Plain language only in default views; real numbers (ABC/XYZ letters, chi-square statistics, raw reorder-point figures) appear only behind an explicit "show the numbers" / "see why" expand.
- No new nav items, no new top-level routes. `InventoryPanel` (`frontend/src/pages/Inventory.tsx`) is rendered as the "Inventory" tab of `Warehouse.tsx` at route `/warehouse` — there is no standalone `/inventory` frontend route. Cross-links target `/warehouse?item=<id>`, not `/inventory`.
- Frontend tests follow the existing convention in this codebase: `*.render.test.tsx` files using `renderToString` from `react-dom/server`, wrapped in `MemoryRouter` + `QueryClientProvider`, asserting on the resulting HTML string. There is no interaction-simulation testing anywhere in this frontend; do not introduce `@testing-library/react` or `fireEvent`.
- Every new TanStack Query call uses `apiFetch` from `frontend/src/api/client.ts` (handles auth headers and token refresh) — never a bare `fetch`.
- Money/quantity figures in this codebase that come from `Decimal` backend fields are typed as `string` on the frontend; figures that come from Python `float` fields (as `reorder_point`, `days_of_supply`, `safety_stock`, `chi2_statistic`, `p_value`, `standardized_residual`, `expected_at_risk_count` all are) are typed as `number`.

---

## File Structure

**Backend (2 files modified, 1 created):**
- `backend/app/services/rca.py` — add `compute_rca_auto()`, orchestrates the existing `compute_rca()` across all `DIMENSIONS`.
- `backend/app/api/routes/inventory_analytics.py` — add `GET /inventory/rca/auto` route.
- `backend/tests/test_rca_auto.py` (new) — tests for the new endpoint.

**Frontend (3 files created under a new `inventory/` subdirectory, mirroring the existing `planning/` subdirectory pattern; 2 existing pages modified; 2 test files touched):**
- `frontend/src/pages/inventory/types.ts` (new) — shared types + pure functions (label translation, sentence building, row sorting). No React, no fetching — testable in isolation.
- `frontend/src/pages/inventory/types.test.ts` (new) — vitest unit tests for the pure functions above, following `frontend/src/pages/analytics/shared.test.ts`'s pattern.
- `frontend/src/pages/inventory/InsightBanner.tsx` (new) — the auto-surfaced RCA banner.
- `frontend/src/pages/inventory/AnalyticsDetail.tsx` (new) — the "why this status" / "what if lead time" block for row expansion.
- `frontend/src/pages/Inventory.tsx` (modified) — wires in the two components above, adds the cross-link to Planning, adds `?item=` auto-select, adds ABC-aware row sorting.
- `frontend/src/pages/Inventory.render.test.tsx` (new) — render tests for `InsightBanner`, `AnalyticsDetail`, and the wired-up `InventoryPanel`, following `frontend/src/pages/Planning.render.test.tsx`'s pattern exactly (same imports, same `render()` helper shape, `describe` blocks per concern).
- `frontend/src/pages/Planning.tsx` (modified) — adds the "View in Inventory" per-row link to `/warehouse?item=<id>`.
- `frontend/src/pages/Planning.render.test.tsx` (modified) — extends with tests for the two additions above.

---

### Task 1: Backend — `GET /inventory/rca/auto`

**Files:**
- Modify: `backend/app/services/rca.py`
- Modify: `backend/app/api/routes/inventory_analytics.py`
- Test: `backend/tests/test_rca_auto.py` (create)

**Interfaces:**
- Produces: `compute_rca_auto(db: AsyncSession, org_id: uuid.UUID) -> RcaResult` in `app.services.rca`. Returns the `RcaResult` (same dataclass `compute_rca` already returns) for whichever of `DIMENSIONS` has the highest `chi2_statistic` among results where `method == "chi_square_independence"` and at least one value has `is_significant=True`. If none qualify, returns an `RcaResult` with `dimension=""`, `method="no_significant_dimension"`, all numeric fields `None`, `values=[]`.
- Produces: `GET /inventory/rca/auto` — same response shape as the existing `GET /inventory/rca` (`RcaResponse`), any authenticated member.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_rca_auto.py`:

```python
from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.bom import Item
from app.models.inventory import InventoryTransaction

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret - satisfies policy: 12+ chars, letter + digit


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _create_supplier(client, token, name="Supplier"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _default_location_id(client, token) -> str:
    res = await client.get("/locations", headers={"Authorization": f"Bearer {token}"})
    return next(loc["id"] for loc in res.json() if loc["is_default"])


async def _bulk_create_items(
    test_engine,
    *,
    org_id: str,
    sku_prefix: str,
    count: int,
    supplier_id: str | None = None,
) -> list[str]:
    """Direct DB insert - same helper pattern as test_rca.py."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ids: list[uuid_module.UUID] = []
    async with session_factory() as session:
        for i in range(count):
            item_id = uuid_module.uuid4()
            ids.append(item_id)
            session.add(
                Item(
                    id=item_id,
                    org_id=uuid_module.UUID(org_id),
                    sku=f"{sku_prefix}-{i}",
                    name=f"{sku_prefix} {i}",
                    item_type="raw_material",
                    unit_of_measure="ea",
                    is_lot_tracked=False,
                    preferred_supplier_id=uuid_module.UUID(supplier_id) if supplier_id else None,
                )
            )
        await session.commit()
    return [str(i) for i in ids]


async def _insert_consumption_for_items(
    test_engine, *, org_id: str, item_ids: list[str], location_id: str, created_by: str, qty=5
):
    """Gives each item real consumption history with no on-hand received -
    status becomes "stockout", same helper pattern as test_rca.py."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for item_id in item_ids:
            session.add(
                InventoryTransaction(
                    org_id=uuid_module.UUID(org_id),
                    item_id=uuid_module.UUID(item_id),
                    location_id=uuid_module.UUID(location_id),
                    transaction_type="consumption",
                    quantity_delta=-Decimal(str(qty)),
                    created_by=uuid_module.UUID(created_by),
                    created_at=now,
                )
            )
        await session.commit()


async def test_no_items_returns_no_significant_dimension(client, test_engine):
    _, token = await _register_and_login(client)
    res = await client.get("/inventory/rca/auto", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["method"] == "no_significant_dimension"
    assert body["dimension"] == ""
    assert body["chi2_statistic"] is None
    assert body["values"] == []


async def test_picks_the_significant_supplier_dimension(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)
    supplier_a = await _create_supplier(client, token, "Supplier A")
    supplier_b = await _create_supplier(client, token, "Supplier B")

    # Same perfect-separation setup as test_rca.py's
    # test_significant_association_by_supplier: A is all stockout, B is all
    # healthy/no_data. item_type is constant ("raw_material") across every
    # item here, and abc_class is unclassified for all of them (no price on
    # file), so supplier is the only dimension that can come out significant.
    stockout_items = await _bulk_create_items(
        test_engine, org_id=org_id, sku_prefix="A", count=10, supplier_id=supplier_a["id"]
    )
    await _insert_consumption_for_items(
        test_engine, org_id=org_id, item_ids=stockout_items, location_id=location_id, created_by=user_id
    )
    await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="B", count=10, supplier_id=supplier_b["id"])

    res = await client.get("/inventory/rca/auto", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["dimension"] == "supplier"
    assert body["method"] == "chi_square_independence"
    assert any(v["is_significant"] for v in body["values"])


async def test_org_isolation(client, test_engine):
    reg_a, _token_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    await _bulk_create_items(test_engine, org_id=reg_a["org_id"], sku_prefix="A", count=5)
    _, token_b = await _register_and_login(client, email="b@example.com", org_name="Org B")

    res = await client.get("/inventory/rca/auto", headers={"Authorization": f"Bearer {token_b}"})
    assert res.status_code == 200
    assert res.json()["method"] == "no_significant_dimension"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rca_auto.py -v`
Expected: all 3 tests FAIL with `404 Not Found` (the route doesn't exist yet), e.g. `assert 404 == 200`.

- [ ] **Step 3: Implement `compute_rca_auto` in `backend/app/services/rca.py`**

Add this function at the end of `backend/app/services/rca.py` (after `compute_rca`):

```python
async def compute_rca_auto(db: AsyncSession, org_id: uuid.UUID) -> RcaResult:
    """Runs compute_rca across every dimension in DIMENSIONS and returns the
    one most likely to explain stockouts/lows: the highest chi-square
    statistic among results with at least one significant value. Pure
    orchestration for the Inventory tab's auto-surfaced insight banner - no
    new statistics beyond compute_rca itself. Ties go to whichever dimension
    comes first in DIMENSIONS, since only a strictly greater statistic
    replaces the current best as dimensions are tried in that order."""
    best: RcaResult | None = None
    best_chi2 = -1.0
    for dimension in DIMENSIONS:
        result = await compute_rca(db, org_id, dimension)
        if result.method != "chi_square_independence" or result.chi2_statistic is None:
            continue
        if not any(v.is_significant for v in result.values):
            continue
        if result.chi2_statistic > best_chi2:
            best = result
            best_chi2 = result.chi2_statistic
    if best is not None:
        return best
    return RcaResult(
        dimension="",
        method="no_significant_dimension",
        chi2_statistic=None,
        p_value=None,
        degrees_of_freedom=None,
        low_expected_frequency_warning=False,
        values=[],
    )
```

- [ ] **Step 4: Add the route in `backend/app/api/routes/inventory_analytics.py`**

Modify the import line (currently `from app.services.rca import DIMENSIONS, RcaDimensionValue, RcaResult, compute_rca`) to also import `compute_rca_auto`:

```python
from app.services.rca import DIMENSIONS, RcaDimensionValue, RcaResult, compute_rca, compute_rca_auto
```

Add this route immediately after the existing `get_rca` function (after its closing `return _rca_to_response(result)`, before the `class ScenarioCompareRequest` block):

```python
@router.get("/rca/auto", response_model=RcaResponse)
async def get_rca_auto(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Runs RCA across supplier/item_type/abc_class and returns the single
    most statistically significant finding, for the Inventory tab's
    auto-surfaced insight banner. See app.services.rca.compute_rca_auto."""
    result = await compute_rca_auto(db, current.org_id)
    return _rca_to_response(result)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rca_auto.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Run the full existing RCA/inventory test suite to confirm nothing broke**

Run: `cd backend && python -m pytest tests/test_rca.py tests/test_inventory_analytics.py tests/test_scenario.py -v`
Expected: all PASS, unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/rca.py backend/app/api/routes/inventory_analytics.py backend/tests/test_rca_auto.py
git commit -m "feat: add GET /inventory/rca/auto, picks the most significant RCA dimension"
```

---

### Task 2: Frontend — `inventory/types.ts` pure helpers

**Files:**
- Create: `frontend/src/pages/inventory/types.ts`
- Create: `frontend/src/pages/inventory/types.test.ts`

**Interfaces:**
- Consumes: nothing (pure TypeScript, no imports from other new files in this plan). Imports `humanize` from `@/lib/status` (already exists).
- Produces, all exported from `frontend/src/pages/inventory/types.ts`:
  - `interface InventoryAnalyticsLine { item_id: string; reorder_point: number | null; safety_stock: number | null; days_of_supply: number | null; abc_class: string | null; xyz_class: string | null; status: string; }`
  - `interface RcaValue { value: string; item_count: number; at_risk_count: number; expected_at_risk_count: number; standardized_residual: number; is_significant: boolean; }`
  - `interface RcaResult { dimension: string; method: string; chi2_statistic: number | null; p_value: number | null; degrees_of_freedom: number | null; low_expected_frequency_warning: boolean; values: RcaValue[]; }`
  - `interface ScenarioDelta { item_id: string; baseline_lead_time_days: number | null; scenario_lead_time_days: number | null; baseline_reorder_point: number | null; scenario_reorder_point: number | null; reorder_point_delta: number | null; baseline_status: string; scenario_status: string; classification: string; }`
  - `const DIMENSION_OPTIONS: { value: string; label: string }[]`
  - `function abcLabel(abcClass: string | null): string | null`
  - `function xyzLabel(xyzClass: string | null): string | null`
  - `function whyThisStatusSentence(line: InventoryAnalyticsLine): string`
  - `function rcaTopValue(result: RcaResult): RcaValue | null`
  - `function rcaBannerSentence(result: RcaResult): string | null`
  - `function scenarioDeltaSentence(delta: ScenarioDelta): string`
  - `function sortInventoryRows<T extends { item_id: string; item_sku: string }>(rows: T[], analyticsByItem: Map<string, InventoryAnalyticsLine>): T[]`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/pages/inventory/types.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  abcLabel,
  rcaBannerSentence,
  rcaTopValue,
  scenarioDeltaSentence,
  sortInventoryRows,
  whyThisStatusSentence,
  xyzLabel,
  type InventoryAnalyticsLine,
  type RcaResult,
  type ScenarioDelta,
} from "./types";

describe("abcLabel", () => {
  it("translates each class to plain language", () => {
    expect(abcLabel("A")).toBe("High-value item");
    expect(abcLabel("B")).toBe("Mid-value item");
    expect(abcLabel("C")).toBe("Lower-value item");
  });

  it("returns null for an unclassified item", () => {
    expect(abcLabel(null)).toBeNull();
  });
});

describe("xyzLabel", () => {
  it("translates each class to plain language", () => {
    expect(xyzLabel("X")).toBe("Steady demand");
    expect(xyzLabel("Y")).toBe("Somewhat variable demand");
    expect(xyzLabel("Z")).toBe("Erratic demand");
  });

  it("returns null for an unclassified item", () => {
    expect(xyzLabel(null)).toBeNull();
  });
});

const LINE: InventoryAnalyticsLine = {
  item_id: "i-1",
  reorder_point: 120,
  safety_stock: 40,
  days_of_supply: 12,
  abc_class: "A",
  xyz_class: "X",
  status: "low",
};

describe("whyThisStatusSentence", () => {
  it("explains a low item with its reorder point and value/demand class", () => {
    expect(whyThisStatusSentence(LINE)).toBe(
      "12 days of stock left, below your reorder point of 120 units. High-value item, steady demand.",
    );
  });

  it("explains a stockout without a days-of-supply figure", () => {
    expect(whyThisStatusSentence({ ...LINE, status: "stockout", days_of_supply: null, abc_class: null, xyz_class: null })).toBe(
      "Out of stock.",
    );
  });

  it("explains dead stock", () => {
    expect(whyThisStatusSentence({ ...LINE, status: "dead_stock", abc_class: null, xyz_class: null })).toBe(
      "No recent demand, but stock is still on hand.",
    );
  });

  it("falls back to plain status text for no_data", () => {
    expect(whyThisStatusSentence({ ...LINE, status: "no_data", abc_class: null, xyz_class: null })).toBe(
      "Not enough history yet to analyze this item.",
    );
  });
});

const SIGNIFICANT_RESULT: RcaResult = {
  dimension: "supplier",
  method: "chi_square_independence",
  chi2_statistic: 14.2,
  p_value: 0.0002,
  degrees_of_freedom: 1,
  low_expected_frequency_warning: false,
  values: [
    {
      value: "Acme Supplier",
      item_count: 10,
      at_risk_count: 10,
      expected_at_risk_count: 3.125,
      standardized_residual: 3.9,
      is_significant: true,
    },
  ],
};

describe("rcaTopValue / rcaBannerSentence", () => {
  it("picks the top significant value and phrases a supplier finding", () => {
    expect(rcaTopValue(SIGNIFICANT_RESULT)?.value).toBe("Acme Supplier");
    expect(rcaBannerSentence(SIGNIFICANT_RESULT)).toBe(
      "Items from Acme Supplier run low 3.2× more often than your other stock.",
    );
  });

  it("phrases an item_type finding without 'from'", () => {
    const result: RcaResult = {
      ...SIGNIFICANT_RESULT,
      dimension: "item_type",
      values: [{ ...SIGNIFICANT_RESULT.values[0], value: "raw_material" }],
    };
    expect(rcaBannerSentence(result)).toBe(
      "Raw Material items run low 3.2× more often than your other stock.",
    );
  });

  it("returns null when the method is insufficient_data", () => {
    expect(rcaBannerSentence({ ...SIGNIFICANT_RESULT, method: "insufficient_data", values: [] })).toBeNull();
  });

  it("returns null when no value is significant", () => {
    const result: RcaResult = {
      ...SIGNIFICANT_RESULT,
      values: [{ ...SIGNIFICANT_RESULT.values[0], is_significant: false }],
    };
    expect(rcaBannerSentence(result)).toBeNull();
  });
});

describe("scenarioDeltaSentence", () => {
  it("describes a reorder point drop with a status change", () => {
    const delta: ScenarioDelta = {
      item_id: "i-1",
      baseline_lead_time_days: 14,
      scenario_lead_time_days: 7,
      baseline_reorder_point: 120,
      scenario_reorder_point: 95,
      reorder_point_delta: -25,
      baseline_status: "low",
      scenario_status: "healthy",
      classification: "improved",
    };
    expect(scenarioDeltaSentence(delta)).toBe(
      "Reorder point would drop from 120 to 95 units; status would change from Low to Healthy.",
    );
  });

  it("says nothing changes when the status is the same", () => {
    const delta: ScenarioDelta = {
      item_id: "i-1",
      baseline_lead_time_days: 14,
      scenario_lead_time_days: 12,
      baseline_reorder_point: 120,
      scenario_reorder_point: 118,
      reorder_point_delta: -2,
      baseline_status: "healthy",
      scenario_status: "healthy",
      classification: "unchanged",
    };
    expect(scenarioDeltaSentence(delta)).toBe("Reorder point would drop from 120 to 118 units.");
  });

  it("says the reorder point can't be estimated without a lead time", () => {
    const delta: ScenarioDelta = {
      item_id: "i-1",
      baseline_lead_time_days: null,
      scenario_lead_time_days: 7,
      baseline_reorder_point: null,
      scenario_reorder_point: null,
      reorder_point_delta: null,
      baseline_status: "no_data",
      scenario_status: "no_data",
      classification: "unchanged",
    };
    expect(scenarioDeltaSentence(delta)).toBe("Reorder point can't be estimated without a lead time.");
  });
});

describe("sortInventoryRows", () => {
  const analyticsByItem = new Map<string, InventoryAnalyticsLine>([
    ["healthy-c", { ...LINE, item_id: "healthy-c", status: "healthy", abc_class: "C" }],
    ["stockout-b", { ...LINE, item_id: "stockout-b", status: "stockout", abc_class: "B" }],
    ["stockout-a", { ...LINE, item_id: "stockout-a", status: "stockout", abc_class: "A" }],
  ]);
  const rows = [
    { item_id: "healthy-c", item_sku: "HC" },
    { item_id: "stockout-b", item_sku: "SB" },
    { item_id: "stockout-a", item_sku: "SA" },
  ];

  it("sorts stockouts before healthy items, high-value first within a tie", () => {
    const sorted = sortInventoryRows(rows, analyticsByItem);
    expect(sorted.map((r) => r.item_id)).toEqual(["stockout-a", "stockout-b", "healthy-c"]);
  });

  it("puts rows with no analytics data last, ahead of nothing", () => {
    const sorted = sortInventoryRows(
      [...rows, { item_id: "no-data", item_sku: "ND" }],
      analyticsByItem,
    );
    expect(sorted[sorted.length - 1].item_id).toBe("no-data");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/inventory/types.test.ts`
Expected: FAIL — `Failed to resolve import "./types"` (the file doesn't exist yet).

- [ ] **Step 3: Implement `frontend/src/pages/inventory/types.ts`**

```typescript
import { humanize } from "@/lib/status";

export interface InventoryAnalyticsLine {
  item_id: string;
  reorder_point: number | null;
  safety_stock: number | null;
  days_of_supply: number | null;
  abc_class: string | null;
  xyz_class: string | null;
  status: string;
}

export interface RcaValue {
  value: string;
  item_count: number;
  at_risk_count: number;
  expected_at_risk_count: number;
  standardized_residual: number;
  is_significant: boolean;
}

export interface RcaResult {
  dimension: string;
  method: string;
  chi2_statistic: number | null;
  p_value: number | null;
  degrees_of_freedom: number | null;
  low_expected_frequency_warning: boolean;
  values: RcaValue[];
}

export interface ScenarioDelta {
  item_id: string;
  baseline_lead_time_days: number | null;
  scenario_lead_time_days: number | null;
  baseline_reorder_point: number | null;
  scenario_reorder_point: number | null;
  reorder_point_delta: number | null;
  baseline_status: string;
  scenario_status: string;
  classification: string;
}

export const DIMENSION_OPTIONS: { value: string; label: string }[] = [
  { value: "supplier", label: "Supplier" },
  { value: "item_type", label: "Item type" },
  { value: "abc_class", label: "Value class" },
];

const ABC_LABELS: Record<string, string> = {
  A: "High-value item",
  B: "Mid-value item",
  C: "Lower-value item",
};

export function abcLabel(abcClass: string | null): string | null {
  return abcClass ? (ABC_LABELS[abcClass] ?? null) : null;
}

const XYZ_LABELS: Record<string, string> = {
  X: "Steady demand",
  Y: "Somewhat variable demand",
  Z: "Erratic demand",
};

export function xyzLabel(xyzClass: string | null): string | null {
  return xyzClass ? (XYZ_LABELS[xyzClass] ?? null) : null;
}

function classFragment(line: InventoryAnalyticsLine): string {
  const parts = [abcLabel(line.abc_class), xyzLabel(line.xyz_class)].filter(
    (p): p is string => p !== null,
  );
  return parts.length > 0 ? ` ${parts.join(", ")}.` : "";
}

export function whyThisStatusSentence(line: InventoryAnalyticsLine): string {
  const days = line.days_of_supply != null ? Math.round(line.days_of_supply) : null;
  const reorder = line.reorder_point != null ? Math.round(line.reorder_point) : null;

  switch (line.status) {
    case "stockout":
      return `Out of stock.${classFragment(line)}`;
    case "low":
      if (days != null && reorder != null) {
        return `${days} days of stock left, below your reorder point of ${reorder} units.${classFragment(line)}`;
      }
      return `Running low on stock.${classFragment(line)}`;
    case "dead_stock":
      return `No recent demand, but stock is still on hand.${classFragment(line)}`;
    case "excess":
      return days != null
        ? `${days} days of stock on hand — more than you typically need.${classFragment(line)}`
        : `More stock on hand than you typically need.${classFragment(line)}`;
    case "slow_moving":
      return days != null
        ? `${days} days of stock on hand, moving slower than usual.${classFragment(line)}`
        : `Moving slower than usual.${classFragment(line)}`;
    case "healthy":
      return days != null
        ? `${days} days of stock on hand — on track.${classFragment(line)}`
        : `On track.${classFragment(line)}`;
    case "no_data":
      return "Not enough history yet to analyze this item.";
    default:
      return humanize(line.status);
  }
}

export function rcaTopValue(result: RcaResult): RcaValue | null {
  if (result.method !== "chi_square_independence") return null;
  // Backend already sorts values by |standardized_residual| descending, so
  // the first significant one is the biggest anomaly.
  return result.values.find((v) => v.is_significant) ?? null;
}

const DIMENSION_PHRASING: Record<string, (value: string) => string> = {
  supplier: (value) => `Items from ${value}`,
  item_type: (value) => `${humanize(value)} items`,
  abc_class: (value) => `${value === "unclassified" ? "Unclassified" : value} class items`,
};

export function rcaBannerSentence(result: RcaResult): string | null {
  const top = rcaTopValue(result);
  if (!top || top.expected_at_risk_count <= 0) return null;
  const ratio = top.at_risk_count / top.expected_at_risk_count;
  const phrase = (DIMENSION_PHRASING[result.dimension] ?? ((v: string) => `Items in "${v}"`))(top.value);
  return `${phrase} run low ${ratio.toFixed(1)}× more often than your other stock.`;
}

export function scenarioDeltaSentence(delta: ScenarioDelta): string {
  const from = delta.baseline_reorder_point;
  const to = delta.scenario_reorder_point;
  if (from == null || to == null) {
    return "Reorder point can't be estimated without a lead time.";
  }
  const direction = to > from ? "rise" : to < from ? "drop" : "stay";
  const reorderPart = `Reorder point would ${direction} from ${Math.round(from)} to ${Math.round(to)} units`;
  if (delta.baseline_status === delta.scenario_status) {
    return `${reorderPart}.`;
  }
  return `${reorderPart}; status would change from ${humanize(delta.baseline_status)} to ${humanize(delta.scenario_status)}.`;
}

const STATUS_URGENCY: Record<string, number> = {
  stockout: 0,
  low: 1,
  dead_stock: 2,
  excess: 3,
  slow_moving: 4,
  healthy: 5,
  no_data: 6,
};

const ABC_RANK: Record<string, number> = { A: 0, B: 1, C: 2 };

export function sortInventoryRows<T extends { item_id: string; item_sku: string }>(
  rows: T[],
  analyticsByItem: Map<string, InventoryAnalyticsLine>,
): T[] {
  return [...rows].sort((a, b) => {
    const aLine = analyticsByItem.get(a.item_id);
    const bLine = analyticsByItem.get(b.item_id);
    const aUrgency = aLine ? (STATUS_URGENCY[aLine.status] ?? 99) : 99;
    const bUrgency = bLine ? (STATUS_URGENCY[bLine.status] ?? 99) : 99;
    if (aUrgency !== bUrgency) return aUrgency - bUrgency;
    const aAbc = aLine?.abc_class ? (ABC_RANK[aLine.abc_class] ?? 3) : 3;
    const bAbc = bLine?.abc_class ? (ABC_RANK[bLine.abc_class] ?? 3) : 3;
    if (aAbc !== bAbc) return aAbc - bAbc;
    return a.item_sku.localeCompare(b.item_sku);
  });
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/inventory/types.test.ts`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/inventory/types.ts frontend/src/pages/inventory/types.test.ts
git commit -m "feat: add plain-language inventory analytics helpers and row sorting"
```

---

### Task 3: Frontend — `InsightBanner.tsx`

**Files:**
- Create: `frontend/src/pages/inventory/InsightBanner.tsx`
- Create: `frontend/src/pages/Inventory.render.test.tsx`

**Interfaces:**
- Consumes: `DIMENSION_OPTIONS`, `rcaBannerSentence`, `type RcaResult` from `./types` (Task 2). `apiFetch` from `../../api/client`. `Alert` from `@/components/ui/alert`. `Select` from `@/components/ui/select`. `Table`/`TBody`/`TD`/`TH`/`THead`/`TR`/`TableWrap` from `@/components/ui/table`. `Link` from `react-router-dom`.
- Produces: `export function InsightBanner()` — a React component taking no props, rendered with no wrapping required beyond `QueryClientProvider` (and `MemoryRouter`/`BrowserRouter` for the `Link`). Renders nothing (`null`) when there is no significant finding or the query hasn't resolved yet.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/Inventory.render.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { InsightBanner } from "./inventory/InsightBanner";
import type { RcaResult } from "./inventory/types";

function renderWithProviders(node: ReturnType<typeof createElement>, seed: (qc: QueryClient) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  return renderToString(
    createElement(MemoryRouter, null, createElement(QueryClientProvider, { client: qc }, node)),
  )
    .split("<!-- -->")
    .join("");
}

const SIGNIFICANT_RESULT: RcaResult = {
  dimension: "supplier",
  method: "chi_square_independence",
  chi2_statistic: 14.2,
  p_value: 0.0002,
  degrees_of_freedom: 1,
  low_expected_frequency_warning: false,
  values: [
    {
      value: "Acme Supplier",
      item_count: 10,
      at_risk_count: 10,
      expected_at_risk_count: 3.125,
      standardized_residual: 3.9,
      is_significant: true,
    },
  ],
};

const EMPTY_RESULT: RcaResult = {
  dimension: "",
  method: "no_significant_dimension",
  chi2_statistic: null,
  p_value: null,
  degrees_of_freedom: null,
  low_expected_frequency_warning: false,
  values: [],
};

describe("InsightBanner", () => {
  it("shows a plain-language finding when one dimension is significant", () => {
    const html = renderWithProviders(createElement(InsightBanner), (qc) =>
      qc.setQueryData(["inventory-rca-auto"], SIGNIFICANT_RESULT),
    );
    expect(html).toContain("Items from Acme Supplier run low 3.2");
    expect(html).toContain("See why");
    expect(html).toContain("Check a different cause");
  });

  it("renders nothing when no dimension is significant", () => {
    const html = renderWithProviders(createElement(InsightBanner), (qc) =>
      qc.setQueryData(["inventory-rca-auto"], EMPTY_RESULT),
    );
    expect(html).toBe("");
  });

  it("renders nothing before the query has resolved", () => {
    const html = renderWithProviders(createElement(InsightBanner), () => {});
    expect(html).toBe("");
  });

  it("hides the raw numbers until 'See why' is used", () => {
    const html = renderWithProviders(createElement(InsightBanner), (qc) =>
      qc.setQueryData(["inventory-rca-auto"], SIGNIFICANT_RESULT),
    );
    expect(html).not.toContain("Expected");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Inventory.render.test.tsx`
Expected: FAIL — `Failed to resolve import "./inventory/InsightBanner"`.

- [ ] **Step 3: Implement `frontend/src/pages/inventory/InsightBanner.tsx`**

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Alert } from "@/components/ui/alert";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { DIMENSION_OPTIONS, rcaBannerSentence, type RcaResult } from "./types";

async function fetchRca(path: string): Promise<RcaResult> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error("Failed to load the insight");
  return res.json();
}

function dimensionLabel(dimension: string): string {
  return DIMENSION_OPTIONS.find((o) => o.value === dimension)?.label ?? dimension;
}

export function InsightBanner() {
  const [dismissed, setDismissed] = useState(false);
  const [showWhy, setShowWhy] = useState(false);
  const [manualDimension, setManualDimension] = useState<string | null>(null);

  const auto = useQuery<RcaResult>({
    queryKey: ["inventory-rca-auto"],
    queryFn: () => fetchRca("/inventory/rca/auto"),
    enabled: manualDimension === null,
  });
  const manual = useQuery<RcaResult>({
    queryKey: ["inventory-rca", manualDimension],
    queryFn: () => fetchRca(`/inventory/rca?dimension=${manualDimension}`),
    enabled: manualDimension !== null,
  });

  const result = manualDimension === null ? auto.data : manual.data;
  if (dismissed || !result) return null;

  const sentence = rcaBannerSentence(result);
  if (manualDimension === null && !sentence) return null;

  return (
    <Alert variant="info" className="mb-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <p>{sentence ?? `Nothing stands out for ${dimensionLabel(manualDimension ?? "").toLowerCase()}.`}</p>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
            {sentence && (
              <button type="button" className="underline" onClick={() => setShowWhy((v) => !v)}>
                {showWhy ? "Hide the numbers" : "See why"}
              </button>
            )}
            <button
              type="button"
              className="underline"
              onClick={() =>
                setManualDimension(manualDimension === null ? result.dimension || "supplier" : null)
              }
            >
              {manualDimension === null ? "Check a different cause" : "Back to the top cause"}
            </button>
            {manualDimension !== null && (
              <Select
                aria-label="Cause"
                value={manualDimension}
                onChange={(e) => setManualDimension(e.target.value)}
              >
                {DIMENSION_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
            )}
          </div>
          {showWhy && sentence && (
            <TableWrap className="mt-3">
              <Table>
                <THead>
                  <TR>
                    <TH>Value</TH>
                    <TH>At risk</TH>
                    <TH>Expected</TH>
                    <TH>Significant</TH>
                  </TR>
                </THead>
                <TBody>
                  {result.values.map((v) => (
                    <TR key={v.value}>
                      <TD>{v.value}</TD>
                      <TD>{v.at_risk_count}</TD>
                      <TD>{v.expected_at_risk_count.toFixed(1)}</TD>
                      <TD>{v.is_significant ? "Yes" : "No"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </div>
        <button
          type="button"
          className="text-xs text-muted-foreground underline"
          onClick={() => setDismissed(true)}
        >
          Dismiss
        </button>
      </div>
    </Alert>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/Inventory.render.test.tsx`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/inventory/InsightBanner.tsx frontend/src/pages/Inventory.render.test.tsx
git commit -m "feat: add the auto-surfaced RCA insight banner for the Inventory tab"
```

---

### Task 4: Frontend — `AnalyticsDetail.tsx`

**Files:**
- Create: `frontend/src/pages/inventory/AnalyticsDetail.tsx`
- Modify: `frontend/src/pages/Inventory.render.test.tsx` (append a `describe` block)

**Interfaces:**
- Consumes: `abcLabel`, `xyzLabel`, `whyThisStatusSentence`, `scenarioDeltaSentence`, `type InventoryAnalyticsLine`, `type ScenarioDelta` from `./types` (Task 2). `apiFetch` from `../../api/client`. `Button` from `@/components/ui/button`. `Field` from `@/components/Field`. `Input` from `@/components/ui/input`.
- Produces: `export function AnalyticsDetail({ itemId, line }: { itemId: string; line: InventoryAnalyticsLine })`. Requires a `QueryClientProvider` ancestor (uses `useMutation`).

**Note on "live" updates:** the design doc describes the what-if field as updating "on change." Firing a network request on every keystroke would spam the API and doesn't match how any other field on this page works (Consume/Adjust/Count/Transfer all use an explicit action). This implementation fires the scenario request `onBlur` (leaving the field) instead of on every keystroke — automatic, no submit button, but not a request per character.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/pages/Inventory.render.test.tsx` (add these imports at the top alongside the existing ones, and this `describe` block at the end of the file):

```tsx
// Add to the existing import block at the top of the file:
import { AnalyticsDetail } from "./inventory/AnalyticsDetail";
import type { InventoryAnalyticsLine } from "./inventory/types";
```

```tsx
const ANALYTICS_LINE: InventoryAnalyticsLine = {
  item_id: "i-1",
  reorder_point: 120,
  safety_stock: 40,
  days_of_supply: 12,
  abc_class: "A",
  xyz_class: "X",
  status: "low",
};

describe("AnalyticsDetail", () => {
  function renderDetail() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return renderToString(
      createElement(
        MemoryRouter,
        null,
        createElement(
          QueryClientProvider,
          { client: qc },
          createElement(AnalyticsDetail, { itemId: "i-1", line: ANALYTICS_LINE }),
        ),
      ),
    )
      .split("<!-- -->")
      .join("");
  }

  it("explains why the item has its status in plain language", () => {
    const html = renderDetail();
    expect(html).toContain("12 days of stock left, below your reorder point of 120 units");
    expect(html).toContain("High-value item, steady demand");
  });

  it("hides the raw numbers until requested", () => {
    const html = renderDetail();
    expect(html).not.toContain("Safety stock");
  });

  it("shows a what-if lead time field", () => {
    const html = renderDetail();
    expect(html).toContain("What if lead time were");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Inventory.render.test.tsx`
Expected: FAIL — `Failed to resolve import "./inventory/AnalyticsDetail"`.

- [ ] **Step 3: Implement `frontend/src/pages/inventory/AnalyticsDetail.tsx`**

```tsx
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import {
  abcLabel,
  scenarioDeltaSentence,
  whyThisStatusSentence,
  xyzLabel,
  type InventoryAnalyticsLine,
  type ScenarioDelta,
} from "./types";

interface AnalyticsDetailProps {
  itemId: string;
  line: InventoryAnalyticsLine;
}

export function AnalyticsDetail({ itemId, line }: AnalyticsDetailProps) {
  const [showNumbers, setShowNumbers] = useState(false);
  const [leadTime, setLeadTime] = useState("");

  const scenario = useMutation({
    mutationFn: async (days: number): Promise<ScenarioDelta | undefined> => {
      const res = await apiFetch("/inventory/scenario-compare", {
        method: "POST",
        body: JSON.stringify({ lead_time_overrides: { [itemId]: days } }),
      });
      if (!res.ok) throw new Error("Failed to compute the scenario");
      const body = await res.json();
      return body.deltas[0] as ScenarioDelta | undefined;
    },
  });

  function checkScenario() {
    const days = Number(leadTime);
    if (leadTime !== "" && Number.isFinite(days) && days >= 0) scenario.mutate(days);
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <p className="text-sm font-semibold">Why this status</p>
      <p className="text-sm">{whyThisStatusSentence(line)}</p>
      <Button type="button" variant="ghost" size="sm" onClick={() => setShowNumbers((v) => !v)}>
        {showNumbers ? "Hide the numbers" : "Show the numbers"}
      </Button>
      {showNumbers && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
          <div>
            <dt className="inline font-medium">Days of supply: </dt>
            <dd className="inline">{line.days_of_supply?.toFixed(1) ?? "—"}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Reorder point: </dt>
            <dd className="inline">{line.reorder_point?.toFixed(1) ?? "—"}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Safety stock: </dt>
            <dd className="inline">{line.safety_stock?.toFixed(1) ?? "—"}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Value class: </dt>
            <dd className="inline">{line.abc_class ?? "—"} ({abcLabel(line.abc_class) ?? "unclassified"})</dd>
          </div>
          <div>
            <dt className="inline font-medium">Demand pattern: </dt>
            <dd className="inline">{line.xyz_class ?? "—"} ({xyzLabel(line.xyz_class) ?? "unclassified"})</dd>
          </div>
        </dl>
      )}

      <Field label="What if lead time were (days)" htmlFor="what-if-lead-time">
        <Input
          id="what-if-lead-time"
          type="number"
          min="0"
          step="1"
          value={leadTime}
          onChange={(e) => setLeadTime(e.target.value)}
          onBlur={checkScenario}
        />
      </Field>
      {scenario.isPending && <p className="text-xs text-muted-foreground">Checking…</p>}
      {scenario.data && <p className="text-sm">{scenarioDeltaSentence(scenario.data)}</p>}
      {scenario.isError && <p className="text-sm text-destructive">Couldn't compute that scenario.</p>}
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/Inventory.render.test.tsx`
Expected: all tests PASS (7 total: 4 from Task 3 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/inventory/AnalyticsDetail.tsx frontend/src/pages/Inventory.render.test.tsx
git commit -m "feat: add why-this-status and what-if-lead-time to the inventory row detail"
```

---

### Task 5: Frontend — wire everything into `InventoryPanel`

**Files:**
- Modify: `frontend/src/pages/Inventory.tsx`
- Modify: `frontend/src/pages/Inventory.render.test.tsx` (append integration tests)

**Interfaces:**
- Consumes: `InsightBanner` (Task 3), `AnalyticsDetail` (Task 4), `sortInventoryRows`, `type InventoryAnalyticsLine` (Task 2 — replaces the file's current local `InventoryAnalyticsLine` interface, which only had `item_id`/`reorder_point`/`days_of_supply`/`status`). `useSearchParams`, `Link` from `react-router-dom`.
- Produces: no new exports; `InventoryPanel` behavior changes only.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/pages/Inventory.render.test.tsx` (add `InventoryPanel` to the top-of-file imports, alongside a `useSearchParams`-aware render helper):

```tsx
// Add to the existing import block at the top of the file:
import { InventoryPanel } from "./Inventory";
```

```tsx
// Deliberately seeded with the healthy item first and the stockout item
// second, so the "sorts the most urgent item first" test actually exercises
// sortInventoryRows rather than passing by coincidence of API order.
const BALANCES = [
  {
    item_id: "healthy-c",
    item_sku: "HC",
    item_name: "Cardboard Box",
    unit_of_measure: "ea",
    quantity_on_hand: "500",
    by_location: [],
  },
  {
    item_id: "stockout-a",
    item_sku: "SA",
    item_name: "Steel Rod",
    unit_of_measure: "ea",
    quantity_on_hand: "0",
    by_location: [],
  },
];

const ANALYTICS_LINES = [
  {
    item_id: "stockout-a",
    reorder_point: 50,
    safety_stock: 10,
    days_of_supply: 0,
    abc_class: "A",
    xyz_class: "Z",
    status: "stockout",
  },
  {
    item_id: "healthy-c",
    reorder_point: 20,
    safety_stock: 5,
    days_of_supply: 90,
    abc_class: "C",
    xyz_class: "X",
    status: "healthy",
  },
];

function renderInventoryPanel(seed: (qc: QueryClient) => void, initialPath = "/warehouse") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  return renderToString(
    createElement(
      MemoryRouter,
      { initialEntries: [initialPath] },
      createElement(QueryClientProvider, { client: qc }, createElement(InventoryPanel)),
    ),
  )
    .split("<!-- -->")
    .join("");
}

describe("InventoryPanel integration", () => {
  it("sorts the most urgent, highest-value item first", () => {
    const html = renderInventoryPanel((qc) => {
      qc.setQueryData(["inventory"], BALANCES);
      qc.setQueryData(["inventory-analytics"], ANALYTICS_LINES);
      qc.setQueryData(["locations"], []);
    });
    expect(html.indexOf("Steel Rod")).toBeLessThan(html.indexOf("Cardboard Box"));
  });

  it("auto-selects and expands the row named by ?item= on load", () => {
    const html = renderInventoryPanel(
      (qc) => {
        qc.setQueryData(["inventory"], BALANCES);
        qc.setQueryData(["inventory-analytics"], ANALYTICS_LINES);
        qc.setQueryData(["locations"], []);
        qc.setQueryData(["inventory", "healthy-c"], {
          ...BALANCES[0], // BALANCES[0] is healthy-c (Cardboard Box) - see the seed order note above
          transactions: [],
        });
      },
      "/warehouse?item=healthy-c",
    );
    expect(html).toContain("Why this status");
  });

  it("links to the Planning Grid from the row detail", () => {
    const html = renderInventoryPanel(
      (qc) => {
        qc.setQueryData(["inventory"], BALANCES);
        qc.setQueryData(["inventory-analytics"], ANALYTICS_LINES);
        qc.setQueryData(["locations"], []);
        qc.setQueryData(["inventory", "healthy-c"], {
          ...BALANCES[0], // BALANCES[0] is healthy-c (Cardboard Box) - see the seed order note above
          transactions: [],
        });
      },
      "/warehouse?item=healthy-c",
    );
    expect(html).toContain('href="/planning"');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/Inventory.render.test.tsx`
Expected: all 3 new tests FAIL — "sorts the most urgent item first" fails because `Cardboard Box` (seeded first, no client-side sort exists yet) renders before `Steel Rod`; "auto-selects and expands" and "links to the Planning Grid" fail because neither the `?item=` read nor the "View in Planning" link exist yet.

- [ ] **Step 3: Modify `frontend/src/pages/Inventory.tsx`**

Replace the existing local type/import block (currently lines 1-16 plus the local `InventoryAnalyticsLine` interface at lines 67-72) with:

```tsx
import { FormEvent, useState } from "react";
import { PackageCheck, TrendingDown, TrendingUp, Warehouse } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { StatCard, STAT_TONES } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, inventoryStatusVariant } from "@/lib/status";
import { cn } from "@/lib/utils";
import { AnalyticsDetail } from "./inventory/AnalyticsDetail";
import { InsightBanner } from "./inventory/InsightBanner";
import { sortInventoryRows, type InventoryAnalyticsLine } from "./inventory/types";
```

Delete the now-duplicate local interface (was at lines 67-72):

```tsx
interface InventoryAnalyticsLine {
  item_id: string;
  reorder_point: number | null;
  days_of_supply: number | null;
  status: string;
}
```

Change the `selectedItemId` state initializer (currently `useState<string | null>(null)`) to read the `?item=` query param, and read the analytics list before it's used:

```tsx
export function InventoryPanel() {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [selectedItemId, setSelectedItemId] = useState<string | null>(searchParams.get("item"));
```

(All the other `useState` declarations below it are unchanged.)

Right after the `analyticsByItem` line (currently `const analyticsByItem = new Map(analytics.data?.map((a) => [a.item_id, a]));`), sort the balances before rendering:

```tsx
  const analyticsByItem = new Map(analytics.data?.map((a) => [a.item_id, a]));
  const sortedBalances = balances.data ? sortInventoryRows(balances.data, analyticsByItem) : undefined;
```

Add `<InsightBanner />` as the first child inside the returned `<div>`, before the existing `{error && ...}` block:

```tsx
  return (
    <div>
      <InsightBanner />

      {error && (
```

Replace every use of `balances.data` in the JSX below with `sortedBalances` (three occurrences: the `balances.data && balances.data.length > 0 &&` stat-tile guard, the `balances.data && balances.data.length === 0` empty-state guard, and the `balances.data?.map((bal) => {` table body):

```tsx
      {sortedBalances && sortedBalances.length > 0 && analytics.data && (
```

```tsx
      {sortedBalances && sortedBalances.length === 0 ? (
```

```tsx
            <TBody>
              {sortedBalances?.map((bal) => {
```

Add a "View in Planning" link to the detail card header. Find the existing header block:

```tsx
          <CardHeader>
            <CardTitle>
              {detail.data.item_sku} - {detail.data.item_name}
            </CardTitle>
```

Replace it with:

```tsx
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>
                {detail.data.item_sku} - {detail.data.item_name}
              </span>
              <Link to="/planning" className="text-xs font-normal text-muted-foreground underline">
                View in Planning
              </Link>
            </CardTitle>
```

Finally, render `AnalyticsDetail` at the end of the detail card's `CardContent`, right after the existing transactions `TableWrap` and before the closing `</CardContent>`:

```tsx
            </TableWrap>

            {(() => {
              const line = analyticsByItem.get(detail.data.item_id);
              return line ? <AnalyticsDetail itemId={detail.data.item_id} line={line} /> : null;
            })()}
          </CardContent>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/Inventory.render.test.tsx`
Expected: all tests PASS (10 total).

- [ ] **Step 5: Run the full frontend test suite to confirm nothing else broke**

Run: `cd frontend && npx vitest run`
Expected: all PASS, no regressions in unrelated files.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Inventory.tsx frontend/src/pages/Inventory.render.test.tsx
git commit -m "feat: wire the insight banner, analytics detail, and cross-link into InventoryPanel"
```

---

### Task 6: Frontend — cross-link from `Planning.tsx`

**Files:**
- Modify: `frontend/src/pages/Planning.tsx`
- Modify: `frontend/src/pages/Planning.render.test.tsx`

**Interfaces:**
- Consumes: `Link` (already imported in this file — no new import needed).
- Produces: no new exports; adds a per-row "View in Inventory" link. (No `?skus=` highlighting: per the design doc's §6 correction, `RcaDimensionValue` never returns item IDs, so nothing in this plan ever has data to populate a `skus` param with — building a reader for a param nothing sets would be dead code.)

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/pages/Planning.render.test.tsx`, at the end of the file:

```tsx
describe("cross-link to Inventory", () => {
  it("links each product row to its Warehouse inventory detail", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain('href="/warehouse?item=p-1"');
    expect(html).toContain('href="/warehouse?item=p-2"');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Planning.render.test.tsx`
Expected: the new test FAILS (no `/warehouse?item=` link exists yet).

- [ ] **Step 3: Modify `frontend/src/pages/Planning.tsx`**

In the "Product" cell, add the cross-link next to the existing SKU line. Find:

```tsx
                        <TD>
                          <span className="font-medium">{product.item_name}</span>
                          <span className="block text-xs text-muted-foreground">{product.item_sku}</span>
                        </TD>
```

Replace with:

```tsx
                        <TD>
                          <span className="font-medium">{product.item_name}</span>
                          <span className="block text-xs text-muted-foreground">
                            {product.item_sku}{" "}
                            <Link to={`/warehouse?item=${product.item_id}`} className="underline">
                              View in Inventory
                            </Link>
                          </span>
                        </TD>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/Planning.render.test.tsx`
Expected: all tests PASS, including every pre-existing test in this file (the link is additive; no existing assertion should break).

- [ ] **Step 5: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Planning.tsx frontend/src/pages/Planning.render.test.tsx
git commit -m "feat: cross-link the Planning Grid and Inventory tab"
```

---

## Final verification

- [ ] **Backend:** `cd backend && python -m pytest -q` — full suite passes.
- [ ] **Frontend:** `cd frontend && npx vitest run` — full suite passes.
- [ ] **Manual smoke check** (per this project's convention of verifying UI changes in a running app before calling the work done): start the backend and frontend dev servers, open `/warehouse`, confirm the Inventory tab shows the insight banner (or nothing, if no org data triggers a significant finding), expand a row and confirm "Why this status" and the what-if field render, and confirm the "View in Planning" / "View in Inventory" links navigate correctly in both directions.
