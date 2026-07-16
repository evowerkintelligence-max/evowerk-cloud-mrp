# Manufacturing-Centric AutoML

Status: Approved for planning
Date: 2026-07-17

## Problem

`backend/app/services/automl_engine.py` is a solid, general-purpose sklearn
bake-off (CV, randomized/Bayesian hyperparameter search, XGBoost/LightGBM,
SHAP, stacking) exposed through `frontend/src/pages/AutoML.tsx`. It works,
but it's a raw data-science tool: pick any Dataset, pick a target column,
pick algorithms, read a leaderboard. Our target users (process engineers,
quality engineers, reliability technicians) don't have ML vocabulary, and
the tool has no opinion about manufacturing - it doesn't know what scrap,
downtime, or a machine failure look like, even though the app already
tracks all three.

Competitors in this space (TIBCO Data Science, Weidmuller AutoML
ModelBuilder, DataRobot) instead ship a small number of guided, no-code
"use case" workflows wired directly into the customer's process data, with
predictions explained and acted on in manufacturing language, not ML
metrics.

Separately, this app already has a bespoke, hand-built predictive-maintenance
feature (`backend/app/services/predictive.py`) that duplicates a chunk of
what the generic AutoML engine does (train a classifier, explain drivers)
with a single hardcoded model instead of a bake-off - built before AutoML
existed, now inconsistent with it.

## Scope

In scope:
- A config-driven **template registry** so "guided AutoML use case" becomes
  a declarative unit, not a bespoke page.
- Three templates: **Scrap/Defect Prediction**, **Predictive Maintenance**
  (re-platformed off the shared engine, replacing `predictive.py`'s
  hardcoded model), **Downtime/OEE Risk**.
- **Plain-English explanations** and a **canned-recommendation layer** per
  template, built on the engine's existing SHAP/permutation-importance
  output.
- **Rare-event / severe-imbalance handling**: automatic anomaly-detection
  fallback when there are too few positive examples for a classifier to be
  trustworthy, plus imbalance-aware primary metrics.
- **Time-series data cleaning** (gap detection, resampling, outlier
  smoothing, imputation) for sensor-style template inputs.
- **Live scoring + drift monitoring**, wired into the *existing*
  `RealtimeStream` / industrial connectors (`backend/app/services/realtime.py`,
  `backend/app/services/connectors/*`) and the *existing*
  `automl_engine.check_data_drift`.
- **Alerting**, wired into the *existing* `AlertRule` /
  `eval_stream_metric` / `_fire_webhook` system in
  `backend/app/services/alerts.py` - including the existing andon/relay
  webhook output.
- The existing generic AutoML page stays, relabeled as an "Advanced /
  Custom" mode for power users; `automl_engine.py`'s core bake-off logic is
  not rewritten, only called with different, template-supplied config.

Out of scope for this spec (explicit product decisions, not oversights -
see the phased roadmap discussed during design):
- **Native PLC/OPC-DA/Modbus wire-protocol work.** The connector layer
  (`connectors/pi_connector.py`, `opcua_connector.py`, `opcda_connector.py`,
  `mqtt_connector.py`, `sqlserver_connector.py`, `oracle_connector.py`,
  `rest_connector.py`) already exists and already covers this; nothing new
  is being built at the protocol level, we're only wiring AutoML into
  streams built on top of connectors that already exist.
- **CMMS integration (SAP/Maximo work-order creation).** Needs a named
  target system and that system's API access before it's spec-able; not
  requested for this phase.
- **Automatic background retraining on a fixed schedule independent of
  drift.** Phase 3.5 retrains *on detected drift*, not on a timer; a
  timer-based policy can be layered on later without redesign.
- **A UI to edit recommendation rule text.** Recommendation rules are
  code-defined per template for v1 (a small Python dict), not
  database-backed/editable - see "Explainability & recommendations" below.
  Cheap to move into a DB table later if a customer needs to customize
  wording; not worth the CRUD/UI surface until someone asks.
- **Any software claim about safety-interlock behavior.** See "Safety
  boundary" below - this is a permanent constraint, not a deferred feature.

### Safety boundary (permanent, not a phase)

The system is **advisory-only**. Nothing built under this spec ever gets
write access to a control loop, a PLC, or any actuator. A predicted "85%
failure risk" can open a work order or fire an andon light (see
Alerting); it can never itself stop or start equipment. Physical safety
interlocks remain entirely on the plant's own SIL-rated hardware,
independent of and unaware of this software. This boundary is enforced by
never building the write path, not by a runtime check.

