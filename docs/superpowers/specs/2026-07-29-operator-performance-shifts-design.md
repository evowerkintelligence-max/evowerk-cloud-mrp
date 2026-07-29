# Operator Performance on Shift Comparison

Status: Approved for planning
Date: 2026-07-29

## Problem

The Shift Comparison engine (`backend/app/services/shifts.py`,
`backend/app/api/routes/shifts.py`,
`frontend/src/pages/analytics/Shifts.tsx`) compares shifts head-to-head on
OEE, quality, throughput, and downtime. It groups a dataset by one dimension,
the shift column, and offers an optional per-machine breakdown *within* each
shift.

There is no way to ask the same questions about people. A supervisor who wants
to know which operators are struggling, whether an operator's output is
trending down, or what is stopping a given operator has to leave this page.
The OEE page's RCA feature already treats Operator as a first-class comparable
dimension alongside Machine, Shift, and Material Batch
(`backend/app/api/routes/oee.py`, `dim_col_map`), so the concept exists in the
product but not in the engine where head-to-head comparison lives.

The data is already there. The `plant` demo dataset's sniffed schema includes
an `operator` column alongside `date`, `machine`, `line`, `shift`,
`product_type`, and `batch_id`. Pace Tracking
(`backend/app/services/pace_tracking.py`) also materializes every stopped
session into a Dataset carrying an `operator` column.

## Scope

In scope: an optional operator column mapping; full parity between the shift
and operator dimensions for ranking, tabulation, trend, downtime breakdown,
and forecasting; a sample-size guard so sparse operators do not win or lose
rankings by noise; confounder context showing which machines and shifts an
operator actually ran; and participation in the page's existing auto-refresh
and TV mode.

Out of scope for this spec (explicit product decisions, not oversights):

- **The live operator board.** Who is on the floor right now, pacing ahead or
  behind target, paused for a breakdown. That reads `GET /pace/sessions`
  rather than the mapped dataset, needs no dataset and no column mapping, and
  therefore does not belong inside `EngineScreen`, which only renders results
  after a dataset is chosen and Compare is clicked. The backend for it is
  already complete and has no UI consuming it. It gets its own spec next.
- **Operator-keyed hourly pace, live handover, weekday average, and the
  schedule editor.** All four key off wall-clock shift windows
  (`resolveCurrentShift`, `ShiftSchedule`) that operators do not have. They
  stay shift-only and are unaffected by the dimension toggle.
- **Per-operator identity or access control.** Operator is a free-text label
  in a dataset column, not a user account. No linking to `users`, no
  per-operator login, no restricting an operator to their own numbers.

## Design

### Discovery: zero configuration

`operator_col` joins the `FIELDS` list in `Shifts.tsx` with hints
`[/operator/i, /worker/i, /employee/i, /technician/i, /associate/i]`.

`autoMapColumns` (`frontend/src/pages/analytics/shared.tsx`) already fills
mapped columns by regex when a dataset is selected, so a dataset with an
`operator`-ish column populates the field with no user action. The existing
Data preview card then shows those operator values in its first five rows, so
a mis-detected column is visible before anything is computed.

When the operator column is mapped, a `Compare by: Shift | Operator` toggle
renders at the top of the results, defaulting to Shift so the page is
unchanged for existing users. When it is not mapped, the toggle renders
disabled with a single line naming the column to add and noting that Pace
Tracking produces operator-labelled datasets.

### Backend: one grouping, called twice

`analyze_shifts` currently interpolates a hardcoded `shift_key` expression
into five separate DuckDB queries (per-shift, downtime-by-category,
per-machine, day-bucketed trend, hourly). The bucket-building work is
extracted into a helper parameterized by the grouping expression, then invoked
once for shift and once for operator. Same aggregates, same
`_derive_shift_metrics`, same `_primary_key` ranking axis. This is a
refactor plus a second call, not a second copy of the SQL-building code.

Operators are grouped case-insensitively. The existing shift key is
`COALESCE(NULLIF(TRIM(CAST(col AS VARCHAR)), ''), 'Unknown')`, which trims but
preserves case. That is correct for shift labels like `A`/`B`/`Night`, which
come from a fixed vocabulary. Human names are entered by different people at
different times, so `J. Kim` and `j. kim` are one person and must not rank as
two. The operator key applies the same COALESCE/NULLIF/TRIM and additionally
folds case for grouping, while displaying the most frequent original spelling
so the UI shows `J. Kim` rather than `j. kim`.

