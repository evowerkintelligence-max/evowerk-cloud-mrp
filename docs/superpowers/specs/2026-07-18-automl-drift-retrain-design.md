# Automated Drift-Triggered Retraining — Design

## Context

AutoML (`automl_engine.py`, `automl_runner.py`, `automl_storage.py`,
`api/routes/automl.py`) already has manual drift-checking
(`check_data_drift`, PSI-based, `/runs/{id}/drift-check`) but it's a
stateless, button-triggered endpoint with no history and no connection to
retraining. This feature makes that automatic: when a live dataset an
opted-in run was trained on changes, check drift and retrain without a
human clicking anything.

This codebase has **no scheduler or cron infrastructure anywhere** — the
only non-HTTP-triggered long-lived process is a Flink/Redpanda pipeline for
realtime production ticks, unrelated to datasets. The only way anything
runs without blocking an HTTP response is FastAPI `BackgroundTasks`, fired
from a route handler. Given that, and that manufacturing shift-level data
doesn't need sub-minute freshness, this feature is **event-driven**, hooked
to the moment a dataset's file actually changes, not a time-based poller.

## Goal

Opt-in, per-run: when a `Dataset` an `AutoMLRun` was trained on gets
overwritten in place, automatically check drift against that run's training
snapshot, and if severity is high, retrain — with a quality gate (don't
silently ship a worse model) and a circuit breaker (don't retrain forever on
sustained, unresolvable drift).

## Non-goals

- Any new scheduler/cron/worker process. Purely event-driven off existing
  write paths.
- Datasets that are recreated fresh each time rather than overwritten in
  place. Pace Tracking's `stop_session` calls `datasets.create_dataset`
  (new `dataset_id` every session) — there's no stable dataset identity to
  hang a "same dataset, new data" comparison on, so pace-tracking-trained
  runs are structurally out of scope. Today this covers the Live Downtime
  Board and Shift Forecast Source datasets — both use
  `datasets.overwrite_dataset_file`.
- A full model registry / promotion UI / A-B champion-challenger workflow.
  The quality gate below is a single metric comparison, not a new state
  machine beyond the fields listed.
- Configurable severity threshold or cooldown window in v1 — both are fixed
  constants, matching how `MIN_FORECAST_ROWS` was left non-configurable in
  the prior shift-forecast feature.

## Architecture

**New service module** `backend/app/services/automl_retrain.py`:
`maybe_retrain_for_dataset(org_id: uuid.UUID, dataset_id: uuid.UUID) -> None`
— mirrors `automl_runner.execute_automl_run`'s pattern exactly: it's
scheduled via `BackgroundTasks` from a route, so it opens its own DB session
(`db_session.async_session_factory()`) rather than reusing the request's.

**Trigger wiring**: `datasets.overwrite_dataset_file` gains an optional
`background_tasks: BackgroundTasks | None = None` parameter. When provided,
it schedules `maybe_retrain_for_dataset` after doing its normal work. The
two real callers — `downtime_tracking.end_incident` (→ `_materialize`) and
`shifts.prepare_forecast_source` — get `background_tasks` threaded from
their routes down one service-function layer. This is the one deliberate
layering compromise in this design: `datasets.py` is otherwise
domain-agnostic, and this makes it aware that AutoML retraining exists. The
alternative (wiring the same decision logic separately into 2+ route files)
was weighed and rejected — it means every future live-dataset feature has
to remember to add it, whereas this way it's automatic.

**Small justified refactors**, both because the new service needs what they
provide, and neither currently lives somewhere reusable:
- `_load_explain_sample` (currently defined inline in
  `api/routes/automl.py`) moves to `automl_storage.py` — a route file
  shouldn't be an import target for a service module.
- `_normalize_higher_is_better` in `automl_engine.py` loses its leading
  underscore (→ `normalize_higher_is_better`) — it's already exactly the
  right tool for the quality gate below (converts any task's primary metric,
  whichever direction is "better" for it, into one comparable "higher wins"
  score) and becomes a legitimate second-caller public function instead of
  a private one reached into from outside its module.

## Data model

Two new columns on `AutoMLRun`:

- `auto_retrain: bool`, default `False`. Opt-in, settable only at
  run-creation time (no toggle endpoint in v1 — to change it, create a new
  run). Means "this run is the currently-watched one for its dataset."
- `retrained_from_run_id: uuid.UUID | None`, self-referential FK
  (`ondelete="SET NULL"`). Set on a child run created by auto-retrain.
- `auto_retrain_streak: int`, default `0`. Consecutive auto-retrain
  attempts for this lineage that did **not** pass the quality gate (reset to
  0 on a promoted retrain). This is the circuit breaker's counter — see
  below. Lives on whichever run is currently the "active" (`auto_retrain
  = True`) one in a lineage, since that's the row that keeps getting
  re-evaluated in place until it's superseded or trips the breaker.

## Retrain policy

On each `maybe_retrain_for_dataset(org_id, dataset_id)` call:

1. Query every `AutoMLRun` with this `org_id`/`dataset_id`,
   `status="success"`, `auto_retrain=True`.
