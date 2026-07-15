# Report Builder: Capture-Based Composition ("Send to report")

Status: Approved for planning
Date: 2026-07-15

## Problem

Report Builder (`frontend/src/pages/ReportBuilder.tsx`,
`backend/app/api/routes/reports.py` + `backend/app/services/reports.py`)
today only builds reports by hand: "Add section" opens a dialog where you
type a dataset UUID and a raw `DatasetQueryRequest` JSON blob (for `table`
sections), or pick a chart from a dropdown (`bi_chart`), or type free text
(`title`/`exec_summary`/`findings`/etc.). There is no way to take a result
you're already looking at - an OEE breakdown, an MRP plan, an RCA case - and
put it in a report without re-deriving its parameters by hand.

This spec adds a capture flow: any result-producing page gets a "Send to
report" button. Clicking it drops a *live reference* to that result into a
per-org inbox (not a frozen snapshot - the app's existing reports are
explicitly "built from live data when you ask for it, nothing is stored,"
per the comment at the top of `reports.py`, and this preserves that). Report
Builder gets an "Add from inbox" picker to select and order captured items
into the report being composed. The existing manual "Add section" dialog is
unchanged and stays available for hand-authored narrative content, which has
no "result" to capture from.

## Scope

In scope: the inbox data model and API; generalizing section resolution
from one-spec-produces-one-`Section` to one-spec-produces-many; a generic
resolver for the 7 analytics-engine pages (OEE, Downtime, Throughput, Scrap,
Shifts, SPC, Plant Summary); a resolver reusing the existing
`build_mrp_plan_report`/`build_rca_report`/`build_inventory_analytics_report`/
`build_scenario_report` functions; a resolver for RCA cases
(`RcaCases.tsx`); "Send to report" wired into all of the above plus BI
charts, dataset/SQL queries, and the Scenarios what-if page; an "Add from
inbox" picker in Report Builder; relabeling the AI narration style picker.

Out of scope: per-item narration style (stays report-level, per existing
behavior); auto-expiring or "mark as used" inbox items (manual delete only);
capturing the RcaCases fishbone diagram as anything other than two flat
tables (causes, actions) - no visual tree in the report; a frontend test
runner (matches the precedent set in
`2026-07-15-sql-editor-autocomplete-design.md` - none exists in this repo).

## Data model

`ReportSectionSpec` (`backend/app/services/reports.py`) gains three new
`type` values alongside the existing `title | exec_summary | table |
bi_chart | findings | risks | recommendations | caveats | text`, each with
its own optional fields (all `None` unless that type is in use):

```python
type: Literal[..., "engine", "kind_report", "rca_case"]

# engine - one of the 7 analytics pages, all sharing EngineScreen
engine_path: str | None        # e.g. "/analytics/oee" - the same `path`
                                # prop EngineScreen already takes
engine_body: dict | None       # the exact request body EngineScreen last
                                # POSTed (dataset_id + column mappings)

# kind_report - RCA-by-dimension / MRP plan / inventory analytics / scenario
report_kind: str | None        # "mrp-plan" | "inventory-analytics" | "rca"
                                # | "scenario" (matches KINDS in reports.py)
report_params: dict | None     # horizon dates, dimension, lead_time
                                # overrides - whatever that kind's builder needs

# rca_case - the fishbone case-management tool
case_id: uuid.UUID | None
```

New `ReportInboxItem` model (`backend/app/models/report_spec.py`, alongside
`SavedReport`), org-scoped the same way:

```python
class ReportInboxItem(Base):
    __tablename__ = "report_inbox_items"
    id: UUID (pk)
    org_id: UUID (fk orgs.id, cascade delete, indexed)
    created_by: UUID (fk users.id)
    created_at: datetime
    label: str                 # shown in the "Add from inbox" picker
    spec: JSON                 # one ReportSectionSpec-shaped dict
```

Migration adds this table only - `ReportSectionSpec`'s new fields are
Pydantic-level (request/response validation, not columns); `SavedReport.
sections` is already a JSON blob and needs no migration to store specs with
new `type` values.

## API

