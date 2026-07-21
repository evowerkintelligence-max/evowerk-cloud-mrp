# Workflows Visual Pipeline Builder

Status: Approved for planning
Date: 2026-07-18

## Problem

The Workflows feature (`backend/app/models/workflow.py`,
`backend/app/services/workflow_dag.py`, `workflow_runner.py`,
`workflow_steps.py`, `backend/app/api/routes/workflows.py`,
`frontend/src/pages/workflows/{Workflows,WorkflowBuilder}.tsx`) already has a
real DAG engine underneath it: Kahn's-algorithm topological layering,
parallel execution per layer, three failure policies, an append-only run
event log, tenant isolation, audit logging. The SQL step in particular is a
well-hardened sandbox (single-statement DuckDB, temp-table isolation,
`enable_external_access=false`, timeout watchdog, row cap).

None of that shows on the surface. `WorkflowBuilder.tsx` configures every
step through a raw JSON `<textarea>` (`{"dataset_id": "...", "filters":
[...]}`, schema undocumented in the UI) and wires dependencies through a flat
checkbox list - there is no visual graph, at build time or at run time. The
component's own comment admits this is a placeholder: *"raw JSON box instead
of a per-step-type form - 8 step types x bespoke fields is a lot of UI for
phase 1; add typed forms per step type when a real user asks for one."*
Someone has now asked for one, and asked for the SAS-Viya-style pipeline
canvas on top of it.

This spec is Phase 1 of that ambition: turn the engine that already exists
into a pipeline builder actually worth using - an interactive DAG canvas,
typed config forms per step type, and live run status rendered on the graph.
It does not add new step types; it makes the 8 that exist buildable and
watchable without hand-writing JSON.

## Scope

In scope: an interactive drag/connect/pan/zoom canvas (React Flow) replacing
the checkbox dependency list; a typed config form per step type replacing
the JSON textarea; step node position persisted so a reopened workflow keeps
its layout; live run status rendered on canvas nodes from the existing
`WorkflowRunEvent` stream; a regression test for the `continue` failure
policy (see Bug fixed in passing); no other behavior change to the DAG
engine.

Out of scope for this phase (explicit decomposition, not oversights - each
becomes its own spec later):

- New step types beyond the current 8 (`sql`, `filter`, `aggregate`,
  `load_dataset`, `export_result`, `import_csv`, `import_excel`,
  `import_json`). No RCA-case, alert, AutoML, or MRP-action steps yet.
- Scheduling or event-driven triggers. Workflows still only run on manual
  "Run workflow" clicks.
- A generic/pluggable step-type registry exposed to the frontend. The 8 step
  types stay hardcoded in the frontend form set, matching how they're
  already hardcoded in `workflow_dag.STEP_TYPES` - no dynamic schema
  discovery endpoint. Worth revisiting once a second phase actually adds new
  step types.
- Any model-training/scoring-pipeline-specific UI. The "SAS Viya" analogy is
  about the canvas experience, not about building an ML pipeline product in
  this phase.

## Bug fixed in passing

`workflow_runner._execute`'s per-layer skip check
(`if set(step.depends_on) & (failed_ids | skipped_ids): skipped_ids.add(...)`)
runs unconditionally, regardless of `failure_policy`. So `continue` -
documented in the UI as *"tries every step regardless"* - currently skips
downstream-of-failure steps exactly like `skip_dependents` does; there is no
running failure_policy today under which a step actually executes with a
missing/incomplete upstream after one of its dependencies failed. This was
never caught because no test exercises `continue`. Fixing the runner's
`continue` semantics (steps proceed even after an upstream failure, missing
outputs surfacing as an empty/partial `upstream` dict) is a small, contained
change alongside this rewrite - not part of the canvas work, but touching
this file for the UI overhaul is the natural point to close a gap already
identified. Add `test_continue_runs_downstream_steps_despite_upstream_failure`
to `backend/tests/test_workflows.py` covering it.

## Data model

`Workflow.steps` (JSON column, unchanged shape otherwise) gains one optional
key per step:

```
{"id": str, "name": str, "type": str, "depends_on": [str], "config": dict,
 "position": {"x": float, "y": float} | omitted}
```

`workflow_dag.WorkflowStep` (Pydantic) gains:

```python
class StepPosition(BaseModel):
    x: float
    y: float

class WorkflowStep(BaseModel):
    id: str
    name: str
    type: str
    depends_on: list[str] = []
    config: dict = {}
    position: StepPosition | None = None
```

`position` is UI metadata only - `validate_dag`, `topo_layers`, and
`descendants` never read it. No migration: it's a new key inside the
existing JSON column, and `Workflow.steps` is already untyped JSON at the
DB layer. A workflow saved before this change simply has no `position` on
its steps; the frontend auto-lays-out unpositioned nodes on first open (see
Frontend) and persists positions from then on.

## API

No new endpoints. `WorkflowWriteRequest`/`WorkflowResponse` in
`backend/app/api/routes/workflows.py` already pass `steps: list[dict]`
through opaquely to/from the DB - `position` rides along inside each step
dict with zero route changes. `_validate_or_400` continues to call
`dag_service.validate_dag`, which now accepts (and ignores) `position`.

## Frontend

### Canvas

New dependency: `@xyflow/react` (React Flow). `WorkflowBuilder.tsx` is
restructured around a `<ReactFlow>` canvas:

- One node per step: id, name, a type badge, and (during/after a run) a
  status indicator.
