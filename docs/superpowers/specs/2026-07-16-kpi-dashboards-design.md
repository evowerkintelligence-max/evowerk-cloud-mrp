# KPI Dashboards & Breakdown Drill-Down

Status: Approved for planning
Date: 2026-07-16

## Problem

`Kpis.tsx` / `kpis.py` / `services/kpis.py` let a user define a named SQL
metric over a dataset with cached history and delta/static/baseline/trend
breach evaluation - but present it as a flat table with a manual "Compute"
button per row. Compared to MachineMetrics and TeepTrak's KPI dashboards
(researched 2026-07-16, see [[teeptrak-comparison]] memory), this app is
missing:

1. A real card/widget dashboard (today: plain table row, no visualization)
2. Auto-refreshing values (today: computed only on click)
3. A way to drill from a KPI into *why* it's breaching (no root-cause view)
4. Per-machine/per-line breakdown of a KPI (today: one KPI = one scalar)
5. Tiered views for different audiences - operator (few metrics, fast
   refresh, fullscreen) vs. supervisor (more metrics, slower refresh)

Additionally, `kpis.py`'s mutating routes (`POST`/`PATCH`/`DELETE /kpis`,
`POST /kpis/seed/manufacturing`) have no `require_role(...)` guard, unlike
every other resource route in this codebase (`datasets.py`, `items.py`,
`boms.py`, etc.) - any authenticated org member, including a read-only role,
can create, edit, delete, or reseed KPIs. This is fixed as part of this spec.

## Scope

In scope: a structured (picker-based, not raw-SQL) breakdown/group-by config
on `Kpi`, reused for both per-machine breakdown and breach-Pareto root-cause;
a new `KpiDashboard` model (named, ordered, tiered collection of KPIs); a
new dashboard page with auto-refreshing cards, sparklines, and breakdown bar
charts; reuse of `EngineScreen`'s existing fullscreen/auto-refresh pattern
rather than a new one; RBAC on all KPI and dashboard mutation routes.

Out of scope for v1: cross-org/shared dashboards, drag-to-reorder UI (reorder
via re-submitting the ordered `kpi_ids` list is enough), scheduled/cron
auto-compute independent of a viewer having the page open, notification
channels (webhook alerting already exists via `AlertRule`, separate
feature), a charting library dependency (none is installed or used anywhere
in this codebase today - hand-rolled SVG is enough for a sparkline and a bar
list).

## Data model

### `Kpi` - 3 new nullable columns (additive migration, no backfill)

- `breakdown_group_col: str | None` - categorical column name from the
  KPI's own dataset
- `breakdown_agg: str | None` - `"count" | "avg" | "sum"`; implied `"count"`
  when `breakdown_group_col` is set and this is null
- `breakdown_value_col: str | None` - numeric column name; required only
  when `breakdown_agg != "count"`

Both column names are validated against the KPI's dataset's actual schema
(`Dataset.columns`) at write time and quoted via the same allow-list +
`qi()` pattern `dataset_query.py` already uses - never raw string
interpolation, consistent with every other structured-query path in this
codebase. `sql_query.py`'s free-text path (used for the KPI's *main* `sql`
field) is intentionally the only place that breaks that rule; the new
breakdown fields are structured, so they follow the allow-list convention
instead.

### New `KpiDashboard`

```
id: uuid
org_id: uuid
name: str
tier: str  # "operator" | "supervisor"
kpi_ids: list[uuid]  # JSON, ordered - same pattern as Kpi.tags
refresh_seconds: int  # default 15 for operator, 300 for supervisor
created_by: uuid
created_at, updated_at
```

No join table - an ordered JSON id list, matching how `Kpi.tags` already
stores an ordered list on this same model family. Reordering/membership
changes are a full-list PATCH.

## Backend

### `services/kpis.py`

- `_compute_one()` runs the breakdown query (if configured) immediately
  after the main value, via the existing sandboxed `run_sql()`. Always
  computed when configured (not gated on breach status) - per-machine
  breakdown is useful even when the KPI is healthy.
  - `agg == "count"`: `SELECT {group_col}, COUNT(*) FROM {table} GROUP BY {group_col} ORDER BY 2 DESC LIMIT 10`
  - `agg in ("avg","sum")`: `SELECT {group_col}, {AGG}(TRY_CAST({value_col} AS DOUBLE)) FROM {table} GROUP BY {group_col} ORDER BY 2 DESC LIMIT 10`
