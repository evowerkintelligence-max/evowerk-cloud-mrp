# Realtime: replace stream-to-app with database sync

Date: 2026-07-30
Branch: claude/realtime-stream-database-sync-2a5b83
Status: approved in brainstorming, amendments folded in

## Problem

The current realtime pipeline streams to the app through a broker chain:

```
connectors -> TickProducer (thread per stream) -> Redpanda realtime.ticks
               |-> Flink job (5s tumbling window) -> Redpanda realtime.metrics
               |     '-> CacheConsumer (per-replica in-memory dict + local JSONL history)
               |          '-> SSE /realtime/streams/{id}/events -> Realtime.tsx
               '-> SensorConsumer (shared consumer group) -> Postgres SensorReading
```

Three problems:

1. `infra/` provisions no Redpanda and no Flink. Both consumers catch the connect
   failure and log "realtime metrics disabled", so in the real deployment the whole
   pipeline is dead code. Only docker-compose has brokers.
2. Most connectors' `fetch_all()` returns the whole table every poll and the producer
   resends `rows[-1000:]` each cycle. Flink windows aggregate the same rows
   repeatedly, and SensorConsumer writes duplicate SensorReading rows every poll.
3. Snapshot state is per replica (in-memory dict) and history is per container
   (local JSONL), so both are invisible across replicas and lost on redeploy.

Decision: remove all realtime streaming to the app. Collectors write windows straight
to Postgres; the frontend syncs at fixed intervals, fetching only rows added since its
last sync.

## Target architecture

```
connectors -> StreamCollector (asyncio task per stream)
                |  fetch_all() on a dedicated 1-thread executor (blocking sync I/O)
                |  multiset dedup vs previous poll  ->  new rows only
                |  compute_window_metrics(new_rows, column_map)
                |-> INSERT realtime_samples (1 row per poll window)
                '-> sensor ingest for streams with sensor_map (same transaction)

frontend -> GET /realtime/streams/{id}/samples?since=<cursor>  every interval_seconds
```

Deleted outright: Redpanda, Flink, `realtime_cache.py`, `SensorConsumer`'s Kafka
plumbing, the SSE route, `sse.ts`, both topics, and the `Tick`/`MetricSnapshot` wire
schemas.

## Data model (one migration, two tables)

### `realtime_samples`

Replaces the in-memory snapshot dict, the realtime.metrics topic, and the JSONL
history files.

| column | type | notes |
|---|---|---|
| `id` | BigInteger PK autoincrement | the sync cursor; monotonic, immune to clock skew. Use `BigInteger().with_variant(Integer, "sqlite")` so autoincrement works on the SQLite test DB |
| `org_id` | Uuid FK orgs CASCADE | |
| `stream_id` | Uuid FK realtime_streams CASCADE | index `(stream_id, id)` |
| `ts` | DateTime(tz) | window collection time |
| `status` | String(16) | running / error |
| `error` | String(2000) nullable | |
| `rows_in_window` | Integer | new rows this window (post-dedup) |
| `rows_collected` | Integer | cumulative new rows, seeded from latest sample on collector start |
| `duration_s` | Integer | cumulative session seconds, seeded the same way |
| `last_tick_s` | Float nullable | seconds since last window with rows, at write time |
| `metrics` | JSON | `compute_window_metrics` output |
| `raw_rows` | JSON | this window's new rows, capped at 1000 (backs AutoML `recent_rows`) |

One table, raw rows inline. AutoML is the only raw-row reader and only ever wants
"the last N rows in order", so flattening `raw_rows` across recent samples covers its
10k-row retrain ask without a second table or second retention policy.

### `realtime_stream_metrics`

Replaces `set_external_metric` (the AutoML live-score slot).

| column | type |
|---|---|
| `stream_id` | Uuid FK realtime_streams CASCADE, PK part |
| `name` | String(255), PK part |
| `value` | JSON |
| `updated_at` | DateTime(tz) |

