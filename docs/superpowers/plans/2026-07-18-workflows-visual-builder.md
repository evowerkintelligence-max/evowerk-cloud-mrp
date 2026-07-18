# Workflows Visual Pipeline Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Workflows' JSON-textarea/checkbox builder with an interactive React Flow canvas, typed per-step config forms, and live run status on canvas nodes — on top of the existing DAG engine, with no new step types.

**Architecture:** `Workflow.steps` (JSON column) gains an optional `position` key per step (UI metadata only, ignored by DAG validation/execution). The frontend gets three new pure-logic modules (`stepLayout.ts`, `stepConfig.ts`) and components (`FilterRowEditor.tsx`, `WorkflowStepNode.tsx`) that `WorkflowBuilder.tsx` composes into a canvas-driven builder. A `continue`-failure-policy bug in the runner is fixed alongside, since it's in a file this work already touches.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy (backend, unchanged shape), React + TypeScript + `@tanstack/react-query` + new `@xyflow/react` dependency (frontend canvas).

## Global Constraints

- Backend tests run from `backend/`: `pytest tests/<file> -v`.
- Frontend typecheck: `cd frontend && npx tsc -b`. Frontend unit tests: `cd frontend && npx vitest run <file>`.
- No new backend API endpoints or DB migration — `position` rides inside the existing `Workflow.steps` JSON column.
- No new step types, scheduling, or triggers in this phase (see spec's Out of scope).
- Match existing code style exactly: no comments explaining *what* code does, only non-obvious *why* (see e.g. `workflow_runner.py`'s existing docstrings/comments for the house style).

---

### Task 1: Backend — `position` field on `WorkflowStep`

**Files:**
- Modify: `backend/app/services/workflow_dag.py:27-39`
- Test: `backend/tests/test_workflow_dag.py` (new)

**Interfaces:**
- Produces: `StepPosition` (Pydantic model, fields `x: float`, `y: float`), `WorkflowStep.position: StepPosition | None`. Both consumed by Task 6's frontend payload (which sends `position` as `{x, y}` inside each step dict) — no other backend task depends on this.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_workflow_dag.py`:

```python
from __future__ import annotations

import pytest

from app.services.workflow_dag import DagError, StepPosition, validate_dag


def test_validate_dag_preserves_position_when_present():
    steps = validate_dag(
        [{"id": "a", "name": "A", "type": "filter", "config": {}, "position": {"x": 120.5, "y": 40}}]
    )
    assert steps[0].position == StepPosition(x=120.5, y=40)


def test_validate_dag_position_defaults_to_none_when_absent():
    steps = validate_dag([{"id": "a", "name": "A", "type": "filter", "config": {}}])
    assert steps[0].position is None


def test_validate_dag_rejects_non_numeric_position():
    with pytest.raises(DagError):
        validate_dag(
            [{"id": "a", "name": "A", "type": "filter", "config": {}, "position": {"x": "abc", "y": 1}}]
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_workflow_dag.py -v`
Expected: FAIL — `ImportError: cannot import name 'StepPosition' from 'app.services.workflow_dag'`

- [ ] **Step 3: Add `StepPosition` and the `position` field**

In `backend/app/services/workflow_dag.py`, replace the `WorkflowStep` class (current lines 27-39):

```python
class WorkflowStep(BaseModel):
    id: str
    name: str
    type: str
    depends_on: list[str] = []
    config: dict = {}

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in STEP_TYPES:
            raise ValueError(f"Unknown step type: {v!r}. Must be one of {STEP_TYPES}")
        return v
```

with:

```python
class StepPosition(BaseModel):
    """Canvas coordinates for a step - UI metadata only, never read by
    validate_dag/topo_layers/descendants below."""

    x: float
    y: float


class WorkflowStep(BaseModel):
    id: str
    name: str
    type: str
    depends_on: list[str] = []
    config: dict = {}
    position: StepPosition | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in STEP_TYPES:
            raise ValueError(f"Unknown step type: {v!r}. Must be one of {STEP_TYPES}")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_workflow_dag.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/workflow_dag.py backend/tests/test_workflow_dag.py
git commit -m "feat(workflows): add optional position field to WorkflowStep"
```

---

### Task 2: Backend — fix `continue` failure-policy semantics

**Files:**
- Modify: `backend/app/services/workflow_runner.py:79-89`
- Test: `backend/tests/test_workflows.py` (append)

**Interfaces:**
- Consumes: nothing new (bug fix inside `_execute`, same signature).
- Produces: no new interface — behavioral fix only. Not depended on by any other task.

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_workflows.py`, insert this new test between `test_skip_dependents_marks_downstream_skipped_not_failed` (ends at line 217) and `test_run_history_lists_across_workflows_with_status_filter_and_org_scoping` (starts at line 220):

```python
async def test_continue_runs_downstream_steps_despite_upstream_failure(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    body = {
        "name": "Continue anyway",
        "failure_policy": "continue",
        "steps": [
            # Missing config.dataset_id -> StepError, this step fails.
            {"id": "bad_sql", "name": "Bad SQL", "type": "sql", "config": {"sql": "SELECT 1"}},
            {
                "id": "after",
                "name": "Attempted anyway",
                "type": "export_result",
                "depends_on": ["bad_sql"],
                "config": {"input_step": "bad_sql", "format": "csv"},
            },
            {"id": "independent", "name": "Independent", "type": "load_dataset", "config": {"dataset_id": dataset_id}},
        ],
    }
    created = await client.post("/workflows", headers=headers, json=body)
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    ran = await client.post(f"/workflows/{workflow_id}/run", headers=headers)
    run_id = ran.json()["id"]

    detail = await client.get(f"/workflows/{workflow_id}/runs/{run_id}", headers=headers)
    result = detail.json()
    assert result["status"] == "partial"

    events_by_step: dict[str, list[str]] = {}
    for e in result["events"]:
        if e["step_id"]:
            events_by_step.setdefault(e["step_id"], []).append(e["event_type"])
    # "after" was actually attempted (not pre-emptively skipped) - it failed on
    # its own missing upstream data, proving continue really does try every
    # step regardless, per its own UI description.
    assert events_by_step["after"] == ["node_started", "node_finished"]
    assert "node_finished" in events_by_step["independent"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_workflows.py::test_continue_runs_downstream_steps_despite_upstream_failure -v`
Expected: FAIL — `assert events_by_step["after"] == ["node_started", "node_finished"]` fails because `"after"` actually got `["node_skipped"]` under the current (buggy) behavior.

- [ ] **Step 3: Fix the skip check to only apply outside `continue`**

In `backend/app/services/workflow_runner.py`, replace the layer loop's skip check (current lines 79-89):

```python
    for layer in layers:
        runnable = []
        for step in layer:
            if set(step.depends_on) & (failed_ids | skipped_ids):
                skipped_ids.add(step.id)
                await _emit(db, org_id, run_id, "node_skipped", step_id=step.id)
                continue
            runnable.append(step)

        if not runnable:
            continue
```

with:

```python
    for layer in layers:
        runnable = []
        for step in layer:
            # "continue" tries every step regardless of an upstream failure - a
            # step that actually needs the missing output fails on its own
            # (e.g. StepError from _upstream_rows), it isn't pre-emptively
            # skipped here. fail_fast never reaches a later layer anyway (it
            # breaks below before one starts); skip_dependents keeps this
            # per-layer skip as before.
            if workflow.failure_policy != "continue" and set(step.depends_on) & (failed_ids | skipped_ids):
                skipped_ids.add(step.id)
                await _emit(db, org_id, run_id, "node_skipped", step_id=step.id)
                continue
            runnable.append(step)

        if not runnable:
            continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_workflows.py -v`
Expected: PASS (all tests in the file, including the new one and the pre-existing `fail_fast`/`skip_dependents` tests unaffected)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/workflow_runner.py backend/tests/test_workflows.py
git commit -m "fix(workflows): continue failure policy now actually runs downstream steps"
```

---

### Task 3: Frontend — `stepLayout.ts` (auto-layout + cycle-check helpers)

**Files:**
- Create: `frontend/src/pages/workflows/stepLayout.ts`
- Test: `frontend/src/pages/workflows/stepLayout.test.ts`

**Interfaces:**
- Produces: `autoLayout<T extends LayoutStep>(steps: T[]): Record<string, {x: number; y: number}>`, `wouldCreateCycle(steps: LayoutStep[], targetId: string, sourceId: string): boolean`, where `LayoutStep = {id: string; depends_on: string[]}`. Consumed by Task 6 (`WorkflowBuilder.tsx`'s load-workflow auto-layout fallback and canvas `onConnect` cycle guard).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/pages/workflows/stepLayout.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { autoLayout, wouldCreateCycle } from "./stepLayout";

describe("autoLayout", () => {
  it("places a step with no dependencies in column 0", () => {
    const pos = autoLayout([{ id: "a", depends_on: [] }]);
    expect(pos.a).toEqual({ x: 0, y: 0 });
  });

  it("places a dependent step to the right of its dependency", () => {
    const pos = autoLayout([
      { id: "a", depends_on: [] },
      { id: "b", depends_on: ["a"] },
    ]);
    expect(pos.b.x).toBeGreaterThan(pos.a.x);
  });

  it("stacks independent steps in the same column at different rows", () => {
    const pos = autoLayout([
      { id: "a", depends_on: [] },
      { id: "b", depends_on: [] },
    ]);
    expect(pos.a.x).toBe(pos.b.x);
    expect(pos.a.y).not.toBe(pos.b.y);
  });

  it("places a step two levels deep two columns to the right", () => {
    const pos = autoLayout([
      { id: "a", depends_on: [] },
      { id: "b", depends_on: ["a"] },
      { id: "c", depends_on: ["b"] },
    ]);
    expect(pos.c.x).toBeGreaterThan(pos.b.x);
    expect(pos.b.x).toBeGreaterThan(pos.a.x);
  });

  it("does not infinite-loop on a cyclic input", () => {
    const pos = autoLayout([
      { id: "a", depends_on: ["b"] },
      { id: "b", depends_on: ["a"] },
    ]);
    expect(pos.a).toBeDefined();
    expect(pos.b).toBeDefined();
  });
});

describe("wouldCreateCycle", () => {
  it("is false for connecting two unrelated steps", () => {
    const steps = [
      { id: "a", depends_on: [] },
      { id: "b", depends_on: [] },
    ];
    expect(wouldCreateCycle(steps, "b", "a")).toBe(false);
  });

  it("is true when the source already (transitively) depends on the target", () => {
    // a <- b <- c (c depends_on b, b depends_on a). Connecting a to depend on
    // c would close the loop a -> c -> b -> a.
    const steps = [
      { id: "a", depends_on: [] },
      { id: "b", depends_on: ["a"] },
      { id: "c", depends_on: ["b"] },
    ];
    expect(wouldCreateCycle(steps, "a", "c")).toBe(true);
  });

  it("is false for a direct dependency that does not close a loop", () => {
    const steps = [
      { id: "a", depends_on: [] },
      { id: "b", depends_on: [] },
    ];
    expect(wouldCreateCycle(steps, "b", "a")).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/workflows/stepLayout.test.ts`
Expected: FAIL — cannot find module `./stepLayout`

- [ ] **Step 3: Implement `stepLayout.ts`**

Create `frontend/src/pages/workflows/stepLayout.ts`:

```ts
export interface LayoutStep {
  id: string;
  depends_on: string[];
}

const COLUMN_WIDTH = 260;
const ROW_HEIGHT = 120;

// Layered auto-layout: a step with no unresolved dependency goes in column 0,
// each subsequent column holds steps whose dependencies are all already
// placed in an earlier column - the same grouping workflow_dag.topo_layers
// computes server-side (Kahn's algorithm), ported client-side so the canvas
// can lay out a workflow saved before per-step positions existed.
export function autoLayout<T extends LayoutStep>(steps: T[]): Record<string, { x: number; y: number }> {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const columnOf = new Map<string, number>();

  function columnFor(id: string, seen: Set<string>): number {
    if (columnOf.has(id)) return columnOf.get(id)!;
    if (seen.has(id)) return 0; // cycle - server-side validation rejects it, just don't infinite-loop here
    seen.add(id);
    const deps = byId.get(id)?.depends_on ?? [];
    const col = deps.length === 0 ? 0 : 1 + Math.max(...deps.map((d) => (byId.has(d) ? columnFor(d, seen) : 0)));
    columnOf.set(id, col);
    return col;
  }

  const rowInColumn: Record<number, number> = {};
  const positions: Record<string, { x: number; y: number }> = {};
  for (const step of steps) {
    const col = columnFor(step.id, new Set());
    const row = rowInColumn[col] ?? 0;
    rowInColumn[col] = row + 1;
    positions[step.id] = { x: col * COLUMN_WIDTH, y: row * ROW_HEIGHT };
  }
  return positions;
}

// True if sourceId already (transitively) depends on targetId - i.e. adding
// "targetId depends_on sourceId" would close a loop back to targetId.
export function wouldCreateCycle(steps: LayoutStep[], targetId: string, sourceId: string): boolean {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const seen = new Set<string>();
  function dependsOn(fromId: string, needle: string): boolean {
    if (seen.has(fromId)) return false;
    seen.add(fromId);
    const deps = byId.get(fromId)?.depends_on ?? [];
    return deps.some((depId) => depId === needle || dependsOn(depId, needle));
  }
  return dependsOn(sourceId, targetId);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/workflows/stepLayout.test.ts`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/workflows/stepLayout.ts frontend/src/pages/workflows/stepLayout.test.ts
git commit -m "feat(workflows): add client-side auto-layout and cycle-check helpers"
```

---

### Task 4: Frontend — `stepConfig.ts` (typed draft ↔ backend JSON conversion)

**Files:**
- Create: `frontend/src/pages/workflows/stepConfig.ts`
- Test: `frontend/src/pages/workflows/stepConfig.test.ts`

**Interfaces:**
- Consumes: nothing (pure, standalone).
- Produces: `StepType` (union of the 8 type strings), `FilterOp`, `AggFn`, `FilterRow`, `SelectRow`, `StepConfigDraft`, `blankConfigDraft(): StepConfigDraft`, `draftToConfig(type: StepType, draft: StepConfigDraft): Record<string, unknown>`, `configToDraft(type: StepType, config: Record<string, unknown>): StepConfigDraft`. Consumed by Task 5 (`FilterRowEditor.tsx`'s row types) and Task 6 (`WorkflowBuilder.tsx`'s save payload / load-workflow parsing / config form).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/pages/workflows/stepConfig.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { blankConfigDraft, draftToConfig, configToDraft } from "./stepConfig";

describe("draftToConfig", () => {
  it("builds a load_dataset config", () => {
    const draft = { ...blankConfigDraft(), datasetId: "ds-1", limit: "250" };
    expect(draftToConfig("load_dataset", draft)).toEqual({ dataset_id: "ds-1", limit: 250 });
  });

  it("builds a filter config reading from a dataset", () => {
    const draft = {
      ...blankConfigDraft(),
      source: "dataset" as const,
      datasetId: "ds-1",
      limit: "500",
      filters: [{ column: "region", op: "eq" as const, value: "west" }],
    };
    expect(draftToConfig("filter", draft)).toEqual({
      dataset_id: "ds-1",
      filters: [{ column: "region", op: "eq", value: "west" }],
      limit: 500,
    });
  });

  it("builds a filter config reading from an upstream step, splitting 'in' values on comma", () => {
    const draft = {
      ...blankConfigDraft(),
      source: "upstream" as const,
      inputStep: "load",
      limit: "500",
      filters: [{ column: "sku", op: "in" as const, value: "A-1, A-2 ,A-3" }],
    };
    expect(draftToConfig("filter", draft)).toEqual({
      input_step: "load",
      filters: [{ column: "sku", op: "in", value: ["A-1", "A-2", "A-3"] }],
      limit: 500,
    });
  });

  it("builds an aggregate config with select rows", () => {
    const draft = {
      ...blankConfigDraft(),
      source: "dataset" as const,
      datasetId: "ds-1",
      limit: "500",
      groupBy: ["region"],
      select: [{ column: "qty", agg: "sum" as const, alias: "total_qty" }],
    };
    expect(draftToConfig("aggregate", draft)).toEqual({
      dataset_id: "ds-1",
      group_by: ["region"],
      select: [{ column: "qty", agg: "sum", alias: "total_qty" }],
      limit: 500,
    });
  });

  it("builds an export_result config", () => {
    const draft = { ...blankConfigDraft(), inputStep: "filter_west", format: "json" as const };
    expect(draftToConfig("export_result", draft)).toEqual({ input_step: "filter_west", format: "json" });
  });

  it("builds an import_csv config", () => {
    const draft = { ...blankConfigDraft(), contentBase64: "Zm9v", filename: "stock.csv", name: "My import" };
    expect(draftToConfig("import_csv", draft)).toEqual({
      content_base64: "Zm9v",
      filename: "stock.csv",
      name: "My import",
    });
  });
});

describe("configToDraft", () => {
  it("round-trips a dataset-sourced filter config", () => {
    const config = {
      dataset_id: "ds-1",
      filters: [{ column: "region", op: "eq", value: "west" }],
      limit: 500,
    };
    const draft = configToDraft("filter", config);
    expect(draft.source).toBe("dataset");
    expect(draft.datasetId).toBe("ds-1");
    expect(draft.filters).toEqual([{ column: "region", op: "eq", value: "west" }]);
    expect(draftToConfig("filter", draft)).toEqual(config);
  });

  it("round-trips an upstream-sourced filter config with an 'in' list value", () => {
    const config = { input_step: "load", filters: [{ column: "sku", op: "in", value: ["A-1", "A-2"] }], limit: 500 };
    const draft = configToDraft("filter", config);
    expect(draft.source).toBe("upstream");
    expect(draft.inputStep).toBe("load");
    expect(draft.filters).toEqual([{ column: "sku", op: "in", value: "A-1, A-2" }]);
    expect(draftToConfig("filter", draft)).toEqual(config);
  });

  it("defaults an unknown aggregation function to sum when parsing", () => {
    const draft = configToDraft("aggregate", { dataset_id: "ds-1", group_by: [], select: [{ column: "qty" }], limit: 500 });
    expect(draft.select).toEqual([{ column: "qty", agg: "sum", alias: "" }]);
  });

  it("returns a blank draft for an unrecognized step type", () => {
    expect(configToDraft("sql", {})).toEqual({ ...blankConfigDraft(), datasetId: "", sql: "", limit: "500" });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/workflows/stepConfig.test.ts`
Expected: FAIL — cannot find module `./stepConfig`

- [ ] **Step 3: Implement `stepConfig.ts`**

Create `frontend/src/pages/workflows/stepConfig.ts`:

```ts
export const STEP_TYPES = [
  "sql", "filter", "aggregate", "load_dataset",
  "export_result", "import_csv", "import_excel", "import_json",
] as const;
export type StepType = (typeof STEP_TYPES)[number];

export const FILTER_OPS = ["eq", "neq", "contains", "in", "gt", "gte", "lt", "lte"] as const;
export type FilterOp = (typeof FILTER_OPS)[number];

export const AGG_FNS = ["sum", "avg", "min", "max", "count"] as const;
export type AggFn = (typeof AGG_FNS)[number];

export interface FilterRow {
  column: string;
  op: FilterOp;
  value: string; // comma-separated when op === "in"
}

export interface SelectRow {
  column: string;
  agg: AggFn;
  alias: string;
}

export interface StepConfigDraft {
  source: "dataset" | "upstream"; // filter/aggregate only
  datasetId: string; // load_dataset, sql, filter(dataset), aggregate(dataset)
  inputStep: string; // filter(upstream), aggregate(upstream), export_result
  sql: string; // sql
  limit: string; // load_dataset, sql, filter, aggregate - numeric string
  filters: FilterRow[]; // filter
  groupBy: string[]; // aggregate
  select: SelectRow[]; // aggregate
  format: "csv" | "json"; // export_result
  filename: string; // import_*
  contentBase64: string; // import_*
  name: string; // import_* (dataset name override)
}

export function blankConfigDraft(): StepConfigDraft {
  return {
    source: "dataset", datasetId: "", inputStep: "", sql: "", limit: "500",
    filters: [], groupBy: [], select: [], format: "csv", filename: "", contentBase64: "", name: "",
  };
}

function filterRowToQueryFilter(row: FilterRow): { column: string; op: FilterOp; value: string | string[] } {
  if (row.op === "in") {
    return { column: row.column, op: row.op, value: row.value.split(",").map((v) => v.trim()).filter(Boolean) };
  }
  return { column: row.column, op: row.op, value: row.value };
}

function queryFilterToRow(f: { column: string; op: string; value: unknown }): FilterRow {
  const op = (FILTER_OPS as readonly string[]).includes(f.op) ? (f.op as FilterOp) : "eq";
  const value = Array.isArray(f.value) ? f.value.join(", ") : f.value == null ? "" : String(f.value);
  return { column: f.column, op, value };
}

function selectRowToSelectField(row: SelectRow): { column: string; agg: AggFn; alias?: string } {
  return { column: row.column, agg: row.agg, alias: row.alias || undefined };
}

function selectFieldToRow(s: { column: string; agg?: string | null; alias?: string | null }): SelectRow {
  const agg = (AGG_FNS as readonly string[]).includes(s.agg ?? "") ? (s.agg as AggFn) : "sum";
  return { column: s.column, agg, alias: s.alias ?? "" };
}

function numOrUndefined(s: string): number | undefined {
  const n = Number(s);
  return s.trim() !== "" && Number.isFinite(n) ? n : undefined;
}

export function draftToConfig(type: StepType, d: StepConfigDraft): Record<string, unknown> {
  const strip = (o: Record<string, unknown>) =>
    Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));

  switch (type) {
    case "load_dataset":
      return strip({ dataset_id: d.datasetId, limit: numOrUndefined(d.limit) });
    case "sql":
      return strip({ dataset_id: d.datasetId, sql: d.sql, limit: numOrUndefined(d.limit) });
    case "filter": {
      const base = { filters: d.filters.map(filterRowToQueryFilter), limit: numOrUndefined(d.limit) };
      return strip(d.source === "upstream" ? { ...base, input_step: d.inputStep } : { ...base, dataset_id: d.datasetId });
    }
    case "aggregate": {
      const base = {
        group_by: d.groupBy,
        select: d.select.map(selectRowToSelectField),
        limit: numOrUndefined(d.limit),
      };
      return strip(d.source === "upstream" ? { ...base, input_step: d.inputStep } : { ...base, dataset_id: d.datasetId });
    }
    case "import_csv":
    case "import_excel":
    case "import_json":
      return strip({ content_base64: d.contentBase64, filename: d.filename, name: d.name || undefined });
    case "export_result":
      return { input_step: d.inputStep, format: d.format };
    default:
      return {};
  }
}

export function configToDraft(type: StepType, config: Record<string, unknown>): StepConfigDraft {
  const draft = blankConfigDraft();
  const str = (v: unknown) => (v == null ? "" : String(v));

  switch (type) {
    case "load_dataset":
      return { ...draft, datasetId: str(config.dataset_id), limit: str(config.limit ?? "500") };
    case "sql":
      return { ...draft, datasetId: str(config.dataset_id), sql: str(config.sql), limit: str(config.limit ?? "500") };
    case "filter":
    case "aggregate": {
      const source: StepConfigDraft["source"] = config.input_step ? "upstream" : "dataset";
      const filters = Array.isArray(config.filters)
        ? config.filters.map((f) => queryFilterToRow(f as { column: string; op: string; value: unknown }))
        : [];
      const select = Array.isArray(config.select)
        ? config.select.map((s) => selectFieldToRow(s as { column: string; agg?: string | null; alias?: string | null }))
        : [];
      const groupBy = Array.isArray(config.group_by) ? (config.group_by as string[]) : [];
      return {
        ...draft, source, datasetId: str(config.dataset_id), inputStep: str(config.input_step),
        limit: str(config.limit ?? "500"), filters, select, groupBy,
      };
    }
    case "import_csv":
    case "import_excel":
    case "import_json":
      return { ...draft, contentBase64: str(config.content_base64), filename: str(config.filename), name: str(config.name) };
    case "export_result":
      return { ...draft, inputStep: str(config.input_step), format: (config.format as "csv" | "json") ?? "csv" };
    default:
      return draft;
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/workflows/stepConfig.test.ts`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/workflows/stepConfig.ts frontend/src/pages/workflows/stepConfig.test.ts
git commit -m "feat(workflows): add typed step-config draft <-> backend JSON conversion"
```

---

### Task 5: Frontend — `FilterRowEditor.tsx` (shared row-list UI)

**Files:**
- Create: `frontend/src/pages/workflows/FilterRowEditor.tsx`

**Interfaces:**
- Consumes: `FilterRow`, `SelectRow`, `FilterOp`, `AggFn`, `FILTER_OPS`, `AGG_FNS` from `./stepConfig` (Task 4).
- Produces: `FilterRowsEditor({rows, columns, onChange})`, `SelectRowsEditor({rows, columns, onChange})`. Consumed by Task 6 (`WorkflowBuilder.tsx`'s filter/aggregate config forms).

No automated test for this task — it's a thin presentational wrapper around already-tested pure logic (Task 4), and this codebase has no React component-rendering test precedent (confirmed: `frontend/src/lib/useCountUp.test.ts` is the only existing frontend test, and it tests a pure function, not a rendered component). It's verified visually in Task 9.

- [ ] **Step 1: Implement `FilterRowEditor.tsx`**

Create `frontend/src/pages/workflows/FilterRowEditor.tsx`:

```tsx
import { X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { AGG_FNS, FILTER_OPS, type AggFn, type FilterOp, type FilterRow, type SelectRow } from "./stepConfig";

export function FilterRowsEditor({
  rows, columns, onChange,
}: {
  rows: FilterRow[];
  columns: string[];
  onChange: (rows: FilterRow[]) => void;
}) {
  function update(i: number, patch: Partial<FilterRow>) {
    onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function remove(i: number) {
    onChange(rows.filter((_, idx) => idx !== i));
  }
  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <Select className="w-32" value={row.column} onChange={(e) => update(i, { column: e.target.value })}>
            <option value="">column…</option>
            {columns.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
          <Select className="w-28" value={row.op} onChange={(e) => update(i, { op: e.target.value as FilterOp })}>
            {FILTER_OPS.map((op) => (
              <option key={op} value={op}>{op}</option>
            ))}
          </Select>
          <Input
            className="flex-1"
            value={row.value}
            placeholder={row.op === "in" ? "comma, separated, values" : "value"}
            onChange={(e) => update(i, { value: e.target.value })}
          />
          <Button variant="ghost" size="icon" aria-label="Remove filter" onClick={() => remove(i)}>
            <X className="size-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="secondary"
        size="sm"
        onClick={() => onChange([...rows, { column: columns[0] ?? "", op: "eq", value: "" }])}
      >
        + Add filter
      </Button>
    </div>
  );
}

export function SelectRowsEditor({
  rows, columns, onChange,
}: {
  rows: SelectRow[];
  columns: string[];
  onChange: (rows: SelectRow[]) => void;
}) {
  function update(i: number, patch: Partial<SelectRow>) {
    onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function remove(i: number) {
    onChange(rows.filter((_, idx) => idx !== i));
  }
  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <Select className="w-32" value={row.column} onChange={(e) => update(i, { column: e.target.value })}>
            <option value="">column…</option>
            {columns.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
          <Select className="w-24" value={row.agg} onChange={(e) => update(i, { agg: e.target.value as AggFn })}>
            {AGG_FNS.map((agg) => (
              <option key={agg} value={agg}>{agg}</option>
            ))}
          </Select>
          <Input
            className="flex-1"
            value={row.alias}
            placeholder="alias (optional)"
            onChange={(e) => update(i, { alias: e.target.value })}
          />
          <Button variant="ghost" size="icon" aria-label="Remove aggregation" onClick={() => remove(i)}>
            <X className="size-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="secondary"
        size="sm"
        onClick={() => onChange([...rows, { column: columns[0] ?? "", agg: "sum", alias: "" }])}
      >
        + Add aggregation
      </Button>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no new errors (this file isn't imported anywhere yet, but must still typecheck standalone)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/workflows/FilterRowEditor.tsx
git commit -m "feat(workflows): add shared filter/aggregate row-list editor"
```

---

### Task 6: Frontend — canvas + typed config forms (`WorkflowBuilder.tsx` rewrite)

**Files:**
- Create: `frontend/src/pages/workflows/WorkflowStepNode.tsx`
- Modify: `frontend/package.json` (add `@xyflow/react`)
- Modify: `frontend/src/pages/workflows/WorkflowBuilder.tsx` (full rewrite)

**Interfaces:**
- Consumes: `autoLayout`, `wouldCreateCycle` (Task 3); `STEP_TYPES`, `StepType`, `StepConfigDraft`, `blankConfigDraft`, `draftToConfig`, `configToDraft` (Task 4); `FilterRowsEditor`, `SelectRowsEditor` (Task 5); `SqlCodeEditor` (existing, `frontend/src/components/SqlCodeEditor.tsx`); `useDatasets`, `Dataset` (existing, `frontend/src/pages/analytics/shared.tsx`).
- Produces: `WorkflowNodeData`, `RunNodeStatus` (from `WorkflowStepNode.tsx`) — consumed by Task 7, which adds live status without touching this task's node component signature.

- [ ] **Step 1: Add the `@xyflow/react` dependency**

In `frontend/package.json`, add one line to `dependencies` (alphabetically after `@uiw/react-codemirror`):

```diff
     "@tanstack/react-query": "^5.59.0",
     "@uiw/react-codemirror": "^4.25.11",
+    "@xyflow/react": "^12.3.0",
     "class-variance-authority": "^0.7.1",
```

Run: `cd frontend && npm install`
Expected: `@xyflow/react` added to `node_modules` and `package-lock.json` updated.

- [ ] **Step 2: Create the custom canvas node component**

Create `frontend/src/pages/workflows/WorkflowStepNode.tsx`:

```tsx
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/utils";

export type RunNodeStatus = "idle" | "running" | "success" | "failed" | "skipped";

export interface WorkflowNodeData {
  name: string;
  type: string;
  status: RunNodeStatus;
  error?: string;
  [key: string]: unknown;
}

const STATUS_BORDER: Record<RunNodeStatus, string> = {
  idle: "border-border",
  running: "border-accent animate-pulse",
  success: "border-success",
  failed: "border-destructive",
  skipped: "border-border opacity-50",
};

export function WorkflowStepNode({ data, selected }: NodeProps & { data: WorkflowNodeData }) {
  return (
    <div
      className={cn(
        "min-w-[10rem] rounded-md border-2 bg-popover px-3 py-2 shadow-sm",
        STATUS_BORDER[data.status],
        selected && "ring-2 ring-ring/50",
      )}
      title={data.error}
    >
      <Handle type="target" position={Position.Left} />
      <div className="text-xs font-medium text-muted-foreground">{data.type}</div>
      <div className="truncate text-sm font-medium">{data.name || "(unnamed step)"}</div>
      {data.error && <div className="mt-1 truncate text-xs text-destructive">{data.error}</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
```

- [ ] **Step 3: Rewrite `WorkflowBuilder.tsx`**

Replace the entire contents of `frontend/src/pages/workflows/WorkflowBuilder.tsx` with:

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReactFlow, Background, Controls, type Node, type Edge, type OnConnect, type OnNodesChange, type OnEdgesDelete,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { apiFetch } from "../../api/client";
import { useDatasets, type Dataset } from "../analytics/shared";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { SqlCodeEditor } from "@/components/SqlCodeEditor";
import { FilterRowsEditor, SelectRowsEditor } from "./FilterRowEditor";
import { autoLayout, wouldCreateCycle } from "./stepLayout";
import {
  STEP_TYPES, blankConfigDraft, configToDraft, draftToConfig, type StepConfigDraft, type StepType,
} from "./stepConfig";
import { WorkflowStepNode, type WorkflowNodeData, type RunNodeStatus } from "./WorkflowStepNode";

const nodeTypes = { workflowStep: WorkflowStepNode };

const FAILURE_POLICIES = ["fail_fast", "continue", "skip_dependents"] as const;

interface StepDraft {
  key: string; // stable React/canvas node id - step.id is user-editable, so it can't double as one
  id: string;
  name: string;
  type: StepType;
  depends_on: string[];
  position: { x: number; y: number };
  config: StepConfigDraft;
}

interface WorkflowDetail {
  id: string;
  name: string;
  description: string | null;
  steps: {
    id: string; name: string; type: string; depends_on: string[]; config: Record<string, unknown>;
    position?: { x: number; y: number };
  }[];
  failure_policy: string;
}

interface RunSummary {
  id: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

interface RunEvent {
  id: string;
  step_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

interface RunDetail extends RunSummary {
  events: RunEvent[];
}

function randomSuffix(): string {
  return Math.random().toString(36).slice(2, 8);
}

function newStep(existingCount: number): StepDraft {
  const id = `step_${randomSuffix()}`;
  return {
    key: id, id, name: "", type: "load_dataset", depends_on: [],
    position: { x: 0, y: existingCount * 140 },
    config: blankConfigDraft(),
  };
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

const STATUS_VARIANT: Record<string, "neutral" | "success" | "danger" | "warning" | "info"> = {
  queued: "neutral", running: "info", success: "success", failed: "danger", partial: "warning",
};

export function WorkflowBuilder() {
  const { id } = useParams();
  const isNew = !id || id === "new";
  const navigate = useNavigate();
  const qc = useQueryClient();
  const datasets = useDatasets();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [failurePolicy, setFailurePolicy] = useState<(typeof FAILURE_POLICIES)[number]>("fail_fast");
  const [steps, setSteps] = useState<StepDraft[]>([newStep(0)]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  const workflow = useQuery<WorkflowDetail>({
    queryKey: ["workflow", id],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${id}`);
      if (!res.ok) throw new Error("Failed to load workflow");
      return res.json();
    },
    enabled: !isNew,
  });

  useEffect(() => {
    if (!workflow.data) return;
    setName(workflow.data.name);
    setDescription(workflow.data.description ?? "");
    setFailurePolicy(workflow.data.failure_policy as (typeof FAILURE_POLICIES)[number]);
    const needsLayout = workflow.data.steps.some((s) => !s.position);
    const layout = needsLayout ? autoLayout(workflow.data.steps) : {};
    setSteps(
      workflow.data.steps.map((s) => ({
        key: `step_${randomSuffix()}`,
        id: s.id,
        name: s.name,
        type: s.type as StepType,
        depends_on: s.depends_on,
        position: s.position ?? layout[s.id] ?? { x: 0, y: 0 },
        config: configToDraft(s.type as StepType, s.config),
      })),
    );
  }, [workflow.data]);

  const runs = useQuery<RunSummary[]>({
    queryKey: ["workflow-runs", id],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${id}/runs`);
      if (!res.ok) throw new Error("Failed to load runs");
      return res.json();
    },
    enabled: !isNew,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 1500 : false,
  });

  const runDetail = useQuery<RunDetail>({
    queryKey: ["workflow-run", id, expandedRun],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${id}/runs/${expandedRun}`);
      if (!res.ok) throw new Error("Failed to load run");
      return res.json();
    },
    enabled: !isNew && !!expandedRun,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1500 : false;
    },
  });

  function buildPayload() {
    return {
      name,
      description: description || null,
      steps: steps.map((s) => ({
        id: s.id, name: s.name || s.id, type: s.type, depends_on: s.depends_on,
        config: draftToConfig(s.type, s.config), position: s.position,
      })),
      failure_policy: failurePolicy,
    };
  }

  const save = useMutation({
    mutationFn: async () => {
      const body = buildPayload();
      const res = await apiFetch(isNew ? "/workflows" : `/workflows/${id}`, {
        method: isNew ? "POST" : "PUT",
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Save failed");
      return res.json() as Promise<WorkflowDetail>;
    },
    onSuccess: (w) => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["workflows"] });
      if (isNew) navigate(`/workflows/${w.id}`, { replace: true });
      else qc.invalidateQueries({ queryKey: ["workflow", id] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const run = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/workflows/${id}/run`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Run failed to start");
      return res.json() as Promise<RunSummary>;
    },
    onSuccess: (r) => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["workflow-runs", id] });
      setExpandedRun(r.id);
    },
    onError: (e: Error) => setError(e.message),
  });

  function removeStep(key: string) {
    setSteps((prev) => {
      const removed = prev.find((s) => s.key === key);
      if (!removed) return prev;
      return prev.filter((s) => s.key !== key).map((s) => ({ ...s, depends_on: s.depends_on.filter((d) => d !== removed.id) }));
    });
    setSelectedKey((k) => (k === key ? null : k));
  }

  function removeDependency(targetKey: string, dependencyId: string) {
    setSteps((prev) => prev.map((s) => (s.key === targetKey ? { ...s, depends_on: s.depends_on.filter((d) => d !== dependencyId) } : s)));
  }

  const nodes: Node[] = useMemo(
    () =>
      steps.map((s) => ({
        id: s.key,
        type: "workflowStep",
        position: s.position,
        selected: s.key === selectedKey,
        data: { name: s.name, type: s.type, status: "idle" as RunNodeStatus } satisfies WorkflowNodeData,
      })),
    [steps, selectedKey],
  );

  const edges: Edge[] = useMemo(
    () =>
      steps.flatMap((s) =>
        s.depends_on
          .map((depId) => steps.find((d) => d.id === depId))
          .filter((d): d is StepDraft => Boolean(d))
          .map((dep) => ({ id: `${dep.key}->${s.key}`, source: dep.key, target: s.key })),
      ),
    [steps],
  );

  const onNodesChange: OnNodesChange = useCallback((changes) => {
    setSteps((prev) => {
      let next = prev;
      for (const change of changes) {
        if (change.type === "position" && change.position) {
          const position = change.position;
          next = next.map((s) => (s.key === change.id ? { ...s, position } : s));
        }
      }
      return next;
    });
  }, []);

  const onConnect: OnConnect = useCallback((connection) => {
    const { source, target } = connection;
    if (!source || !target || source === target) return;
    setSteps((prev) => {
      const targetStep = prev.find((s) => s.key === target);
      const sourceStep = prev.find((s) => s.key === source);
      if (!targetStep || !sourceStep || targetStep.depends_on.includes(sourceStep.id)) return prev;
      if (wouldCreateCycle(prev, targetStep.id, sourceStep.id)) {
        setError("That connection would create a cycle.");
        return prev;
      }
      setError(null);
      return prev.map((s) => (s.key === target ? { ...s, depends_on: [...s.depends_on, sourceStep.id] } : s));
    });
  }, []);

  const onEdgesDelete: OnEdgesDelete = useCallback((deleted) => {
    setSteps((prev) => {
      let next = prev;
      for (const edge of deleted) {
        const sourceStep = prev.find((s) => s.key === edge.source);
        if (!sourceStep) continue;
        next = next.map((s) => (s.key === edge.target ? { ...s, depends_on: s.depends_on.filter((d) => d !== sourceStep.id) } : s));
      }
      return next;
    });
  }, []);

  const selectedStep = steps.find((s) => s.key === selectedKey) ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title={isNew ? "New workflow" : name || "Workflow"}
        description="Drag to connect steps, click a step to configure it, then save and run."
        actions={
          <div className="flex gap-2">
            {!isNew && (
              <Button variant="secondary" loading={run.isPending} onClick={() => run.mutate()}>
                Run workflow
              </Button>
            )}
            <Button loading={save.isPending} onClick={() => save.mutate()}>
              Save
            </Button>
          </div>
        }
      />

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Weekly stock export" />
          </Field>
          <Field label="Failure policy" hint="fail_fast stops the run; skip_dependents skips only downstream steps; continue tries every step regardless.">
            <Select value={failurePolicy} onChange={(e) => setFailurePolicy(e.target.value as typeof failurePolicy)}>
              {FAILURE_POLICIES.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </Select>
          </Field>
          <Field label="Description" className="sm:col-span-2">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="optional" />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pipeline</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button variant="secondary" size="sm" onClick={() => setSteps((prev) => [...prev, newStep(prev.length)])}>
            + Add step
          </Button>
          <div className="flex gap-4">
            <div className="relative h-[32rem] flex-1 rounded-md border border-border">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={() => {}}
                onConnect={onConnect}
                onEdgesDelete={onEdgesDelete}
                onNodeClick={(_, node) => setSelectedKey(node.id)}
                onPaneClick={() => setSelectedKey(null)}
                fitView
              >
                <Background />
                <Controls />
              </ReactFlow>
            </div>
            {selectedStep && (
              <div className="w-96 shrink-0 space-y-4 overflow-y-auto rounded-md border border-border p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold">Step config</h3>
                  <Button variant="destructive" size="sm" onClick={() => removeStep(selectedStep.key)}>
                    Remove step
                  </Button>
                </div>
                <Field label="Step id">
                  <Input
                    value={selectedStep.id}
                    onChange={(e) => setSteps((prev) => prev.map((s) => (s.key === selectedStep.key ? { ...s, id: e.target.value } : s)))}
                  />
                </Field>
                <Field label="Name">
                  <Input
                    value={selectedStep.name}
                    onChange={(e) => setSteps((prev) => prev.map((s) => (s.key === selectedStep.key ? { ...s, name: e.target.value } : s)))}
                  />
                </Field>
                <Field label="Type">
                  <Select
                    value={selectedStep.type}
                    onChange={(e) =>
                      setSteps((prev) =>
                        prev.map((s) => (s.key === selectedStep.key ? { ...s, type: e.target.value as StepType, config: blankConfigDraft() } : s)),
                      )
                    }
                  >
                    {STEP_TYPES.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Depends on" hint="Drag a connection between steps on the canvas to add a dependency.">
                  {selectedStep.depends_on.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No dependencies yet.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {selectedStep.depends_on.map((depId) => (
                        <Badge key={depId} variant="neutral" className="gap-1">
                          {depId}
                          <button type="button" aria-label={`Remove dependency on ${depId}`} onClick={() => removeDependency(selectedStep.key, depId)}>
                            ×
                          </button>
                        </Badge>
                      ))}
                    </div>
                  )}
                </Field>
                <StepConfigForm
                  step={selectedStep}
                  allSteps={steps}
                  datasets={datasets.data ?? []}
                  onChange={(config) => setSteps((prev) => prev.map((s) => (s.key === selectedStep.key ? { ...s, config } : s)))}
                />
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {!isNew && (
        <Card>
          <CardHeader>
            <CardTitle>Run history</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {(!runs.data || runs.data.length === 0) && (
              <p className="text-sm text-muted-foreground">No runs yet — click "Run workflow" above.</p>
            )}
            {runs.data?.map((r) => (
              <div key={r.id} className="rounded-md border border-border">
                <button
                  className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm"
                  onClick={() => setExpandedRun(expandedRun === r.id ? null : r.id)}
                >
                  <span className="flex items-center gap-2">
                    <Badge variant={STATUS_VARIANT[r.status] ?? "neutral"}>{r.status}</Badge>
                    <span className="text-muted-foreground">{new Date(r.created_at).toLocaleString()}</span>
                  </span>
                  <span className="text-muted-foreground">{expandedRun === r.id ? "Hide" : "Show timeline"}</span>
                </button>
                {expandedRun === r.id && runDetail.data && (
                  <div className="space-y-1.5 border-t border-border px-4 py-3">
                    {runDetail.data.events.map((e) => (
                      <div key={e.id} className="flex items-center gap-2 text-xs">
                        <span className="w-32 shrink-0 text-muted-foreground">
                          {new Date(e.created_at).toLocaleTimeString()}
                        </span>
                        <Badge variant="neutral">{e.event_type}</Badge>
                        {e.step_id && <span className="font-mono">{e.step_id}</span>}
                        {typeof e.payload.status === "string" && (
                          <Badge variant={e.payload.status === "success" ? "success" : "danger"}>
                            {String(e.payload.status)}
                          </Badge>
                        )}
                        {typeof e.payload.error === "string" && (
                          <span className="truncate text-rose-300">{e.payload.error}</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function GroupByEditor({ columns, value, onChange }: { columns: string[]; value: string[]; onChange: (v: string[]) => void }) {
  if (columns.length === 0) return <p className="text-xs text-muted-foreground">No columns available yet.</p>;
  return (
    <div className="flex flex-wrap gap-3">
      {columns.map((c) => (
        <label key={c} className="flex items-center gap-1.5 text-sm">
          <Checkbox
            checked={value.includes(c)}
            onChange={(e) => onChange(e.target.checked ? [...value, c] : value.filter((v) => v !== c))}
          />
          {c}
        </label>
      ))}
    </div>
  );
}

function StepConfigForm({
  step, allSteps, datasets, onChange,
}: {
  step: StepDraft;
  allSteps: StepDraft[];
  datasets: Dataset[];
  onChange: (config: StepConfigDraft) => void;
}) {
  const config = step.config;
  function set<K extends keyof StepConfigDraft>(key: K, value: StepConfigDraft[K]) {
    onChange({ ...config, [key]: value });
  }

  const dataset = datasets.find((d) => d.id === config.datasetId);
  // ponytail: upstream-step output columns aren't known without running the
  // step, so filter/aggregate fall back to a free-text column input when
  // reading from an upstream step instead of a dataset-backed <Select> -
  // real column autocomplete there needs output-schema inference, out of
  // scope for this phase.
  const knownColumns = dataset ? dataset.columns.map((c) => c.name) : [];
  const upstreamOptions = allSteps.filter((s) => step.depends_on.includes(s.id));

  if (step.type === "load_dataset") {
    return (
      <div className="space-y-4">
        <Field label="Dataset">
          <Select value={config.datasetId} onChange={(e) => set("datasetId", e.target.value)}>
            <option value="">Select a dataset…</option>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Row limit">
          <Input type="number" value={config.limit} onChange={(e) => set("limit", e.target.value)} />
        </Field>
      </div>
    );
  }

  if (step.type === "sql") {
    return (
      <div className="space-y-4">
        <Field label="Dataset">
          <Select value={config.datasetId} onChange={(e) => set("datasetId", e.target.value)}>
            <option value="">Select a dataset…</option>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="SQL">
          <SqlCodeEditor
            value={config.sql}
            onChange={(v) => set("sql", v)}
            columns={knownColumns.map((cname) => ({ name: cname, dtype: "text" as const }))}
          />
        </Field>
        <Field label="Row limit">
          <Input type="number" value={config.limit} onChange={(e) => set("limit", e.target.value)} />
        </Field>
      </div>
    );
  }

  if (step.type === "filter" || step.type === "aggregate") {
    return (
      <div className="space-y-4">
        <Field label="Source">
          <Select value={config.source} onChange={(e) => set("source", e.target.value as StepConfigDraft["source"])}>
            <option value="dataset">A dataset</option>
            <option value="upstream">Output of an upstream step</option>
          </Select>
        </Field>
        {config.source === "dataset" ? (
          <Field label="Dataset">
            <Select value={config.datasetId} onChange={(e) => set("datasetId", e.target.value)}>
              <option value="">Select a dataset…</option>
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </Select>
          </Field>
        ) : (
          <Field
            label="Upstream step"
            hint={upstreamOptions.length === 0 ? "Connect this step from another step on the canvas first." : undefined}
          >
            <Select value={config.inputStep} onChange={(e) => set("inputStep", e.target.value)}>
              <option value="">Select a step…</option>
              {upstreamOptions.map((s) => (
                <option key={s.id} value={s.id}>{s.name || s.id}</option>
              ))}
            </Select>
          </Field>
        )}
        {step.type === "filter" ? (
          <Field label="Filters">
            <FilterRowsEditor rows={config.filters} columns={knownColumns} onChange={(rows) => set("filters", rows)} />
          </Field>
        ) : (
          <>
            <Field label="Group by" hint="Leave empty to aggregate the whole table into one row.">
              <GroupByEditor columns={knownColumns} value={config.groupBy} onChange={(v) => set("groupBy", v)} />
            </Field>
            <Field label="Aggregations">
              <SelectRowsEditor rows={config.select} columns={knownColumns} onChange={(rows) => set("select", rows)} />
            </Field>
          </>
        )}
        <Field label="Row limit">
          <Input type="number" value={config.limit} onChange={(e) => set("limit", e.target.value)} />
        </Field>
      </div>
    );
  }

  if (step.type === "export_result") {
    return (
      <div className="space-y-4">
        <Field
          label="Upstream step"
          hint={upstreamOptions.length === 0 ? "Connect this step from another step on the canvas first." : undefined}
        >
          <Select value={config.inputStep} onChange={(e) => set("inputStep", e.target.value)}>
            <option value="">Select a step…</option>
            {upstreamOptions.map((s) => (
              <option key={s.id} value={s.id}>{s.name || s.id}</option>
            ))}
          </Select>
        </Field>
        <Field label="Format">
          <Select value={config.format} onChange={(e) => set("format", e.target.value as "csv" | "json")}>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </Select>
        </Field>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Field label="File">
        <input
          type="file"
          className="text-sm"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            const base64 = await fileToBase64(file);
            onChange({ ...config, contentBase64: base64, filename: file.name });
          }}
        />
      </Field>
      {config.filename && <p className="text-xs text-muted-foreground">Selected: {config.filename}</p>}
      <Field label="Dataset name (optional)">
        <Input value={config.name} onChange={(e) => set("name", e.target.value)} placeholder={config.filename || "defaults to filename"} />
      </Field>
    </div>
  );
}
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors

- [ ] **Step 5: Manual verification**

Run: `cd frontend && npm run dev`, open `/workflows/new` in a browser.

Verify: the "Pipeline" card shows a canvas with one default step node; clicking it opens the side panel with id/name/type fields, a "Depends on" section, and a `load_dataset` config form (dataset picker + row limit); clicking "+ Add step" adds a second node; dragging from the first node's right handle to the second node's left handle adds an edge and the dependency badge appears in the second node's panel; dragging a node around repositions it; saving with a name and at least one dataset-backed step succeeds and redirects to `/workflows/<id>`.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/pages/workflows/WorkflowStepNode.tsx frontend/src/pages/workflows/WorkflowBuilder.tsx
git commit -m "feat(workflows): rewrite builder around a React Flow canvas with typed step forms"
```

---

### Task 7: Frontend — live run status on canvas nodes

**Files:**
- Modify: `frontend/src/pages/workflows/WorkflowBuilder.tsx`

**Interfaces:**
- Consumes: `RunNodeStatus` (Task 6, `WorkflowStepNode.tsx` — unchanged, already supports all 5 status values).
- Produces: nothing new for later tasks (final functional piece before verification).

- [ ] **Step 1: Add a query for the latest run's live status**

In `frontend/src/pages/workflows/WorkflowBuilder.tsx`, immediately after the existing `runs` query (added in Task 6), add:

```tsx
  const latestRun = runs.data?.[0];
  const latestRunDetail = useQuery<RunDetail>({
    queryKey: ["workflow-run", id, "latest", latestRun?.id],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${id}/runs/${latestRun!.id}`);
      if (!res.ok) throw new Error("Failed to load run");
      return res.json();
    },
    enabled: !isNew && !!latestRun,
    refetchInterval: (query) =>
      query.state.data?.status === "queued" || query.state.data?.status === "running" ? 1500 : false,
  });

  const statusByStepId = useMemo(() => {
    const map: Record<string, { status: RunNodeStatus; error?: string }> = {};
    for (const e of latestRunDetail.data?.events ?? []) {
      if (!e.step_id) continue;
      if (e.event_type === "node_started") map[e.step_id] = { status: "running" };
      if (e.event_type === "node_skipped") map[e.step_id] = { status: "skipped" };
      if (e.event_type === "node_finished") {
        const status = typeof e.payload.status === "string" ? e.payload.status : "success";
        const failed = status !== "success";
        map[e.step_id] = {
          status: failed ? "failed" : "success",
          error: failed && typeof e.payload.error === "string" ? e.payload.error : undefined,
        };
      }
    }
    return map;
  }, [latestRunDetail.data]);
```

- [ ] **Step 2: Feed status into the canvas nodes**

In the same file, update the `nodes` `useMemo` (from Task 6) to read from `statusByStepId` and add it to the dependency array:

```diff
   const nodes: Node[] = useMemo(
     () =>
       steps.map((s) => ({
         id: s.key,
         type: "workflowStep",
         position: s.position,
         selected: s.key === selectedKey,
-        data: { name: s.name, type: s.type, status: "idle" as RunNodeStatus } satisfies WorkflowNodeData,
+        data: {
+          name: s.name,
+          type: s.type,
+          status: statusByStepId[s.id]?.status ?? "idle",
+          error: statusByStepId[s.id]?.error,
+        } satisfies WorkflowNodeData,
       })),
-    [steps, selectedKey],
+    [steps, selectedKey, statusByStepId],
   );
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors

- [ ] **Step 4: Manual verification**

With the dev server running (`cd frontend && npm run dev`), open an existing saved workflow with at least 2 connected steps (build one via the canvas if none exists, using a real dataset from `/datasets`), click "Run workflow", and watch the canvas: the first node's border should pulse (running), then turn green (success) or red (failed) with an error tooltip on hover, and the next node should follow the same sequence once its dependency finishes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/workflows/WorkflowBuilder.tsx
git commit -m "feat(workflows): show live run status on canvas nodes"
```

---

### Task 8: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest -q`
Expected: all tests pass, including `test_workflow_dag.py`, `test_workflows.py` (with the new `continue` test), and `test_workflow_steps.py` unaffected.

- [ ] **Step 2: Run the full frontend typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Run the new frontend unit tests**

Run: `cd frontend && npx vitest run src/pages/workflows`
Expected: `stepLayout.test.ts` (8 tests) and `stepConfig.test.ts` (10 tests) pass.

- [ ] **Step 4: Manual end-to-end browser walkthrough**

With `cd frontend && npm run dev` and the backend running, in a browser:

1. Go to `/workflows`, click "New workflow".
2. Build a 3-step pipeline entirely through the canvas and side panel, with zero hand-typed JSON: a `load_dataset` step against a real uploaded dataset, a `filter` step connected from it (source = "Output of an upstream step", one filter row), and an `export_result` step connected from the filter step (format `csv`).
3. Save. Confirm redirect to `/workflows/<id>` and that reopening the page shows the same 3 nodes in the same positions (layout persisted).
4. Click "Run workflow". Confirm nodes visually update in sequence (running → success) and the "Run history" text timeline below still shows the same run's full event log.
5. Reload the page. Confirm the canvas still renders the saved layout and the last run's final status.

- [ ] **Step 5: Final commit (if manual verification required any fixup)**

```bash
git add -A
git commit -m "chore(workflows): verification fixups for visual pipeline builder"
```

(Skip this commit if Step 4 required no code changes.)