2. For each: if `auto_retrain_streak >= MAX_AUTO_RETRAIN_STREAK` (constant,
   `3`), skip retraining. This is a defensive guard, not the normal trip
   path — normally a run's streak never reaches this check still
   `auto_retrain=True`, because step 6 below trips the breaker immediately,
   in the same evaluation that produces the 3rd consecutive rejection, not
   on a later 4th check.
3. Otherwise, run `check_data_drift` (current dataset content vs. this
   run's stored training sample). If `overall_severity != "high"`, do
   nothing. ("moderate" is deliberately not actionable — see the prior
   design's PSI-threshold reasoning; this avoids retraining on noise.)
4. If a cooldown window (constant, `1 hour`) has already produced an
   auto-retrain attempt for this `(org, dataset)` pair, skip — backstop
   against rapid-fire retraining on a fast-changing live dataset.
5. Otherwise, train a new `AutoMLRun` with the same config
   (task/target/features/thoroughness/algorithms/etc.), same `dataset_id`,
   `retrained_from_run_id` = the parent's id. This reuses
   `execute_automl_run`'s actual training logic directly (already-running
   inside a background task, so no need to hop through another
   `BackgroundTasks.add_task` — just `await` it).
6. **Quality gate**: once the new run finishes, compare
   `normalize_higher_is_better(task, new.metric_value)` against
   `normalize_higher_is_better(task, old.metric_value)`.
   - **Not worse** (`new >= old`): promote. Child gets `auto_retrain=True`,
     `auto_retrain_streak=0`. Parent gets `auto_retrain=False` (retired —
     the child is now the watched run for this dataset). Log
     `automl.auto_retrain_promoted`.
   - **Worse**: don't promote. Child keeps `auto_retrain=False` (stays in
     history for transparency/audit, never itself re-evaluated). Parent gets
     `auto_retrain_streak += 1`, evaluated immediately, in this same step —
     not deferred to the next check:
     - If the incremented streak is still `< MAX_AUTO_RETRAIN_STREAK`:
       parent keeps `auto_retrain=True` and remains the active model; drift
       will be re-checked against it again after the next qualifying data
       change (subject to the cooldown). Log `automl.auto_retrain_rejected`.
     - If the incremented streak reaches `MAX_AUTO_RETRAIN_STREAK`: the
       breaker trips right here — parent's `auto_retrain` is also set to
       `False` in this same update, in the same retrain attempt that
       produced the 3rd consecutive rejection. Log
       `automl.auto_retrain_circuit_broken` instead of (not in addition to)
       `automl.auto_retrain_rejected`.

Each run stores its own `explain_X` training snapshot at training time
(`automl_runner.execute_automl_run`, unchanged) — so a promoted child
compares future drift against *its own* baseline, not an ancestor's. Drift
detection is self-correcting across a lineage without any extra design work
here.

## Notification

No new alert-rule type (real scope, deliberately deferred). Reuses the
existing `log_event` audit-log mechanism with three new actions:
`automl.auto_retrain_promoted`, `automl.auto_retrain_rejected`,
`automl.auto_retrain_circuit_broken` — each with metadata (`from_run_id`,
`new_run_id` where applicable, drift severity, old/new metric values). The
circuit-broken case is the one that most needs a human to notice; a future
enhancement could route it through the existing alert-rule webhook firing,
but that's out of scope here (no `automl` alert-rule category exists today,
and inventing one is a bigger change than this feature needs).

## UI

- "Auto-retrain on drift" checkbox on the existing AutoML training form
  (sets `auto_retrain: true` in the create-run request).
- Run detail/list pages: a small badge for `auto_retrain`-enabled runs, and
  — when `retrained_from_run_id` is set — a note ("Auto-retrained from run
  X, drift: high") linking back to the parent.
- No new page. No new dedicated "retrain history" view in v1 — the existing
  run list, filtered/sorted by `created_at`, already shows a lineage once
  you follow `retrained_from_run_id` links.

## Error handling

- Drift check fails (e.g. explain sample missing) → caught, logged, no
  retrain attempted, `auto_retrain` left as-is (don't punish a run for a
  transient artifact-loading problem).
- Training itself fails (`execute_automl_run`'s existing `status="failed"`
  path) → treated as "worse" by the quality gate (a failed run has no
  `metric_value` to compare, so it can't be promoted) — same
  `auto_retrain_rejected` path, streak increments.
- Dataset deleted between the overwrite and the background task running →
  `maybe_retrain_for_dataset` no-ops (mirrors `execute_automl_run`'s
  existing "dataset not found" guard).

## Testing

- `backend/tests/test_automl_retrain.py` (new): drift below threshold does
  nothing; drift at "high" triggers a retrain; a promoted retrain flips
  `auto_retrain` from parent to child and resets the streak; a rejected
  retrain increments the streak and keeps the parent active; three
  consecutive rejections trip the circuit breaker and disable
  `auto_retrain`; the whole flow exercised end-to-end through the real
  `downtime_tracking` incident-close route (real HTTP, real DB), not by
  calling `maybe_retrain_for_dataset` directly with mocked data.
- Frontend: `tsc -b` + manual verification (no automated frontend test
  runner in this repo, matching prior precedent).