- One edge per `depends_on` entry, directed from the dependency to the
  dependent step.
- Dragging a node updates that step's `position` in local state (persisted
  on Save, same as every other field today).
- Drawing a connection between two nodes adds a `depends_on` entry; deleting
  an edge removes it. Client-side cycle rejection on connect (reject the
  drag if the resulting graph would cycle, checked via the same
  Kahn's-algorithm shape as the backend) - purely a UX nicety, since
  `_validate_or_400` still rejects a cyclic save server-side regardless.
- Workflows saved before this change (no `position` on any step) get one
  auto-layout pass on first load using a simple layered layout (steps
  grouped into columns by topological depth, matching how `topo_layers`
  already groups them) - not a new dependency, just reusing the layering
  the backend already computes conceptually, ported client-side in a ~20
  line helper.
- Clicking a node opens its config in a docked side panel (not the existing
  modal `Dialog` component - a modal would block seeing the canvas while
  editing a step, which defeats the point of a live graph) - keeps the
  canvas from becoming unreadable as a workflow grows past a handful of
  steps.

### Typed step config forms (in the side panel)

Replaces the single `<textarea>` per step with a form keyed on `step.type`:

- **load_dataset**: dataset `<Select>` (via `useDatasets()`, already used
  elsewhere in this file), numeric row-limit input.
- **sql**: dataset `<Select>` + `SqlCodeEditor` (reused as-is from
  `frontend/src/components/SqlCodeEditor.tsx`, which already provides
  column-aware autocomplete - the dataset's columns feed its `columns`
  prop, same as its other call site).
- **filter**: a source toggle (dataset vs. upstream step, matching
  `_upstream_rows`/`dataset_id` mutual exclusivity in
  `workflow_steps._run_filter`), then repeatable filter rows - new small
  `FilterRowEditor` component (column `<Select>`, op `<Select>` from
  `{eq,neq,contains,in,gt,gte,lt,lte}`, value `<Input>`). No existing
  filter-row builder to reuse (`ReportBuilder.tsx`'s query section is also
  raw JSON) - this is genuinely new, small UI.
- **aggregate**: same source toggle, group-by multi-select over the
  dataset's columns, repeatable select rows (column `<Select>`, agg
  `<Select>` from `{sum,avg,min,max,count}`, alias `<Input>`) - reuses
  `FilterRowEditor`'s row-list pattern for the select rows too.
- **import_csv / import_excel / import_json**: file `<input type="file">`,
  read client-side and base64-encoded into `config.content_base64` (matches
  what `_run_import` already expects), filename auto-filled from the
  picked file.
- **export_result**: upstream-step `<Select>` (from `depends_on`), format
  radio (`csv`/`json`).

Every form emits exactly the JSON shape `workflow_steps.py`'s runners
already expect - no backend schema changes. Config parsing/validation moves
from "hope the JSON is right" to "the form only lets you build a valid
shape," which is the actual idiot-proofing fix.

### Live run status on canvas

`runDetail` (the existing polled `WorkflowRunEvent` query) drives node
visual state instead of only the text timeline:

- `node_started` -> node gets a pulsing/running border.
- `node_finished` (`status: success`) -> green.
- `node_finished` (`status: failed`) -> red, error text in a tooltip on
  hover (full text stays in the text timeline below, not truncated there).
- `node_skipped` -> greyed out.
- No event yet for this run -> neutral/default node style.

The text timeline (`Run history` card) is kept, not removed - it's still
the only place with full event timestamps and untruncated error text.

## Files touched

Backend:
- `backend/app/services/workflow_dag.py` - add `StepPosition`, `position`
  field on `WorkflowStep`.
- `backend/app/services/workflow_runner.py` - fix `continue` failure-policy
  semantics (see Bug fixed in passing).
- `backend/tests/test_workflows.py` - add the `continue`-policy regression
  test.

Frontend:
- `frontend/package.json` - add `@xyflow/react`.
- `frontend/src/pages/workflows/WorkflowBuilder.tsx` - rewritten around the
  canvas, side-panel config forms, and live status.
- `frontend/src/pages/workflows/FilterRowEditor.tsx` - new, shared by the
  filter and aggregate forms.
- `frontend/src/pages/workflows/stepLayout.ts` - new, the client-side
  layered auto-layout helper for workflows with no saved `position`s.
- `frontend/src/pages/workflows/Workflows.tsx` - no change expected (list
  page is unaffected).

## Testing

Backend (`pytest`, extends `backend/tests/test_workflows.py`): the new
`continue`-policy test asserts a step downstream of a failed one still
executes (event log shows `node_started`/`node_finished` for it, not
`node_skipped`), with its `upstream` dict missing the failed dependency's
entry rather than raising. Existing tests (`test_workflows.py`,
`test_workflow_steps.py`) stay green unmodified otherwise - `position` is
additive and every other code path is untouched.

Frontend: no automated test runner precedent for page-level UI in this repo
(consistent with prior specs). New unit coverage for `FilterRowEditor`
(emits the exact `QueryFilter`/`SelectField` JSON shape) and `stepLayout.ts`
(topological-depth columns match `topo_layers`' grouping) via `vitest`,
which is already a dependency. Manual in-browser verification: `tsc -b`
typecheck, then build a 3-4 step workflow via drag/connect (mixing a `sql`
step, a `filter` step chained off it, and an `export_result` step) entirely
through the canvas and forms with zero hand-typed JSON, save, run it, watch
node status update live, reload the page and confirm the layout persisted.