All under the existing `/reports` router, same org-scoping pattern as every
other endpoint there (`current.org_id` filter, 404-not-403 on cross-org
access to match `get_saved_report_or_404`'s existing convention):

- `POST /reports/inbox` - body `{label, ...ReportSectionSpec fields}`.
  Creates an item, `created_by = current.id`. Any authenticated org member
  (`get_current_user`) - this is what every "Send to report" button calls.
- `GET /reports/inbox` - list the org's inbox, newest first.
- `DELETE /reports/inbox/{id}` - **creator or owner**: `current.id ==
  item.created_by or current.role == "owner"`, else 403. This is a narrower
  rule than the plain `require_role("owner")` used for
  chart/dashboard/dataset/saved-report deletion elsewhere in this codebase
  (see `bi.py:delete_chart`/`delete_dashboard`, `datasets.py:delete_dataset`,
  and this branch's fix to `reports.py:delete_custom_report`) - deliberate,
  since inbox items are personal scratch/staging data rather than a named,
  shared deliverable. Implemented as a small inline check in the route (not
  a new `require_role` variant), since it needs the resource's
  `created_by`, which a dependency resolved before the row is fetched
  can't see.

## Resolution: one spec, many sections

`_resolve_section(spec) -> Section` becomes `_resolve_sections(spec) ->
list[Section]`. Existing types keep returning a single-element list - no
behavior change for `table`, `bi_chart`, `text`, `findings`, `risks`,
`recommendations`, `caveats`, `title`, `exec_summary`. `build_custom_report`
changes from a list comprehension to a flatten:

```python
resolved = [section for spec in sections for section in await _resolve_sections(db, org_id, spec)]
```

**`engine`**: a static `dict[str, tuple[type[BaseModel], Callable]]` maps
`engine_path` to `(RequestModel, handler_fn)`, e.g. `{"/analytics/oee":
(OeeComputeRequest, oee_compute), "/analytics/downtime":
(DowntimeAnalyzeRequest, downtime_analyze), ...}` for all 7 engines.
Resolution: validate `engine_body` against `RequestModel`, call `await
handler_fn(req, current, db)` directly (these are plain `async def(req,
current, db)` functions - no HTTP self-call, no duplicated compute logic),
then run a generic flattener over the returned Pydantic response:

```python
def _flatten_response_to_sections(title: str, resp: BaseModel) -> list[Section]:
    """Scalar top-level fields -> one 'Summary' Section (metric/value rows).
    Each list[dict]-shaped field -> its own named Section. One level of
    recursion into nested BaseModel/dict fields (e.g. OEE's six_big_losses)
    to catch their list sub-fields too."""
```

This one function is what makes covering all 7 engines a single change
instead of 7 - their response shapes differ, but all follow the same
"scalars + a few `list[...]` breakdowns" pattern (confirmed by reading all 7
response models in `backend/app/api/routes/{oee,downtime,throughput,scrap,
shifts,spc,plant_summary}.py`).

**`kind_report`**: dispatch `report_kind` to the matching existing builder
(`build_mrp_plan_report`, `build_rca_report`, `build_inventory_analytics_report`,
`build_scenario_report`) with `report_params` unpacked into that builder's
existing arguments, and return `result.sections` directly - these already
produce `Section` objects, zero new rendering logic.

**`rca_case`**: fetch the `RcaCase` + its `RcaNode`s + `RcaAction`s
(org-scoped 404 if missing), return two `Section`s: "Causes" (columns:
Text, Type, Category, Status) and "Actions" (columns: Description, Owner,
Status, ΔOEE - from `RcaAction.verification`).

## Report Builder UI

`frontend/src/pages/ReportBuilder.tsx`: existing section list, Up/Down/Edit/
Remove, and the manual "+ Add section" dialog are unchanged. New "Add from
inbox" button opens a dialog: `GET /reports/inbox` results shown as
checkable rows (label, a source-type badge, created-by, created-at). Deleted
inbox items get a Delete button on their row (calls the creator-or-owner
endpoint above; hidden/disabled per-row when the current user is neither).
"Add selected" copies the checked items' `spec`s (in the order checked) onto
the end of the report's `sections` array - after that they're indistinguishable
from manually-added sections and can be reordered/edited/removed the same
way. Inbox items are not consumed or marked used, so the same capture can be
added to multiple reports.

## "Send to report" - the capture button

One shared component, `SendToReportButton` in
`frontend/src/pages/analytics/shared.tsx` (next to `useDatasets`/`useCharts`):
takes a `spec: Partial<ReportSectionSpec>` and a default `label`, POSTs to
`/reports/inbox`, shows a success toast/inline confirmation. Wired in:

- **`EngineScreen`** (covers all 7 analytics pages for free): after a
  successful `compute` mutation, render the button with `{type: "engine",
  engine_path: path, engine_body: <the body just POSTed>}`.
- **`Reports.tsx`** (the standalone kind-dropdown page): `{type:
  "kind_report", report_kind: kind, report_params: <current form state -
  horizon dates / dimension / lead_time overrides>}`.
- **`Planning.tsx`**: `{type: "kind_report", report_kind: "mrp-plan",
  report_params: {horizon_start, horizon_end}}` from its own horizon inputs
  - same underlying builder as the Reports.tsx path, different trigger point.
- **`RcaCases.tsx`** case detail view: `{type: "rca_case", case_id}`.
- **`DashboardDetail.tsx`**'s `ChartCard`** (saved charts only - not the
  unsaved live preview in `ChartBuilder.tsx`, which has no `chart_id` yet):
  `{type: "bi_chart", chart_id}`.
- **Datasets Query tab, SQL Editor, `Scenarios.tsx`**: `{type: "table",
  dataset_id, query}` - all three already produce a `DatasetQueryRequest`-
  shaped query object today, so this is a pure reuse of the `table` type
  added in this branch's earlier fix, no new resolver code.

## AI narration

Cosmetic only: `NARRATION_STYLES` values (`narrative`/`bullets`/`brief`)
stay as-is in the backend (already grounded, already per-section); the
frontend `<Select>` in `ReportBuilder.tsx` gets clearer option labels
("Paragraph" / "Bullet points" / "Brief") instead of the raw enum values.

## Files touched

Backend:
- `backend/app/models/report_spec.py` - add `ReportInboxItem`.
- `backend/alembic/versions/` - new migration for `report_inbox_items`.
- `backend/app/services/reports.py` - broaden `ReportSectionSpec`;
  `_resolve_section` -> `_resolve_sections`; new engine-dispatch dict +
  `_flatten_response_to_sections`; `kind_report` and `rca_case` resolution;
  `build_custom_report`'s flatten.
- `backend/app/api/routes/reports.py` - `POST/GET/DELETE /reports/inbox`.
- `backend/app/api/routes/{oee,downtime,throughput,scrap,shifts,spc,
  plant_summary}.py` - no changes; handler functions are called directly,
  as-is.

Frontend:
- `frontend/src/pages/analytics/shared.tsx` - `SendToReportButton`,
  `useInbox()` (mirrors `useCharts`/`useDatasets`).
- `frontend/src/pages/analytics/shared.tsx` (`EngineScreen`) - render the
  button after a successful compute.
- `frontend/src/pages/ReportBuilder.tsx` - "Add from inbox" dialog; style
  label relabel.
- `frontend/src/pages/Reports.tsx`, `Planning.tsx`, `RcaCases.tsx`,
  `analytics/DashboardDetail.tsx`, `Datasets.tsx` (Query tab), `SqlEditor.tsx`,
  `Scenarios.tsx` - one `SendToReportButton` call each.

## Testing

Backend (`pytest`, following `test_report_builder.py`/`test_bi.py`
conventions): inbox CRUD + creator-or-owner delete RBAC (both allowed and
403 paths, mirroring today's `test_rbac_blocks_non_owner_from_delete`
pattern); one resolution test per new `type` (`engine` against a real
analytics compute, `kind_report` against each of the 4 kinds,` rca_case`);
`build_custom_report` correctly flattens a spec that expands to multiple
sections.

Frontend: no automated test runner in this repo (consistent with the
precedent in `2026-07-15-sql-editor-autocomplete-design.md`). Verify
manually in-browser per source: `tsc -b` typecheck, then for a
representative subset (one analytics engine, the kind-report page, an RCA
case, a BI chart) - send to inbox, confirm it appears in the picker with
the right label/badge, add it to a report, confirm it renders correctly in
HTML/PDF/CSV/XLSX export and survives AI narration.
