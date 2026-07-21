# Simulation: Monte Carlo Confidence Bands + Sensitivity Analysis

Status: Approved for planning
Date: 2026-07-19

## Problem

The Simulation feature (`backend/app/services/simulation_engine.py`,
`backend/app/api/routes/simulation.py`, `frontend/src/pages/Simulation.tsx`)
is a deterministic parametric calculator: one baseline, one set of changes,
one point-estimate projection. That's a legitimate and common shape for a
business what-if tool, but it falls short of what "simulation" means in an
operations-research sense - no variability, no confidence bounds, no way to
tell which input actually matters for a given outcome.

This spec adds two capabilities on top of the existing engine, both computed
from the same `run_whatif()` math so there is no second source of truth:

1. **Monte Carlo confidence bands** - instead of a flat projected number,
   run many trials with baseline inputs sampled around their real-world
   variability and report P10/P50/P90 for each output metric.
2. **Sensitivity ("tornado") analysis** - for a chosen outcome metric, rank
   which baseline input moves that outcome the most when swung across its
   plausible range.

## Scope

In scope: variance derivation from a linked dataset's day-by-day history
(with a documented default fallback when no dataset is linked or history is
too thin); a Monte Carlo trial runner and percentile aggregation; a
sensitivity sweep ranked by impact on a caller-chosen target metric; a new
API endpoint that persists results into the existing `SimulationRun.result`
JSON (no schema migration); a second frontend action ("Run with confidence
range") alongside today's Run button.

Out of scope for v1 (explicit decisions, not oversights):
- No variation of `changes` - Monte Carlo only samples the baseline's
  natural variability. `changes` is the deliberate scenario being tested and
  stays a fixed input, same as today's deterministic run.
- No persistence of raw per-trial data - only the aggregated
  P10/P50/P90 per metric is stored. Keeps `SimulationRun.result` the same
  order of magnitude in size as today.
- No distribution-shape configuration (log-normal, skewed, etc.) - every
  sampled input uses a clipped normal distribution. Revisit only if a real
  dataset's variability turns out to be visibly non-normal in practice.
- No background job / async execution - trial counts are capped low enough
  (see Performance) that this runs synchronously inside the request.
- No UI charting library addition - the tornado chart is rendered as plain
  SVG/div bars sized by impact, consistent with how the rest of this page
  already avoids new dependencies.

## Variance derivation

New function in `simulation_engine.py`:

```python
def derive_variance_from_rows(
    headers: list[str], rows: list[dict[str, Any]], mapping: dict[str, str | None] | None = None,
) -> dict[str, float]:
    """Per-metric coefficient of variation (std/mean), derived from grouping
    rows by day and computing oee_pct/defect_rate_pct/throughput_per_shift
    per day - same day-grouping and column-mapping as derive_baseline_from_rows."""
```

Reuses `auto_map_columns`/`_day_key` from the existing baseline-derivation
code path. For each of `oee_pct`, `defect_rate_pct`, `throughput_per_shift`,
compute the metric per calendar day, then `stdev(daily_values) /
mean(daily_values)`. Requires at least 3 distinct days; below that, the
metric falls back to its default CV (see below) since a 1-2 point "spread"
isn't meaningful.

Metrics not covered by dataset history get a fixed default CV:

```python
_DEFAULT_CV: dict[str, float] = {
    "oee_pct": 0.05,
    "defect_rate_pct": 0.15,
    "throughput_per_shift": 0.05,
    "demand_units_per_day": 0.10,
}
```

`shifts_per_day`, `shift_hours`, `workers_per_shift`, `avg_wage_per_hour`,
`scrap_cost_per_unit`, `raw_material_cost_per_unit`,
`selling_price_per_unit` are treated as fixed (CV = 0) in both the
data-derived and default case - they're policy/contract values a plant sets,
not something that drifts day to day on its own. The response reports
`variance_source: "dataset" | "default"` per the honesty requirement below,
computed as `"dataset"` only if the linked simulation has a `dataset_id`
*and* at least one metric had ≥3 days of history to derive from; otherwise
`"default"`.

## Monte Carlo trial runner

Refactor `run_whatif`'s middle section (baseline math through
`p_capacity_gap`) into a new pure function:

```python
def _project(baseline: dict[str, Any], changes: dict[str, Any]) -> dict[str, float]:
    """The metric computation only - no insights, no baseline/projected/delta
    wrapper. run_whatif() becomes _project(baseline, {}) for its baseline_snap
    and _project(baseline, changes) for its projected_snap - behavior-preserving
    refactor, existing tests must still pass unmodified."""
```

New function:

```python
def run_monte_carlo(
    baseline: dict[str, Any], changes: dict[str, Any], variance: dict[str, float],
    trials: int = 2000,
) -> dict[str, dict[str, float]]:
    """Sample each varying baseline metric from a clipped normal distribution
    (random.gauss(mean, cv * mean)) `trials` times, apply `changes`
    deterministically on top via _project(), and return per-metric P10/P50/P90
    over the resulting projected values. oee_pct clipped to [0, 100], all
    other sampled values clipped to >= 0."""
```

Percentiles via stdlib `statistics.quantiles(values, n=100)` - no numpy
dependency. `trials` clamped server-side to `[100, 10000]`.

## Sensitivity ("tornado") analysis

New function:

```python
def run_sensitivity(
    baseline: dict[str, Any], changes: dict[str, Any], variance: dict[str, float],
    target_metric: str, trials: int = 2000,
) -> list[dict[str, Any]]:
    """For each baseline parameter with a non-zero variance range, hold
    everything else at baseline, evaluate _project() with that parameter at
    baseline-1sd and baseline+1sd (using the same variance dict Monte Carlo
    uses, so the two features agree on 'plausible range'), and rank by
    |high[target_metric] - low[target_metric]|. Returns the top 8 as
    [{parameter, baseline_value, low_value, high_value, impact}, ...]
    sorted descending by impact."""
```

`target_metric` must be a key present in `_project()`'s output (e.g.
`daily_margin`, `daily_capacity`, `utilisation_pct`); anything else is a 400
at the API layer, not a silent no-op.

## API

New endpoint on the existing router (`backend/app/api/routes/simulation.py`):

```
POST /simulation/{simulation_id}/run/monte-carlo
Body: { changes: dict = {}, target_metric: str = "daily_margin", trials: int = 2000 }
```