Separate table, not a JSON column on `realtime_streams`: a lost update here is a
missed alert, and upsert on a composite PK is race-free where read-modify-write on
JSON is not.

`realtime_streams` itself is unchanged (`accumulate` stays as a vestigial column; no
data migration).

## Sync API contract

`GET /realtime/streams/{stream_id}/samples?since=<id>&limit=<n>`

- `since` omitted: return only the latest sample (cheap page-open, no backfill).
- `since` given: return rows `id > since` ordered by `id`, `limit` default 500,
  max 2000.
- Response: `{ stream_id, cursor, samples: [...] }` where `cursor` is the highest
  `id` returned (so `limit` truncation self-heals next poll), and each sample carries
  `ts, status, error, rows_in_window, rows_collected, duration_s, duration
  (HH:MM:SS, formatted at read time), last_tick_s, metrics`.
- External metrics from `realtime_stream_metrics` are merged into the newest
  sample's `metrics` in each response, mirroring today's `get_snapshot` merge.
- The server keeps no cursor state. Two tabs polling the same stream are both
  correct. Within a stream there is exactly one writer inserting sequentially, so
  keyset pagination on `id` has no commit-order race.

## Collector design

`StreamCollector` (in `app/services/realtime.py`, replacing `TickProducer`):

- One asyncio task per stream, registered in the same `_runners`-style dict.
  `start_runner` / `stop_runner` / `stop_all_runners` / `resume_all_streams` keep
  their roles; start/stop become async.
- `fetch_all()` runs on a per-collector `ThreadPoolExecutor(max_workers=1)`, shut
  down on stop. Not `asyncio.to_thread`: MQTT collectors block continuously (no
  interval wait, `collect_seconds` per cycle) and would starve the shared default
  executor that `app.services.integrations` also uses.
- Poll cadence unchanged: MQTT loops back-to-back; everything else waits
  `interval_seconds` between polls.
- **Ingest dedup:** the collector keeps the previous poll's rows as a
  `Counter` of row-hashes and ingests only the multiset difference. New rows only
  flow into metrics, `raw_rows`, and sensor ingest. Known edge (ponytail comment in
  code): two genuinely identical rows in adjacent polls with no distinguishing
  column are treated as re-fetched. Multiset semantics keep duplicates within one
  poll.
- Every poll writes a sample row, including empty ones (`rows_in_window=0`), so
  status, duration and the frontend cursor keep advancing. On connector error the
  sample carries `status="error"` and the collector updates
  `realtime_streams.last_error`.
- Sensor path: for streams with `sensor_map` + `machine`, the deduped new rows go
  through `ingest_rows(db, stream, rows)` in the same transaction. This is
  `SensorConsumer._handle_tick`'s scoring/baseline/anomaly logic reused verbatim,
  minus Kafka; it fixes the duplicate-readings flood as a side effect.
- Counters `rows_collected` and `duration_s` are seeded from the stream's latest
  sample on collector start, so they survive restarts (today they reset with the
  Flink job).

## Retention

- `realtime_retention_days: int = 7` in settings.
- Hourly, whichever collector hits its hourly mark first runs one global
  `DELETE FROM realtime_samples WHERE ts < cutoff` (guarded by an in-memory
  last-pruned timestamp per process). Global, not per stream, so stopped streams'
  rows are pruned too. No scheduler dependency; none exists in the app.

## Blast radius