- `KpiComputeItem` gains `breakdown: list[tuple[str, float]] | None`.
- New `compute_kpis_multi_dataset(db, org_id, kpi_ids, *, force, include_eval, lookback)`:
  groups the requested KPI ids by each KPI's own `dataset_id`, calls the
  existing `compute_kpis()` once per group, merges results back into
  request order. No change to `compute_kpis()` itself. This exists because
  today `/kpis/cards` requires one shared `dataset_id` for every requested
  KPI, which breaks for a dashboard spanning KPIs whose SQL runs against
  different datasets (e.g. a supervisor dashboard mixing a Throughput KPI
  and a Quality KPI).

### `api/routes/kpis.py`

- `KpiWriteRequest`/`KpiPatchRequest` gain the 3 breakdown fields
  (validated against the target dataset's columns).
- `KpiComputeItemResponse` gains `breakdown: list[dict] | None`.
- Add `require_role(...)` (same role set `datasets.py` uses) to
  `create_kpi`, `update_kpi`, `delete_kpi`, `seed_manufacturing_kpis`.
  Read-only routes (`list_kpis`, `get_kpi`, `/compute`, `/cards`,
  `/history`) stay open to any authenticated org member.

### New `api/routes/kpi_dashboards.py` (`/kpi-dashboards`)

CRUD following the exact shape `kpis.py` already uses:

- `GET /kpi-dashboards` - list for org
- `POST /kpi-dashboards` - create; `require_role(...)`
- `PATCH /kpi-dashboards/{id}` - rename/retier/reorder; `require_role(...)`
- `DELETE /kpi-dashboards/{id}` - `require_role(...)`
- `GET /kpi-dashboards/{id}/cards` - computes every KPI on the dashboard via
  `compute_kpis_multi_dataset`, `include_eval=True`; returns each card with
  its KPI definition (name/unit/direction) inlined so the frontend doesn't
  need a second round-trip per card. Open to any authenticated org member.

## Frontend

No charting library is installed or used anywhere in this codebase
(verified by grep across `frontend/src`) - two small self-contained SVG
components instead of a new dependency:

- `Sparkline` (~15 lines) - inline `<svg><polyline>` from a number array
- `BreakdownBars` (~20 lines) - horizontal bar list (label + value), used
  for both per-machine breakdown and Pareto display

### `Kpis.tsx` (existing, unchanged except the form)

Create/edit form gains a collapsible "Breakdown (optional)" section: a
group-by column `<select>` populated from `GET /datasets/{id}` (already
returns `columns: [{name, dtype}]`, no new endpoint needed), an aggregate
`<select>` (`count`/`avg`/`sum`), and a value-column `<select>` shown only
when agg is not `count`.

### New `KpiDashboards.tsx` (`/kpis/dashboards`, tab from the KPIs page)

- Dashboard picker + "New dashboard" flow (name, tier, multi-select KPIs)
- Card grid: name, value, unit, status badge, trend arrow (from
  `eval.trend`), `Sparkline` (from `/kpis/{id}/history`), `BreakdownBars`
  underneath when the KPI has a breakdown configured
- Auto-refresh: reuses `EngineScreen`'s existing `autoRefresh` state +
  `setInterval` pattern verbatim (`frontend/src/pages/analytics/shared.tsx:440`),
  seeded from the dashboard's `refresh_seconds`
- Fullscreen "TV mode": reuses `EngineScreen`'s existing toggle
  (`frontend/src/pages/analytics/shared.tsx:581`) rather than reimplementing
  it; operator-tier dashboards default it on, supervisor-tier default off

## Testing

- Backend: extend `test_kpis.py` with breakdown-query cases (count-Pareto
  and avg-per-group); new `test_kpi_dashboards.py` covering CRUD, the
  multi-dataset `cards` compute path (two KPIs from two different datasets
  on one dashboard), and an RBAC regression test (non-privileged role gets
  403 on KPI and dashboard mutation routes).
- Frontend: no existing frontend test suite to extend (no `.tsx` page in
  this codebase has tests today) - verified manually via dev-server preview.