Handler:
1. Load the simulation (`get_simulation_or_404`, same as today's `/run`).
2. If `simulation.dataset_id` is set, load the dataset via
   `ds_service.get_dataset_or_404` + `ds_service.dataframe_for`, call
   `derive_variance_from_rows`. Otherwise (or if that yields no
   dataset-derived metrics), build `variance` entirely from `_DEFAULT_CV`.
3. Validate `target_metric` is a known projected-metric key -> 400 if not.
4. Clamp `trials` to `[100, 10000]`.
5. Call `run_whatif()` (unchanged, gives `baseline`/`projected`/`delta`/`insights`),
   `run_monte_carlo()` (gives `bands`), `run_sensitivity()` (gives ranked list).
6. Persist via the **existing** `service.execute_simulation` path, extended
   to accept a pre-computed `result` dict instead of always calling
   `run_whatif` itself - the extra `bands`/`sensitivity`/`variance_source`
   keys just ride along in the same `SimulationRun.result` JSONB column.
   No new column, no migration.

Response shape (superset of today's `/run` response):

```json
{
  "scenario_name": "...",
  "baseline": { "...": "..." },
  "projected": { "...": "..." },
  "delta": { "...": "..." },
  "insights": ["..."],
  "bands": {
    "daily_capacity": { "p10": 2850.0, "p50": 3000.0, "p90": 3150.0 },
    "...": "..."
  },
  "sensitivity": {
    "target_metric": "daily_margin",
    "ranked": [
      { "parameter": "oee_pct", "baseline_value": 75.0, "low_value": 12800.0, "high_value": 16100.0, "impact": 3300.0 }
    ]
  },
  "variance_source": "dataset"
}
```

Existing endpoints (`/simulation/run` ad-hoc, `/simulation/{id}/run`
deterministic, CRUD, compare, export) are unchanged - this is additive.
`/simulation/{id}/runs/{run_id}/export` continues to work unmodified since
it only reads `result["baseline"|"projected"|"delta"|"insights"]`, which are
still present; the new keys are simply not included in the exported table
(acceptable for v1 - export stays a point-estimate table).

## Frontend

`frontend/src/pages/Simulation.tsx`: a second button, "Run with confidence
range," next to the existing "Run" button - always enabled (falls back to
default variance without a linked dataset, matching the backend). A
`target_metric` dropdown (populated from `METRIC_LABELS`, defaulting to
`daily_margin`) appears next to it for the tornado sweep.

Results rendering:
- Each metric in the results table shows `2,850 – 3,150` under the point
  value when `bands` is present, with the point value still shown as
  today (e.g. "3,000 (2,850–3,150)").
- A small badge reading "from your data" or "default estimate" next to the
  results header, driven by `variance_source` - the idiot-proofing
  transparency this whole feature exists to provide.
- A tornado section below the results table: one horizontal bar per ranked
  sensitivity entry, bar width proportional to `impact`, labeled with the
  parameter name and its low/high values. Plain divs with inline width
  percentages - no charting library.

## Files touched

Backend:
- `backend/app/services/simulation_engine.py` - `_project` extraction
  (refactor of `run_whatif`'s core), `derive_variance_from_rows`,
  `run_monte_carlo`, `run_sensitivity`.
- `backend/app/services/simulation.py` - new `execute_simulation_monte_carlo`
  function, parallel to the existing `execute_simulation`: same
  get-simulation + build `SimulationRun` + commit shape, but building
  `result` from `run_whatif()` + `run_monte_carlo()` + `run_sensitivity()`
  instead of `run_whatif()` alone. Kept separate from `execute_simulation`
  rather than adding a mode flag to it, so the existing deterministic path's
  signature and tests are untouched.
- `backend/app/api/routes/simulation.py` - new
  `POST /simulation/{simulation_id}/run/monte-carlo` endpoint + request/response
  models.

Frontend:
- `frontend/src/pages/Simulation.tsx` - new button, target-metric dropdown,
  band display, variance-source badge, tornado bars.

No new files, no new dependencies, no database migration.

## Testing

Backend (`pytest`, following `test_simulation.py` conventions):
- `derive_variance_from_rows`: synthetic multi-day OEE CSV with known
  day-to-day spread -> assert computed CV matches hand-calculated std/mean;
  fewer than 3 days -> falls back to `_DEFAULT_CV`.
- `run_monte_carlo`: bands always satisfy `p10 <= p50 <= p90` per metric
  across repeated calls; `oee_pct` samples never produce a projected result
  outside physically valid ranges; `trials=100` vs `trials=10000` both
  return well-formed output (just noisier at low N - no assertion on that,
  only on shape/ordering).
- `run_sensitivity`: hand-constructed baseline where one parameter is known
  to dominate `daily_margin` (e.g. `selling_price_per_unit` swung wide) ->
  assert it ranks first.
- `_project` refactor: existing `run_whatif`-based tests in
  `test_simulation.py` must pass unmodified (behavior-preserving refactor
  check).
- API: `POST /simulation/{id}/run/monte-carlo` persists a `SimulationRun`
  with `bands`/`sensitivity`/`variance_source` in `result`, visible via
  `GET /simulation/{id}/runs`; invalid `target_metric` -> 400; simulation
  with no `dataset_id` -> `variance_source == "default"` and the call still
  succeeds; tenant isolation (same pattern as
  `test_simulation_run_export_tenant_isolation`).

Frontend: no automated test runner in this repo (consistent with prior specs'
precedent). Verify manually in-browser: `tsc -b` typecheck, then run a
Monte Carlo simulation both with and without a linked dataset and confirm
the badge and bands differ accordingly, and confirm the tornado bars
reorder when switching `target_metric`.