## Architecture

```
Frontend: one generic wizard + one generic results view
  Step 1: pick Dataset (or bind a RealtimeStream) -> Step 2: map columns
  per the template's declared roles, friendly-label each -> Step 3: run

Backend: template registry (backend/app/services/automl_templates/)
  each template = {id, label, required column roles, preprocess(),
                    postprocess(), recommendation_rules}
       |              |                |
   Scrap          Predictive        Downtime
 Prediction       Maintenance         Risk
       |______________|________________|
                       v
  Shared pipeline (new):
  1. Time-series data-quality clean (timeseries_quality.py)
  2. Rare-event routing: anomaly vs. classification (automl_rare_event.py)
  3. automl_engine.train_automl_baseline()            <- unchanged
  4. Plain-English explain + recommendation (automl_explain_nl.py)
                       v
              AutoMLRun (persisted)
                       v
       automl_realtime.py (Phase 3.5, optional):
       live scoring + drift-check against a bound RealtimeStream
                       v
       alerts.py: risk score exposed as a stream metric ->
       existing eval_stream_metric / andon webhook, unchanged
```

`automl_engine.py` is called, never modified, by every template. Each
template is a thin adapter: it knows how to turn a mapped Dataset into
`(df, target_col, task, feature_cols)` and how to turn the engine's output
into domain language - nothing else.

## Data model

Extend `backend/app/models/automl_run.py`:
```python
class AutoMLRun(Base):
    ...
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_stream_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("realtime_streams.id", ondelete="SET NULL"), nullable=True
    )
```
`template_id=None` means "generic/Advanced run" (today's behavior,
unchanged). `meta` (already a free-form JSON column) gains new keys:
`data_quality_notes`, `explanation_sentence`, `recommendation`,
`rare_event_routing` (`"classification" | "anomaly"`) - no new columns
needed for these since `meta` already exists for exactly this kind of
engine-output metadata.

New table for live bindings (Phase 3.5), `backend/app/models/automl_live_binding.py`:
```python
class AutoMLLiveBinding(Base):
    __tablename__ = "automl_live_bindings"
    id: UUID (pk)
    org_id: UUID (fk orgs.id, cascade delete, indexed)
    run_id: UUID (fk automl_runs.id, cascade delete, indexed)
    stream_id: UUID (fk realtime_streams.id, cascade delete, indexed)
    metric_name: str                    # e.g. "automl_risk_score" - published into
                                         # StreamRunner's metrics dict
    drift_severity_trigger: str = "moderate"   # "moderate" | "high" - triggers retrain
                                         # when check_data_drift's existing overall_severity
                                         # (PSI-based: <0.1 low, 0.1-0.25 moderate, >0.25 high
                                         # per column) reaches this level
    drift_check_interval_minutes: int = 1440   # default: daily
    last_scored_at: datetime | None
    last_drift_check_at: datetime | None
    last_drift_status: str | None       # "ok" | "drifted" | None (never checked)
    auto_retrain_on_drift: bool = False
    enabled: bool = True
```
One migration adds both the `automl_runs` columns and this table.

Recommendation rules are **not** a table - each template module exports a
plain Python list of `(driver_name_pattern, direction, action_text)`
tuples plus one generic fallback string. Simplest thing that works; a DB
table is a fast-follow if a customer ever needs to edit wording without a
deploy.

## The three templates

All three follow the same adapter shape:
```python
@dataclass
class AutoMLTemplate:
    id: str
    label: str
    roles: list[ColumnRole]              # {key, label, required, dtype_hint}
    preprocess: Callable[[pd.DataFrame, dict], PreprocessResult]  # -> df, target_col, task, feature_cols, notes
    postprocess: Callable[[dict, dict], dict]  # engine result + role config -> domain result
    recommendation_rules: list[RecommendationRule]
    is_timeseries: bool                  # gates the Phase 3 cleaning step
    min_positive_examples: int = 20      # rare-event routing cutoff (see below);
                                          # per-template override of the global default
```

**Scrap / Defect Prediction** (`automl_templates/scrap_prediction.py`)
- Roles mirror `scrap.py`'s existing request shape: `date_col`,
  `total_units_col`, `scrap_col`, optional `reason_col`/`machine_col`, plus
  any other numeric/categorical columns as candidate features.
