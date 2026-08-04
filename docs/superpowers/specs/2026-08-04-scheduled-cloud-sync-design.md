# Scheduled cloud-DB sync (replaces in-app live streaming)

## Problem

The app currently runs a full live-streaming pipeline inside itself: `TickProducer` background threads poll an Integration's connector and publish every row to Redpanda; a Flink job (`backend/streaming/realtime_metrics_job.py`) computes windowed OEE metrics; `CacheConsumer` and `SensorConsumer` (both `aiokafka` background tasks) fan the ticks back out to an in-memory snapshot cache, an SSE route, CBM sensor/anomaly persistence, and AutoML live scoring/drift-retrain.

This is unwanted: it means Evowerk's backend hosts a live Kafka/Flink cluster and continuously ingests customer production data into itself, which cuts against the product's positioning that Evowerk never hosts customer data and every deployment stays customer-owned (see `no-data-hosting-usp` in project memory).

## Direction

Customers stream their real-time data (sensors/PLC/MQTT/etc) into their **own** Azure/AWS-hosted database, outside this app entirely. This app's backend runs a **scheduled pull sync** against that database on an admin-configured interval, computes the same OEE/CBM/AutoML outputs it does today, and stores results in its own (customer-owned) Postgres. No push pipeline, no cluster, no new infra service.

## Architecture

```
[Azure/AWS DB]  ← customer's own sensors/PLC/MQTT stream into it (outside our app)
      │  every N minutes
      ▼
[APScheduler job] → connector.fetch_since(cursor_col, last_cursor) → new rows only
      │
      ├─→ compute_window_metrics(rows, column_map)   → SyncTick (metrics history)
      ├─→ extract sensor_map fields → SensorReading + anomaly.score_reading (CBM, unchanged logic)
      └─→ if AutoML binding exists → score rows, check drift, retrain if triggered (unchanged logic)
      │
      ▼
   Postgres (already-running db service — no new infra)
      ▲
[Frontend polls GET /sync-schedules/{id}/snapshot every ~30s]  ← replaces SSE
```

## Components

### Removed entirely

- `redpanda`, `flink-jobmanager`, `flink-taskmanager` services and `REDPANDA_BROKERS` env var from `docker-compose.yml`
- `backend/streaming/` directory: `realtime_metrics_job.py`, `schemas.py` (Tick/MetricSnapshot Kafka payload dataclasses), `flink.Dockerfile`, `requirements.txt`, and their tests — except `metrics.py`, which is pure and gets relocated (see below)
- `app/services/realtime.py` (thread-based `TickProducer` + runner registry)
- `app/services/realtime_cache.py` (`aiokafka` `CacheConsumer`)
- `app/services/sensor_consumer.py` (`aiokafka` `SensorConsumer`) — logic ported, module removed
- `frontend/src/lib/sse.ts` and the `/realtime/streams/{id}/events` SSE route
- `kafka-python` / `aiokafka` from `backend/requirements.txt` (confirm nothing else in the codebase depends on them before removing)

### Renamed / repurposed

- `app/models/realtime.py`'s `RealtimeStream` → `app/models/sync_schedule.py`'s `SyncSchedule`, table `realtime_streams` → `sync_schedules`.
  - Dropped: `accumulate` (no longer meaningful — every sync is incremental now).
  - Added: `cursor_column: str | None`, `cursor_value: str | None` (last-seen cursor, as text; connectors cast per their column type).
  - `interval_seconds` stays as the column name but its practical minimum moves from 1s to 60s (scheduler cadence, not a poll-loop cadence).
  - `desired_status` (`running`/`stopped`) keeps its meaning: whether the schedule's job should be active.
- `app/api/routes/realtime.py` → `app/api/routes/sync_schedules.py`, prefix `/sync-schedules` (was `/realtime`). Resource renamed "stream" → "schedule" throughout the route module and its Pydantic schemas.
- `app/models/automl_live_binding.py`'s `AutoMLLiveBinding.stream_id` → `schedule_id`, FK now points at `sync_schedules.id`.