`ShiftAnalyzeRequest` gains `operator_col: Optional[str]`.
`ShiftAnalyzeResponse` gains:

- `operators: List[OperatorResult]`, same shape as `ShiftResult` including
  `downtime_by_category`
- `best_operator`, `worst_operator`
- `operator_trend: List[OperatorTrendPoint]`
- `operator_min_rows: int`, the computed threshold
- `operators_below_threshold: int`

No new endpoint. One request returns both dimensions when both columns are
mapped.

### Fair ranking

Operator data differs from shift data in a way that breaks naive ranking.
Shift labels are a small fixed vocabulary with roughly balanced row counts.
Operator names are free text, higher cardinality, and unevenly distributed: a
new hire with three rows sits in the same list as someone with two thousand.
Ranking them together means best and worst are frequently decided by noise,
on a screen that names people.

The threshold is `max(3, median_operator_rows // 5)`, computed from the data
rather than entered by the user, where `median_operator_rows` is the median
row count across *every* operator present, including sparse ones.

Operators below the threshold keep their table row with all their numbers
intact, and remain selectable for the trend chart, downtime breakdown, and
forecast, since a supervisor looking up one specific person should still see
their data. They are excluded only from the two places where a small sample
misleads by comparison: best/worst selection and the ranked chart. The UI
states how many were set aside and why.

When more than 15 operators clear the threshold, the ranked chart shows the
top 8 and bottom 8 rather than an unreadable 40-bar chart. The table always
lists every operator.

Both numbers are deliberate judgment calls with no user-facing knob, and are
cheap to change.

### Confounder context

Selecting an operator shows which machines and which shifts they actually ran,
reusing the per-machine aggregation the engine already computes.

This is not decoration. An operator confined to the worst machine on nights
will rank low through no fault of their own, and a ranking of people that
hides that fact invites the wrong conclusion. The supervisor needs to see the
attribution before acting on the number.

### Live behaviour

`EngineScreen` already provides an Auto-refresh (30s) checkbox with a live
pulse dot and a fullscreen TV mode. The dimension toggle's selection survives
a refresh, so an operator leaderboard left on screen keeps updating in place.

This is only genuinely live when something is feeding the underlying dataset,
for example Downtime Tracking or a materialized Pace session. A static
uploaded CSV never changes. The page already carries this caveat for the
handover card and it applies unchanged here.

### Forecast

`ShiftForecastDataset` (`backend/app/models/shift_forecast.py`) is keyed
`(org_id, shift)` and points at the Dataset that `prepare_forecast_source`
overwrites with that shift's latest trend series. It gains a `dimension`
column, `"shift"` or `"operator"`, defaulting to `"shift"`, added to the
composite primary key via an Alembic migration.

Without the added key column, an operator whose name matches a shift label
would silently overwrite that shift's materialized forecast dataset.

`prepare_forecast_source` and `POST /analytics/shifts/forecast-source` take a
`dimension` field alongside the existing value, and read from
`operators`/`operator_trend` when it is `"operator"`.

### Label accuracy

The run button's `runLabel` changes from `"Compare shifts"` to `"Compare"`,
since the same click now computes both dimensions.

## Testing

Backend:

- An operator-mapped analysis populates `operators`, `best_operator`,
  `worst_operator`, and `operator_trend`.
- An operator with 2 rows against a set whose median is well above the floor
  appears in `operators` but is excluded from `best_operator`/`worst_operator`,
  and `operators_below_threshold` counts it.
- `J. Kim` and `j. kim` in the same dataset produce one operator row, labelled
  with the more frequent spelling.
- A shift and an operator sharing a name produce two distinct
  `ShiftForecastDataset` rows and two distinct materialized datasets.
- An analysis with no operator column mapped returns the same response shape
  as today, with the operator fields empty and no error.

Frontend: no new test infrastructure. Existing render-test patterns apply if
this page adopts one.

## Open questions

None blocking. The threshold divisor and the 15-operator chart cutoff are
judgment calls flagged above; the real operator cardinality in production data
was not measurable at spec time, because the demo dataset's file was lost in
the ephemeral-storage incident that motivated
`infra/terraform/azure/storage.tf`.