- User picks task framing in the wizard: binary ("will this lot/run
  scrap") or regression ("predicted scrap rate this shift").
- `is_timeseries = True` when `date_col` is mapped (usual case).
- `postprocess()` adds a "Pareto of predicted drivers" alongside the
  existing engine leaderboard/SHAP, echoing `scrap.py`'s existing
  reason-Pareto.

**Predictive Maintenance** (`automl_templates/predictive_maintenance.py`)
- Roles identical to `predictive.py.analyze()`'s current signature:
  `date_col`, `machine_col`, `planned_time_col`, `operating_time_col`,
  `total_units_col`, `good_units_col`, optional `failure_col`,
  `downtime_category_col`, plus new optional sensor columns
  (vibration/temperature/etc. - not used by today's hardcoded model at
  all).
- `preprocess()` reuses `predictive.py`'s existing OEE/day aggregation and
  `is_failure`/`scrap_rate`/`dt_rate` feature engineering verbatim (moved,
  not rewritten), now also passing through any mapped sensor columns as
  extra features.
- `postprocess()` keeps `predictive.py`'s existing RUL calculation
  (`_rul_days`), cost summary, and top-driver logic **unchanged** as a
  layer reading the new engine's leaderboard/SHAP output instead of the
  old `_ml_analyze`'s single-model output.
- `predictive.py`'s public `analyze()` function and the existing
  `/predictive` route/page keep working with the same external contract -
  internally, `_ml_analyze` is replaced by a call into this template's
  `preprocess -> train_automl_baseline -> postprocess` path. This is the
  one template touching already-shipped code; see Testing.

**Downtime / OEE Risk** (`automl_templates/downtime_risk.py`)
- Roles pull from `DowntimeLiveDataset`'s materialized columns
  (`machine`, `reason`, `shift`, `started_at`/`ended_at`) plus optional
  `pace_tracking` columns.
- Task framing: "will there be an unplanned incident next shift"
  (classification, usually rare -> anomaly routing applies) vs.
  "predicted OEE next period" (regression, not rare).
- `postprocess()` produces a per-machine risk ranking instead of a single
  score, since this template is inherently multi-machine.

## Explainability & recommendations (`automl_explain_nl.py`)

Input: the engine's SHAP/permutation-importance output (already computed
by `train_automl_baseline`) + the wizard's friendly column labels (Section
2 of the design discussion - defaults to a cleaned-up header if the user
doesn't override it, e.g. `vibration_sensor_3` -> "Vibration Sensor 3").

1. Take the top 1-2 drivers by absolute importance and their direction
   (increases/decreases the predicted outcome).
2. Render a template sentence: `"<Driver 1> <rising/high/low> + <Driver 2>
   <rising/high/low> -> <risk%> risk of <template's outcome label> within
   <horizon>."` Falls back to a single-driver sentence, then a generic
   "Top factors: X, Y, Z" list, if fewer than 2 usable drivers exist.
3. Match the top driver (by name substring + direction) against the
   template's `recommendation_rules`; use the matched action text, or a
   generic `"Investigate <driver> - it's the strongest predictor."`
   fallback.

This layer **can never fail the run**: every step above degrades to a
simpler fallback rather than raising. If it produces nothing useful, the
run still succeeds with `explanation_sentence=None` and the existing
leaderboard/SHAP data intact.

## Rare-event / imbalance handling (`automl_rare_event.py`)

Sits between a template's `preprocess()` and `automl_engine.train_automl_baseline()`.

1. Count positive/event rows in the derived target.
2. If count `< 20` (default, tunable per template): route to unsupervised
   anomaly scoring - fit an `IsolationForest` on the majority ("normal")
   rows, score all rows by anomaly percentile, and return a result shape
   compatible with the engine's normal output (`leaderboard` with one
   "Isolation Forest (anomaly)" entry, `meta` carrying an anomaly score
   instead of a class probability) so `postprocess()` and the explain
   layer don't need to special-case it. Requires >= 50 "normal" rows to
   fit at all; below that, the run fails with an explicit message - there
   isn't enough data to establish what "normal" looks like, so nothing
   downstream (anomaly or otherwise) can be trusted.
3. If count `>= 20`: proceed into `train_automl_baseline()` as today, with
   two additions to the engine call: an oversampling option alongside the
   existing `class_weight='balanced'`, and - when the imbalance ratio is
   severe (>10:1) - the primary/selection metric switches from
   `f1_weighted` to an imbalance-aware metric (minority-class F1 / average
   precision), since `f1_weighted` can still be dominated by majority-class
   performance at extreme ratios.
4. Either path records which one ran in `AutoMLRun.meta["rare_event_routing"]`
   plus a note explaining why, following the engine's existing
   `notes`-list convention (e.g. "Dataset too small for cross-validation").

