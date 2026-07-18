# Condition-Based Monitoring (CBM) Predictive Maintenance — Design

## Context

The current `Predictive Maintenance` feature (`backend/app/services/predictive.py`,
`backend/app/api/routes/predictive.py`, `frontend/src/pages/Predictive.tsx`) computes
failure risk from **daily-aggregated OEE data uploaded as a CSV** — a Gradient Boosting
classifier (with a hand-tuned statistical fallback) trained fresh on every request, plus
a linear/quadratic RUL heuristic. It is not real predictive maintenance in the industry
sense: no live condition data, no calibrated failure probabilities, no persisted model,
and failure ground truth depends on exact string matching against a fixed downtime
category set.

This spec replaces that feature with genuine **condition-based monitoring (CBM)**:
continuous per-machine sensor telemetry, statistical anomaly detection against a learned
baseline, and Weibull survival-analysis RUL calibrated against real breakdown history —
the architecture used by real industrial CBM systems (ISO 13374/13381 lineage).

No sensor hardware vendor is chosen yet (tracked separately as a vendor partnership).
This design is vendor-agnostic: it consumes timestamped `{sensor, value}` readings from
any source already wired into the app (OPC-UA, MQTT, or future connectors), and is ready
to receive real data the moment a vendor is chosen.

**Decision: full replacement.** The CSV/OEE-based analysis is deleted, not kept as a
fallback. A machine with no live sensor stream connected shows an explicit "no stream
connected" state rather than any prediction — matches the industry meaning of CBM, which
always requires live condition data. This was confirmed explicitly with the user,
including the consequence that machines without a stream lose predictive coverage they
have today.

## What already exists (reused, not rebuilt)

Investigated before designing new infrastructure:

- **Realtime ingestion pipeline** (`backend/app/services/realtime.py`,
  `backend/streaming/*`): `TickProducer` polls an `Integration` connector and publishes
  each row as a `Tick` to Redpanda topic `realtime.ticks`. `Tick.row` is an **unconstrained
  JSON dict** — already carries arbitrary fields, not limited to OEE columns.
- **OPC-UA connector** (`opcua_connector.py`): returns `{node_id, value, timestamp}` per
  polled node — generic key/value, vibration/temperature/current-draw tags work today via
  `node_ids` configuration, no new connector code needed.
- **MQTT connector** (`mqtt_connector.py`): parses JSON payloads into
  `{topic, timestamp, **payload}` — fully arbitrary fields, same conclusion.