### New

- `app/services/connectors/postgres_connector.py`, `mysql_connector.py` — same shape as `sqlserver_connector.py` (`test`/`preview`/`fetch_all`/`iter_rows`), added to `VALID_TYPES` and `get_connector()` in `app/services/connectors/__init__.py`.
- `fetch_since(cursor_column: str, cursor_value: str | None, max_rows: int) -> tuple[list[dict], str | None]` added to the connector interface for `sqlserver`, `oracle`, `postgres`, `mysql` (the DB-query connectors). Wraps the admin's configured query: `SELECT * FROM (<query>) AS _src WHERE <cursor_column> > :cursor_value ORDER BY <cursor_column>`, `cursor_value=None` on first run pulls everything up to `max_rows`. Returns the new max cursor value alongside the rows so the caller can persist it. Connector types without `fetch_since` (csv, excel, sap_odata/erp_rest/mes_rest, opcua, mqtt, opcda, pi_webapi) are excluded from the "create scheduled sync" UI/API — they remain usable via the existing manual on-demand `POST /integrations/{id}/sync`, untouched.
- `app/models/sync_tick.py`:
  - `SyncTick` (id, schedule_id FK, org_id, ts, row_count, metrics JSON) — one row per successful scheduled run. Replaces the JSONL history file; queried for `/sync-schedules/{id}/history` and for alert evaluation.
  - `SyncRow` (id, schedule_id FK, ts, row JSON) — raw synced rows, capped at the most recent ~10,000 per schedule (oldest deleted past the cap on each run, mirroring the old in-memory `_RAW_ROW_BUFFER` size). Feeds AutoML scoring's need for raw feature columns, same reason `realtime_cache.recent_rows()` existed.
- `app/services/metrics_calc.py` — `compute_window_metrics()`, moved verbatim from `backend/streaming/metrics.py`. Pure function, no behavior change.
- `app/services/sync_scheduler.py` — new module, `AsyncIOScheduler` (APScheduler) instance owned by the app process.
  - `start_scheduler()`: called from `app.main`'s startup lifespan hook. Loads every `SyncSchedule` with `desired_status="running"`, adds one interval job per schedule (replaces `resume_all_streams`).
  - `stop_scheduler()`: called from shutdown lifespan hook.
  - `add_job(schedule)` / `remove_job(schedule_id)` / `reschedule_job(schedule)`: called from the create/start/stop/update/delete routes to mutate the live job set without a process restart (replaces `start_runner`/`stop_runner`/`get_runner`).
  - `_run_sync(schedule_id)`: the job body, run on the asyncio loop with blocking connector I/O in `asyncio.to_thread` (same pattern `app/services/integrations.py::_run_connector_method` already uses):
    1. Load `SyncSchedule` + `Integration`, decrypt config.
    2. `rows, new_cursor = connector.fetch_since(schedule.cursor_column, schedule.cursor_value, max_rows)`.
    3. If no rows: update `last_error=None` and return (no-op tick, not an error).
    4. `metrics = compute_window_metrics(rows, schedule.column_map)`; insert `SyncTick(row_count=len(rows), metrics=metrics)`.
    5. Insert `SyncRow` per row (capped); trim rows beyond the cap for this schedule.
    6. If `schedule.sensor_map`: for each row, extract mapped channels, persist `SensorReading`, call `anomaly.score_reading()` — logic ported unchanged from `sensor_consumer.py`'s per-tick handling, just called inline instead of via a Kafka consumer loop.
    7. If an `AutoMLLiveBinding` exists for this schedule: score the new rows against the bound model, run the drift check (respecting `drift_check_interval_minutes` — track last-checked time on the binding same as today), retrain if `auto_retrain_on_drift` and drift crosses `drift_severity_trigger` — logic ported unchanged from `automl_realtime.py`, called inline instead of on its own background thread.
    8. `schedule.cursor_value = new_cursor`; commit.
    9. On any exception in steps 2–8: `schedule.last_error = str(exc)`; commit; do NOT advance the cursor, so the next tick retries the same window. Log, don't raise (APScheduler already isolates job exceptions, but the schedule row is the durable error surface the UI reads).