## Time-series data quality (`timeseries_quality.py`)

Runs inside `preprocess()`, only when the template is `is_timeseries=True`
and has sensor-like numeric columns mapped:
1. Detect the typical sampling interval from the time column.
2. Flag gaps beyond N x that interval (default N=3) rather than silently
   bridging them.
3. Resample/align to a regular grid.
4. Smooth outlier spikes per numeric column via a rolling-median (Hampel)
   filter.
5. Interpolate remaining short gaps; leave long gaps as `NaN` (dropped by
   the engine's existing row-level `dropna` on the target, or imputed by
   the engine's existing `SimpleImputer` on features - no change needed
   there).
6. Return a `data_quality_notes` list (e.g. "3.2% of readings were missing
   and interpolated", "14 gaps > 3h were left out of training") - the same
   honesty-first pattern as the rest of the engine.

A column that's non-numeric or almost entirely gaps is excluded with a
note (mirrors `automl_engine._screen_features`'s existing "exclude with a
note" behavior) rather than crashing the run.

## Real-time scoring & drift (`automl_realtime.py`, Phase 3.5)

Optional per run - only relevant when a `RealtimeStream` exists for the
same data source. Binding a run to a stream creates an
`AutoMLLiveBinding` row.

1. **Live scoring**: on a schedule (poll `realtime.read_history()` /
   `StreamRunner.snapshot()`, does not need a new push mechanism), build
   the row(s) the trained pipeline expects from the stream's recent
   buffer, call the existing `predict_with_model()`, and publish the
   result into the stream's metrics under `binding.metric_name` (default
   `"automl_risk_score"`). `StreamRunner` needs a small extension point to
   accept an externally-computed metric alongside its own derived
   OEE/scrap metrics - the smallest change needed in `realtime.py` for
   this spec.
2. **Drift check**: on `drift_check_interval_minutes` (default 1440 /
   daily), take a recent window from `read_history()` as a DataFrame and
   call the existing `automl_engine.check_data_drift()` against the run's
   stored training baseline (`explain_X`/`explain_y` already persisted by
   `automl_runner.py`). Updates `last_drift_check_at` / `last_drift_status`.
3. **Retrain on drift**: if `auto_retrain_on_drift` and `check_data_drift`'s
   `overall_severity` reaches `drift_severity_trigger`,
   materialize accumulated stream history into a `Dataset` - the same
   pattern `DowntimeLiveDataset` already uses (overwrite one Dataset with
   live data) - and call `execute_automl_run` again against it, producing
   a **new** `AutoMLRun` (versioned, not an in-place overwrite of the old
   one) with the same `template_id` and a new `AutoMLLiveBinding` pointing
   at it.

This module owns scheduling (a lightweight interval loop per binding,
same in-process-thread precedent as `StreamRunner` itself - no new job
queue). It calls existing engine/drift/materialization code; nothing here
duplicates logic that already exists elsewhere.

## Alerting

No new alerting subsystem. Two additions to the existing
`backend/app/services/alerts.py`, both following its established patterns:

1. **Live case**: automatic, requires nothing new in `alerts.py` itself -
   once `automl_realtime.py` publishes `automl_risk_score` (or whatever
   `metric_name` a binding uses) into a stream's metrics dict, the
   existing `eval_stream_metric` already knows how to alert on it. A user
   creates a normal `stream_metric` `AlertRule` with
   `metric_name="automl_risk_score"` and a threshold, same flow as any
   other stream alert, including the existing andon/relay webhook
   delivery via `_fire_webhook`.
2. **Batch case**: one new evaluator, `_eval_automl_risk(rule, dataset,
   con, rel)`, matching the exact shape of `_eval_scrap`/`_eval_quality` -
   reads a completed batch run's per-row predictions, flags rows/machines
   above the configured risk threshold, returns the same `AlertTrigger`
   shape. Registered in the existing dispatch alongside the other
   `_eval_*` functions.

## API

New/changed routes in `backend/app/api/routes/automl.py` (existing routes
unchanged, all still org-scoped via `current.org_id` like today):
- `GET /automl/templates` - list registered templates (id, label, roles).
- `POST /automl/templates/{template_id}/runs` - create a run against a
  template: `dataset_id` or `stream_id`, column-role mapping, optional
  friendly labels, optional task-framing choice. Sets `template_id` on the
  created `AutoMLRun`; everything downstream (background execution,
  status polling, predict/explain/drift-check) reuses today's existing
  `/runs/{run_id}/...` endpoints unchanged.
- `POST /automl/runs/{run_id}/live-bind` - create an `AutoMLLiveBinding`
  (stream_id, metric_name, thresholds).
- `DELETE /automl/live-bindings/{id}` - stop live scoring/drift for a run.
- `alerts.py`'s existing rule CRUD gains `type="automl_risk"` as a valid
  value; no new routes needed there.

## Frontend

- `AutoML.tsx` becomes an "Advanced / Custom" entry point (unchanged
  behavior, relabeled), reachable from the new guided-templates landing
  page rather than being the default.
- New generic wizard + results components under
  `frontend/src/pages/automl/templates/`: `TemplatePicker.tsx` (fetches
  `GET /automl/templates`), `MappingWizard.tsx` (renders the role-mapping
  form from whichever template's declared roles, reusing the existing
  form patterns from `Scrap.tsx`/`Predictive.tsx`'s column-mapping UI),
  `ResultsView.tsx` (plain-English headline + recommendation up top,
  domain-specific section per template, leaderboard/SHAP collapsed into
  an "Advanced" section).
- `frontend/src/pages/automl/api.ts` gains hooks for the new endpoints.
- `frontend/src/pages/Predictive.tsx` (if it exists as a distinct page)
  keeps its current UI/contract; results now originate from the
  re-platformed template underneath.

## Error handling

Existing contract preserved and extended, not replaced:
`automl_runner.py` already catches training exceptions and marks the run
`status="failed"` with `error=str(exc)` rather than crashing - every new
layer follows the same contract.

- **Pre-flight** (before any background job): missing required role
  mapping blocks submission client-side (reuses `ds_service.validate_columns`,
  same as today's Scrap/Predictive forms); fewer than 40 rows overall
  surfaces immediately rather than wasting a background run.
- **Data-quality cleaning**: a column that's non-numeric or mostly gaps is
  excluded with a note, not a crash.
- **Rare-event routing**: zero positive examples at all (not just "few")
  fails the run explicitly - there's no baseline of "normal" to compare
  against even for anomaly detection. Isolation Forest's own minimum
  row floor (~50 normal rows) is enforced the same way.
- **Explain/recommendation layer**: never fails the run; degrades through
  fallback rungs (2-driver -> 1-driver -> generic list -> generic
  recommendation) as described above.
- **Predictive-maintenance re-platform**: any behavior change against
  `predictive.py`'s current test cases is treated as a regression to fix,
  not an intentional change, until proven otherwise (see Testing).

## Testing

- **`timeseries_quality.py`**: synthetic series with known injected
  gaps/outliers/dropouts; assert gaps beyond threshold are flagged (not
  silently bridged), outliers are smoothed, short gaps interpolated, and
  `data_quality_notes` text matches what happened.
- **`automl_rare_event.py`**: boundary tests at the positive-count
  threshold (19 vs. 20 rows) confirming correct routing; both paths
  (anomaly vs. classification) assert on returning the same result shape;
  explicit test of the "zero positive examples" and "too few normal rows"
  hard-failure cases.
- **`automl_explain_nl.py`**: golden-output tests for known driver
  combinations; explicit test of every fallback rung (no SHAP, 1 driver, 0
  drivers, no rule match).
- **Per-template `preprocess`/`postprocess`**: extend `predictive.py`'s
  existing test fixtures for maintenance - RUL/cost/driver math must
  produce identical output pre- and post-re-platform on the same input;
  new fixtures for scrap and downtime feature/target derivation.
- **Regression gate**: existing `backend/tests/test_automl.py` (7 tests)
  and `predictive.py`'s current test suite must both still pass unchanged.
- **Alerting**: new test for `_eval_automl_risk` matching the existing
  `_eval_scrap`/`_eval_quality` test style; a live-stream test asserting
  `eval_stream_metric` correctly fires once `automl_realtime.py` publishes
  a risk score into a stream's metrics.
- **End-to-end per template**: one integration test per template hitting
  the real API route with a small synthetic dataset, asserting the
  `AutoMLRun` reaches `status="success"` with `template_id`,
  `explanation_sentence`, `recommendation`, `data_quality_notes` all
  populated.
- **Live binding**: a test that fakes a `StreamRunner` snapshot,
  confirms `automl_realtime.py` publishes the expected metric, and
  confirms a drift-detected scenario with `auto_retrain_on_drift=True`
  produces a new `AutoMLRun` versioned from the old one.

Every new module gets at least one test file; no new logic ships
untested, matching how `automl_engine.py` is already covered today.