- **Gap**: nothing durably stores high-frequency readings today. Only a capped 20k-point
  JSONL history per stream exists; Redpanda topics are ephemeral. No sensor/telemetry
  model exists (`app/models/telemetry.py` is unrelated — it's HTTP usage analytics).
- **Failure ground truth**: `DowntimeIncident` (`backend/app/models/downtime_tracking.py`)
  has `machine`, `started_at`, nullable `ended_at`, and a `reason` enum including
  `breakdown` vs. planned stop types. Filtering `reason == "breakdown"` with
  `started_at` as failure time and open (`ended_at IS NULL`) records as right-censored is
  a viable, already-populated ground-truth source for Weibull fitting.
  (`RcaCase` was also checked and rejected — it has no structured machine or
  failure-timestamp field, just a free-form `trigger_context` JSON blob.)
- **Alerting**: `AlertRule` + `_fire_webhook` (`backend/app/services/alerts.py`) already
  dispatches by rule type and fires webhooks — new CBM rule types plug into this
  unchanged.

## Architecture

```
Sensor/PLC
  → OPC-UA or MQTT connector (existing, no changes)
  → TickProducer (existing, no changes)
  → Redpanda topic `realtime.ticks` (existing)
  → NEW: SensorConsumer (Python service, same pattern as existing CacheConsumer)
      - reads ticks for streams with a `sensor_map` configured
      - extracts named channels from Tick.row per the map
      - writes SensorReading rows to Postgres
      - updates SensorBaseline incrementally (Welford's online algorithm)
      - runs SPCScorer inline, persists SensorAnomaly on flag
  → Postgres (existing DB, new tables — see Data Model)
  → Nightly batch job: refit WeibullFit per machine from SensorReading + DowntimeIncident
  → AlertRule evaluation (existing scan loop): new rule types query SensorAnomaly / WeibullFit
  → Frontend: fleet dashboard + per-machine detail (Predictive.tsx rewrite)
```

**Why a Python consumer over a Flink job:** the existing PyFlink job already does OEE
tumbling-window aggregation; CBM's job is fundamentally "read a tick, extract fields,
write a row, run O(1) incremental stats" — a stateful Flink job would duplicate state
that otherwise lives cleanly in Postgres, and is harder to test/debug than a plain Python
consumer. Reuses `CacheConsumer`'s existing pattern rather than introducing new
stream-processing machinery.

**Why Postgres over a dedicated time-series DB:** no real sensor is connected yet — no
volume has been measured that would justify TimescaleDB/InfluxDB operational cost.
`ponytail: add a dedicated time-series store when raw-retention volume on the existing
Postgres table is shown to be a real problem, not before.`

## Data Model (new)

```
RealtimeStream (existing, extended)
  + sensor_map: dict[str, str] | None   # {"vib_x": "<Tick.row field name>", ...}
  + machine: str | None                 # which machine this stream represents

SensorReading
  id, org_id, machine, sensor, value: float, ts: datetime
  index: (org_id, machine, sensor, ts)
  retention: 90 days raw, pruned by periodic job (config default, no downsampling yet)

SensorBaseline
  org_id, machine, sensor, mean, std, n, learning_until: datetime
  updated incrementally on every SensorReading write (Welford's algorithm, O(1))

SensorAnomaly
  id, org_id, machine, sensor, reading_id, z_score, detected_at, acknowledged_at: datetime | None

WeibullFit
  org_id, machine, shape_k, scale_lambda, n_failures, n_censored, fit_tier, fit_at: datetime
  fit_tier: "per_machine" | "fleet_pooled" | "insufficient_data"
```

## Anomaly Detection

- **Baseline learning**: first 14 days or 500 readings (whichever first) per
  (machine, sensor) — no flags fire during this window, mirrors the existing auto-baseline
  UX convention already used by Throughput's target estimation.
- **Detection rule (v1)**: 3-sigma control limit —
  `is_anomaly = abs(value - mean) > 3 * std`, `z_score` stored as severity.
  `ponytail: plain 3-sigma, not the full Western Electric rule set (run-length,
  trending patterns) — add specific WECO rules only if false-positive rate on real data
  shows plain 3-sigma insufficient.`
- **Baseline reset**: manual "relearn baseline" action per (machine, sensor), for when a
  real repair legitimately shifts the normal operating range. Automatic detection of
  "this shift is legitimate vs. degradation" is out of scope for v1.
- **ML seam**: `AnomalyScorer` protocol — `score(reading, baseline) -> AnomalyResult`.
  One implementation (`SPCScorer`) ships now. This abstraction is justified because
  swappable-scorer-later was an explicit requirement, not spec-ulative design — no second
  implementation is built in this pass.
- **Out of scope for v1**: multi-signal/cross-sensor anomaly correlation — deferred to the
  future ML scorer.

## RUL — Weibull Survival Analysis

- **Ground truth**: `DowntimeIncident` filtered to `reason == "breakdown"`, per machine.
  Duration = time between consecutive breakdowns; the current open interval (since last
  breakdown, or since first reading if none) is right-censored.
- **Fitting**: two-parameter Weibull (`shape k`, `scale λ`) via `lifelines.WeibullFitter`
  — the standard tool for censored-failure-time MLE; hand-rolling censored likelihood
  optimization is a correctness trap, not something to reimplement.
- **Sparse-data fallback tiers** (mirrors the existing ML→statistical fallback pattern
  already in this codebase, for consistency):
  1. Per-machine Weibull if ≥5 breakdown events for that machine
  2. Else fleet-wide pooled Weibull (all machines' breakdowns combined)
  3. Else (fleet-wide also <5 total breakdowns): **no RUL number shown** —
     "insufficient failure history," not a fabricated estimate.
- **Refit cadence**: nightly batch job, persisted to `WeibullFit` — not recomputed per
  request (fixes the "retrains from scratch every call" gap in the old implementation).
- **Output**: conditional probability of failure in the next 30 days
  (`1 - S(t+30)/S(t)`, properly conditioned on survival to current age `t`), plus median
  remaining life.
- **Not fused with anomaly detection.** Weibull gives the calibrated baseline hazard from
  failure history; SPC anomalies are a live per-sensor deviation signal. Shown together in
  the UI, not mathematically blended — a blended score would need hand-tuned weights with
  no data to justify them, which is exactly the problem being fixed.
- **Risk bucketing**: reuses existing `high/medium/low` labels, thresholds on 30-day
  failure probability (≥0.65 high, ≥0.35 medium) — consistent with existing conventions.

## Alerting Integration

Two new `AlertRule` types, both dispatched through the existing rule-type evaluation loop
and existing `_fire_webhook` — no new delivery path:

- `cbm_anomaly` — fires on a new `SensorAnomaly`, or when count in a trailing window
  exceeds a configurable threshold
- `cbm_rul` — fires when 30-day failure probability crosses a configurable threshold, or
  risk level transitions to `high`

## Fleet Dashboard

New rollup view (same shape as the existing `PlantSummary`): one row per machine — CBM
status (no stream / learning baseline / active), current risk level & 30-day failure
probability, active anomaly count (trailing 24h), last-reading staleness. Pure reporting
query over `SensorReading` / `SensorAnomaly` / `WeibullFit` — no new detection logic.

## Frontend — `Predictive.tsx` full rewrite

1. **Fleet dashboard** (the table above) becomes the landing view.
2. **Machine detail** (click a row): live sensor trend chart(s) with control-limit bands
   and anomaly markers, RUL/survival summary, recent-anomaly list with an acknowledge
   action.
3. **Schedule/recommendations**: existing `MaintenanceSchedule` CRUD table is unchanged —
   it's a generic action queue indifferent to how risk was computed. Recommendations are
   now generated from CBM risk instead of OEE risk.
4. Machines with no `sensor_map`/stream configured show an explicit **"no live sensor
   stream connected — set one up in Integrations"** empty state, not a blank or broken
   page.

## Backend Cleanup

Deleted outright, not kept behind a flag (delete over add):

- `predictive.py`: `analyze()`, `_ml_analyze()`, `_stat_analyze()`, `_rul_days()`,
  OEE-shaped dataclasses
- `predictive.py` API route: `POST /datasets/{dataset_id}/predictive/analyze` and its
  Pydantic request/response models
- `MachineRisk`/`MaintenanceRec` are reshaped for CBM fields (`failure_probability`,
  `active_anomalies`, `last_reading_at` replace `current_oee`, `trend_slope`)
- Maintenance schedule CRUD (`list_schedules`/`create_schedule`/etc.) is retained as-is

## Testing

One runnable check per non-trivial piece of logic, no test-framework scaffolding beyond
what's already in use:

- SPC scorer: synthetic in-band/out-of-band reading series → correct anomaly flags
- Weibull fit: synthetic failure+censored dataset → correct fit and correct fallback-tier
  selection at each data-sparsity level
- Ingestion consumer: a `Tick` with `sensor_map` fields → correct `SensorReading` row
- Alerting: new rule types fire the webhook (same pattern as existing alert rule tests)
- API/tenant-isolation test analogous to the current `test_predictive.py`, updated for
  CBM endpoints

## Explicitly Out of Scope (this pass)

- Choosing/integrating a real sensor hardware vendor (separate, ongoing effort)
- ML anomaly scorer implementation (Isolation Forest/autoencoder) — seam only
- Multi-signal/cross-sensor anomaly correlation
- Downsampling/rollup storage for `SensorReading` — raw retention only until proven
  insufficient
- Automatic (vs. manual) baseline-shift detection after legitimate repairs
- Fusing anomaly score and Weibull hazard into one blended risk number