| area | change |
|---|---|
| `app/services/realtime.py` | `TickProducer` -> `StreamCollector` (async, DB writes, dedup, prune). `history_path` / `read_history` / `delete_history` deleted |
| `app/services/sensor_consumer.py` | Kafka consumer class deleted; becomes `app/services/sensor_ingest.py` exposing `ingest_rows` with the existing scoring logic |
| `app/services/realtime_cache.py` | deleted |
| `app/services/stream_metrics.py` | new home of `streaming/metrics.py` (`compute_window_metrics`), moved unchanged |
| `app/api/routes/realtime.py` | SSE route + `_sse_generator` deleted; `/samples` added; `_snapshot_dict` and `/history` read `realtime_samples`; shared `latest_snapshot(db, stream)` helper does the sample + external-metric merge |
| `app/services/alerts.py` | `eval_stream_metric` becomes `async (db, org_id, rule)` reading via `latest_snapshot`; `stream_alerts_bulk` keeps N+1 avoidance with one latest-sample-per-stream query (`id IN (SELECT max(id) ... GROUP BY stream_id)`, portable to the SQLite test DB) |
| `app/services/automl_realtime.py` | `recent_rows` -> async flatten of `raw_rows` from latest samples until n rows; `set_external_metric` -> upsert into `realtime_stream_metrics`; both live in `realtime.py` |
| `app/models/realtime.py` | add `RealtimeSample`, `RealtimeStreamMetric` |
| `app/main.py`, `dev_server.py` | cache/sensor consumer wiring removed |
| `app/core/config.py` | `redpanda_brokers` removed; `realtime_retention_days` added |
| `streaming/` | deleted (`metrics.py` and `test_metrics.py` move first) |
| `docker-compose.yml` | `redpanda`, `flink-jobmanager`, `flink-taskmanager` services removed; backend's `REDPANDA_BROKERS` env + depends_on removed |
| `requirements.txt` | `kafka-python`, `aiokafka` removed; `streaming/requirements.txt` + `flink.Dockerfile` deleted |
| `frontend/src/lib/sse.ts` | deleted (Realtime.tsx is its only importer) |
| `frontend/src/pages/Realtime.tsx` | `LiveMetrics` swaps SSE for the cursor poll: `since` in a ref, poll every `interval_seconds`, render the newest returned sample. List view keeps its existing `refetchInterval` polling |
| alembic | one migration adding both tables |

## Behavior changes (accepted)

- Status `not_running_in_this_process` disappears; a DB read has no per-replica
  blind spot. Frontend status union shrinks.
- A stopped stream reports its real final counters instead of a zeroed
  definition-only snapshot (durable rows have no staleness problem).
- Metrics are per-window over new rows only. Today's windows re-aggregate re-fetched
  rows, so absolute-sum metrics (throughput, total_downtime_min) change scale;
  stream_metric alert thresholds may need re-tuning. Ratio metrics are unaffected.
- Old JSONL history is abandoned, not migrated: it was per container, invisible
  across replicas, and explicitly a nice-to-have. History starts fresh in Postgres.

## Out of scope

- Multi-replica collector ownership. `resume_all_streams()` starts a collector on
  every replica today (double-publishing ticks); this change neither fixes nor
  worsens that. Cheap later fix: `pg_try_advisory_lock` per stream.
- Dropping the vestigial `accumulate` column.
- Auto-retuning existing stream_metric alert thresholds.

## Testing

- New `tests/test_realtime_sync.py`, the contract that is the point of the change:
  - poll, poll again with the returned cursor, assert only new windows, zero
    duplicates, strictly advancing cursor
  - ingest dedup: two polls returning overlapping rows produce non-overlapping
    `raw_rows` and correct `rows_in_window`
  - retention: cutoff deletes old rows, leaves recent ones
- `test_metrics.py` moves with `metrics.py`.
- `tests/test_realtime.py`: SSE tests replaced with `/samples` tests; snapshot and
  bulk-alert tests updated to DB-backed reads.
- `tests/test_automl_realtime.py`: cache seeding replaced with `realtime_samples`
  seeding.
- Deleted with their subjects: `test_realtime_cache.py`,
  `test_realtime_cache_external.py`, `test_realtime_producer.py`,
  `streaming/test_pipeline_e2e.py`, `test_infra_smoke.py`, `test_schemas.py`.
