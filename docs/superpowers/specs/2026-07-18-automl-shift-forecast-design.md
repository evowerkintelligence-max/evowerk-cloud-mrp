# AutoML Shift Forecast — Design

## Context

Shift-tracking analytics (per-shift trend history, live handover Pareto,
per-machine shift tiles, intra-shift pacing) already shipped in commit
`ab87883` on `Shifts.tsx` / `shifts.py` — nothing to build there.

Separately, `AutoML` (`automl_engine.py`, `automl_runner.py`,
`api/routes/automl.py`) already has a fully generic `forecasting` task
(lag/rolling features, recursive multi-step trajectory, drift check) that
works over any `Dataset` with a time column and a target column. A user can
already point it at any uploaded or live-materialized dataset today — the
gap isn't capability, it's that reaching it from the Shifts page requires
manually building a dataset and mapping columns by hand.

## Goal

Add a "Forecast" card to the Shifts page, scoped to whichever shift is
currently selected (reusing the existing Handover dropdown), that projects
that shift's primary metric (OEE > FPQ > units > downtime — same ranking
`shifts.py._primary_key` already uses) forward using the existing AutoML
forecasting engine. No changes to the AutoML engine itself.

## Non-goals

- Forecasting multiple shifts at once / overlaying projections on the
  existing multi-shift "Shift trend" chart.
- A metric picker — the card always forecasts the page's primary metric,
  same one the rest of the page already ranks shifts on.
- Any changes to `automl_engine.py`'s forecasting logic. This feature is
  pure plumbing: materialize existing computed data into a `Dataset`, then
  drive the existing training/polling/trajectory endpoints.

## Architecture

One new table, one new endpoint, one new frontend card. Everything else
reuses AutoML's existing engine and existing frontend hooks unmodified.

### Data model

`backend/app/models/shift_forecast.py`:

```python
class ShiftForecastDataset(Base):
    """One row per (org, shift) - points at the Dataset that
    prepare_forecast_source() keeps overwriting with that shift's latest
    day-bucketed trend series, mirroring DowntimeLiveDataset's one-dataset-
    per-key materialization pattern."""

    __tablename__ = "shift_forecast_datasets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    )
    shift: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
```

New Alembic migration for `shift_forecast_datasets`; register the model
module in `backend/tests/conftest.py`'s model-import list (drives
`Base.metadata.create_all()` for the in-memory test DB), same as every
prior new-table plan in this repo.

### Backend endpoint

`POST /analytics/shifts/forecast-source` in `backend/app/api/routes/shifts.py`:

- **Request**: same fields as `ShiftAnalyzeRequest` (dataset_id + column
  mappings) plus `shift: str` — the shift to scope to.
- **Behavior** (new function `shifts_service.prepare_forecast_source`):
  1. Resolve the dataset, call the *existing* `shifts_service.analyze_shifts`
     (zero new aggregation SQL — reuses the same `trend` computation that
     already powers the "Shift trend" chart).
  2. 400 if `timestamp_col` wasn't mapped (`has_timestamp` false in the
     underlying analysis) — "map a timestamp column to enable forecasting."
  3. Filter `trend` to the requested `shift`; 400 if empty — "no trend data
     for shift '<shift>' to forecast from."
  4. Determine the primary metric via the existing `_primary_key()` helper;
     build a two-column CSV (`period,value`) from the filtered trend points.
  5. Look up `ShiftForecastDataset` for `(org_id, shift)`. If absent, create
     a new `Dataset` (name `"Shift Forecast Source: <shift>"`) via
     `ds_service.create_dataset` and insert the mapping row. If present,
     `ds_service.overwrite_dataset_file` the existing dataset — identical
     control flow to `downtime_tracking._materialize`.
- **Response**: `{dataset_id, target_column: "value", time_column: "period", metric_label, rows}`.

No other backend changes. Training, run polling, and trajectory fetching
all go through the *existing* `/automl/runs` POST/GET and
`/automl/runs/{id}/forecast-trajectory` endpoints, unmodified.

### Frontend

New card on `Shifts.tsx`, placed near the existing Handover/Hourly-pace
cards (same single-shift scoping, reusing `handoverCandidate`):

1. On expand, call `forecast-source` for `handoverCandidate` to get
   `{dataset_id, target_column, time_column, metric_label}`.
2. Check the org's existing AutoML runs (`GET /automl/runs`, filtered
   client-side by `dataset_id` and `task === "forecasting"`, latest
   `created_at` wins) — if a successful run already targets this exact
   dataset, show it immediately instead of retraining.
3. "Train forecast" button calls the existing `useCreateAutoMLRun()` with:
   ```
   { name: "Shift forecast: <shift>", dataset_id, task: "forecasting",
     target_column: "value", feature_columns: [], time_column: "period",
     horizon: 14, forecast_strategy: "recursive", thoroughness: "fast",
     handle_class_imbalance: true, algorithms: null, time_budget_seconds: null,
     search_method: "random" }
   ```
4. Poll via the existing `useAutoMLRun(id)` hook until `status === "success"`
   (or `"failed"`, surfaced as-is).
5. On success, call the existing `useForecastTrajectory(id)` hook and
   render `points` as a dashed projection appended after that shift's own
   history (from the already-client-side `byShiftTrend` map), in a small
   chart local to this card — deliberately *not* merged into the existing
   multi-shift "Shift trend" chart, so it doesn't imply every shift shown
   there has a projection.

No new frontend API file needed beyond one small hook
(`useShiftForecastSource`) in `frontend/src/pages/analytics/shared.tsx` (or
inline in `Shifts.tsx`) for the one new endpoint — everything else is
existing hooks from `frontend/src/pages/automl/api.ts`.

## Error handling

- Missing timestamp mapping / no trend rows for the shift → 400 from
  `forecast-source`, shown inline on the card before any run starts.
- Insufficient history for a recursive trajectory — `automl_engine.py`
  requires at least `N_LAGS + 1` = 15 day-bucketed rows
  (`forecast_trajectory`'s `min_history` check) — surfaces as the run's
  normal `status: failed` / `error` field, rendered as-is. This is a real,
  visible limit: a shift needs roughly two-plus weeks of dated history
  before a forecast is possible at all. The card should say so up front
  (e.g. "Forecasting needs at least 15 days of history for this shift")
  rather than only after a failed run.
- Model training failure for any other reason — already handled by
  AutoML's existing run-status/error path; nothing new to build.

## Testing

- `backend/tests/test_shift_forecast.py`: `forecast-source` builds the
  expected `period,value` CSV from a known trend; a second call for the
  same `(org, shift)` overwrites the same `dataset_id` rather than creating
  a new one; 400 without a timestamp column mapped; 400 when the requested
  shift has no trend rows.
- Frontend: `tsc -b` typecheck + manual in-browser verification pass (no
  automated frontend test runner in this repo — matches the
  pace-tracking/downtime-tracking precedent).
