# Data Readiness — Design

## Problem

A dataset's Detail page (`frontend/src/pages/analytics/DatasetDetail.tsx`) lets a user upload
tabular data and then run analytics against it — Quality analysis, Process Mining, and, via
separate pages elsewhere in the app, OEE, SPC, Throughput, Scrap, Downtime, Shifts, Root Cause
Analysis, Plant Summary, and Manufacturing Forecast. Each of these requires the user to map
specific columns in the dataset to specific semantic roles (e.g. "which column is the defect
flag", "which column is the timestamp"), and each has an implicit type constraint on that role
(numeric, datetime) enforced only in backend service logic (`pd.to_numeric`/`pd.to_datetime`/
DuckDB `TRY_CAST`, all with `errors="coerce"`) — never surfaced before the user opens that
feature's page and tries it.

Today there is no way to tell, before opening each of those ~11 pages, whether a given dataset
has the right shape of columns to make that feature useful at all. Users discover this via a
disabled button or a silently-empty result. This feature closes that gap: a per-dataset
checklist showing which of the app's analytics features are actually usable given the dataset's
inferred column types.

## Scope decision: what "ready" means

Readiness checks **type-availability, not semantic mapping**. It answers "does this dataset have
enough columns of the right type to attempt this feature" — not "which column plays which role."
Actually guessing that a column named `defect_flag` should map to the Quality panel's `defect_column`
role is a materially harder, fuzzier feature (name-based matching, false-positive risk) and is
explicitly out of scope for this iteration. It can be layered on top later without changing the
architecture below (the requirements registry stays the same; only the checker would add a
name-matching pass).

## Architecture — pure frontend, no backend changes

The dataset `/health` endpoint (`GET /datasets/{id}/health`, backend `compute_health` in
`backend/app/services/datasets.py`) already infers each column's type as `"numeric"`, `"text"`,
or `"datetime"` from cell content (CSV/TSV are sniffed `ALL_VARCHAR=1` at ingest, so the raw
schema type is useless for this — `inferred_dtype` from actual values is the real signal, same
reason `Schema` and `SqlPanel` tabs already consume it). Readiness is a static requirements table
plus a pure function that cross-checks column-type *counts* from that same response — no new API
endpoint, no new backend service, no new network round trip beyond what the page already makes.

### New file: `frontend/src/pages/analytics/readiness.ts`

```ts
interface ColumnRole {
  role: string;                              // human label, e.g. "planned time"
  dtype: "numeric" | "datetime" | "any";
  required: boolean;
}

interface FeatureRequirement {
  key: string;          // stable id, e.g. "oee"
  label: string;        // display name, e.g. "OEE"
  to: string;            // "/analytics/oee", or a DatasetDetail tab name ("quality" | "process")
  isTab: boolean;        // true if `to` is a DatasetDetail tab (switch via setTab), false if it's a route (navigate)
  roles: ColumnRole[];
}

const FEATURE_REQUIREMENTS: FeatureRequirement[] = [ /* see Registry below */ ];

interface ReadinessResult {
  key: string;
  label: string;
  to: string;
  isTab: boolean;
  ready: boolean;
  missing: string[];   // human-readable reasons, empty if ready
}

function computeReadiness(columns: { inferred_dtype: string }[]): ReadinessResult[];
```

`computeReadiness` tallies `numericCount`, `datetimeCount`, and `totalCount` once from the
dataset's columns, then for each `FeatureRequirement` sums how many *required* roles need each
dtype (`neededNumeric`, `neededDatetime`, `neededAny`) and compares against the tallies. A
feature is `ready` only if the dataset has **at least as many** columns of each required type as
that feature needs distinct roles for — e.g. OEE needs 4 separate numeric columns (planned time,
operating time, total units, good units), so `numericCount >= 4`, not merely `>= 1`. This is a
cardinality check, not a "does at least one exist" check, because reusing the same physical
column for two different required roles is nonsensical in every one of these features.

`missing` is built as human text, e.g.:
- `"needs 2 more numeric columns (have 2, need 4)"`
- `"needs a datetime column"`

### Registry

| key | label | target | required roles |
|---|---|---|---|
| `quality` | Quality Analyze | tab `quality` | 1 numeric (defect) |
| `process` | Process Mining | tab `process` | 1 datetime (timestamp) — case/activity are `any`, always satisfiable |
| `oee` | OEE | `/analytics/oee` | 4 numeric (planned time, operating time, total units, good units) |
| `spc` | SPC | `/analytics/spc` | 1 numeric (value) |
| `throughput` | Throughput | `/analytics/throughput` | 1 numeric (units) |
| `scrap` | Scrap & Quality | `/analytics/scrap` | 2 numeric (total units, scrap) |
| `downtime` | Downtime | `/analytics/downtime` | 1 numeric (duration) |
| `shifts` | Shifts | `/analytics/shifts` | 1 numeric (approximated, see below) |
| `rca` | Root Cause Analysis | `/rca-cases` | 4 numeric (same shape as OEE) |
| `plant_summary` | Plant Summary | `/analytics/plant-summary` | 4 numeric (same shape as OEE) |
| `mfg_forecast` | Manufacturing Forecast | `/mfg-forecast` | 1 datetime + 1 numeric |

**Shifts approximation:** the real backend rule (`backend/app/api/routes/shifts.py`,
`ShiftAnalyzeRequest`) is "at least one metric family is mappable" — an OR across several
optional numeric-column groups (units, planned/operating, good/total, scrap, downtime), not a
simple required/optional split. Modeling that OR exactly would need a second constraint type in
the registry for one feature. Approximating as "ready if >=1 numeric column exists" instead —
looser than the real rule, but the real rule is itself permissive (any one family suffices), so
this rarely produces a false positive in practice.

**Excluded from the checklist** (not because they're broken, but because listing them would
always show "ready" and add noise instead of signal):
- **AutoML** (`/automl`) — its actual column-type requirement depends on the task
  (classification/regression/forecast) the user picks *inside* the feature, after opening it. A
  static pre-check would either always pass or occasionally be wrong. Left out rather than faked;
  worth reconsidering if AutoML later exposes a fixed task type per template.
- **KPIs, KPI Dashboards, BI Chart Builder** — operate on arbitrary SQL / any-column selections,
  so they're usable on any non-empty dataset already.
- **Pace Tracking, Downtime Tracking** — confirmed not dataset-column-driven at all; they operate
  on structured DB models (`PaceSession`, `DowntimeIncident`) and only reference a dataset as an
  *output*, not an input mapping.
- Dataset's own tabs — **Overview, Schema, Preview, Health, Query, SQL, Clean, Transform** — have
  no column-role requirements; they work on any dataset already and aren't gated.

## UI

New `"readiness"` tab added to the `TABS` array in `DatasetDetail.tsx`, placed immediately after
`"overview"` (it's meant to guide the user's next step before they explore the other tabs).

`ReadinessPanel` component (same file, same pattern as `Schema`/`HealthPanel`/`SqlPanel`) queries
the *same* `["dataset-health", d.id]` react-query key already used by those three tabs — a no-op
fetch if the user already visited any of them. On load/error it reuses the existing pattern:
`Skeleton` while loading, `<Alert variant="destructive">Failed to compute health.</Alert>` on
failure (matches `HealthPanel` exactly).

Renders one row per feature: label, a ready/not-ready `Badge`, the missing-reason text (blank if
ready), and an action button:
- If `isTab`, the button calls `onOpenTab(key)` (a prop threaded down to switch `DatasetDetail`'s
  own tab state — no navigation).
- Otherwise, the button calls `navigate(to)` (React Router), taking the user to the standalone
  analytics page. Note: this does **not** pre-select the dataset on arrival — that page still
  requires its own dataset picker. Passing the dataset id through and having the target page
  auto-select it is a further UX improvement, explicitly out of scope here.

Not-ready rows still render (greyed out, not hidden) so the checklist reads as a complete picture
of the dataset's potential, not just what currently works.

## Testing

`frontend/src/pages/analytics/readiness.test.ts` (vitest, matching the existing colocated
`*.test.ts` convention seen in `frontend/src/lib/useCountUp.test.ts` and
`frontend/src/pages/workflows/stepConfig.test.ts`). Cases:

1. 4 numeric + 1 datetime + 1 text column → OEE, RCA, Plant Summary, Quality, SPC, Throughput,
   Scrap, Downtime, Shifts, Process Mining, MFG Forecast all `ready`.
2. 2 numeric columns only → OEE/RCA/Plant Summary `not ready`, `missing` mentions "have 2, need 4".
3. 0 datetime columns → Process Mining and MFG Forecast `not ready`, others unaffected.
4. Empty column list → nothing ready, no exceptions thrown.

## Explicit non-goals (for this iteration)

- No backend endpoint or service change — pure frontend, built entirely on the existing
  `/health` response shape.
- No name-based auto-suggestion of which column fills which role.
- No readiness surfaced on the Datasets list page (`frontend/src/pages/analytics/Datasets.tsx`) —
  that would need per-row health data the list page doesn't currently fetch; a reasonable
  follow-up once this lands, not part of this scope.
- No pre-selecting the dataset when navigating to a standalone analytics page from a not-in-tab
  readiness row.