### Ported, not rewritten

`automl_realtime.py`'s scoring/drift/retrain functions and `sensor_consumer.py`'s reading-extraction/anomaly-scoring functions keep their existing bodies. They move from being triggered by a Kafka message loop to being called as plain functions from `sync_scheduler._run_sync`. Their internals (drift math, anomaly scoring, retrain triggering) are unchanged.

## Data flow / error handling

- A schedule's `fetch_since` failing (source unreachable, bad query, cursor column missing) sets `last_error` and leaves `cursor_value` untouched — next run retries the same window rather than silently skipping data.
- Creating a schedule requires picking a `cursor_column` from the connector's `preview()` output (same UI moment the old "column_map" picker used) for DB-query connector types; other connector types don't offer scheduled sync.
- `app/services/alerts.py::eval_stream_metric` reads the latest `SyncTick.metrics` for `rule.config["schedule_id"]` instead of `realtime_cache.get_snapshot()`. `AlertRule.config`'s JSON key `stream_id` → `schedule_id`: one-time data migration (`UPDATE alert_rules SET config = jsonb_set(config - 'stream_id', '{schedule_id}', config->'stream_id') WHERE type = 'stream_metric'` or equivalent in an Alembic migration).
- Frontend `Realtime.tsx` drops `subscribeToSSE()` entirely; polls `GET /sync-schedules/{id}/snapshot` on a fixed interval (e.g. `setInterval`/`refetchInterval`, matching the schedule's own interval or a fixed ~30s, whichever is simpler to implement) while the page is open. History chart reads `GET /sync-schedules/{id}/history` the same way it read the old history endpoint.

## Testing

- `backend/tests/test_realtime.py` → `test_sync_schedules.py`: drop every test gated on `REDPANDA_BROKERS` — a fake/in-memory connector stands in for the cloud DB now, no live infra required for any test in this suite. Keep and adapt: CRUD, cross-org isolation, alert-attachment, delete-stops-job. Add: cursor advances only on success, `fetch_since` called with the persisted cursor, scheduler actually invokes the job on its interval (use APScheduler's ability to run a job immediately / a short test interval), failed run leaves cursor untouched and sets `last_error`.
- `backend/streaming/test_metrics.py` → `backend/tests/test_metrics_calc.py`, assertions unchanged, just relocated import path.
- New `test_postgres_connector.py` / `test_mysql_connector.py`: `fetch_since` cursor-filtering logic tested against a local/dockerized instance where available, skipped otherwise (same `skipif` pattern the old Redpanda tests used) — or a lighter unit test that only checks the generated SQL/query wrapping if standing up a real DB in CI isn't in scope for this pass.
- `test_realtime_cache.py`, `test_realtime_cache_external.py`, `test_realtime_producer.py`: deleted (module gone).
- `test_sensor_consumer.py`: rewritten to call the ported extraction/scoring function directly with a fake row batch instead of feeding a fake Kafka tick.
- `test_automl_realtime.py`: rewritten the same way — call the ported scoring/drift function directly from a fake sync batch.

## Out of scope for this pass

- Building the customer-side pipeline that streams sensor/PLC data into their Azure/AWS DB — that's the customer's/a partner's responsibility, not this app.
- A generic "any SQL engine" connector abstraction — Postgres and MySQL connectors are added concretely; further engines follow the same pattern later if needed.
- Changing how AutoML training (non-live) or the batch `POST /integrations/{id}/sync` on-demand flow work — untouched.
