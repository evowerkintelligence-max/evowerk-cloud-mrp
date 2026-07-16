# Realtime Streaming: Redpanda + Flink Slice

Status: Approved for planning
Date: 2026-07-16

## Problem

The realtime streaming layer (`backend/app/api/routes/realtime.py`,
`backend/app/services/realtime.py`, `backend/app/models/realtime.py`) polls
an `Integration`'s connector on an interval and computes throughput/scrap/OEE
metrics inside a single in-process `StreamRunner` background thread. Two
limitations are already called out in the code itself:

- `app/services/realtime.py`'s module docstring: "The live snapshot/status
  this module tracks is still ephemeral and only reflects this process" - a
  stream started on one backend replica is invisible
  (`"not_running_in_this_process"`) to any request served by a different
  replica, including its own SSE feed and alert evaluation (`_snapshot_dict`
  in `realtime.py:47`, `eval_stream_metric` in `alerts.py:296`, both call
  `get_runner()` directly).
- The poll interval is fixed per stream at creation (`StreamCreate.
  interval_seconds`, 1-300s) and processed strictly in a blocking poll loop -
  there's no sub-interval freshness, and `alerts.py:346`'s own comment admits
  there's "no retry/backoff/queue" around it.

This spec replaces only the collection + metric-computation internals (the
`StreamRunner` thread) with a Redpanda topic and a Flink windowed-aggregation
job, while keeping every model, route signature, and the SSE JSON contract
unchanged. It's the infra half of closing the real-time gap called out
against TeepTrak-style live monitoring; see
`docs/superpowers/specs/2026-07-16-downtime-tracking-design.md` and
`2026-07-15-pace-tracking-design.md` for the product-side half of that gap.

## Scope

In scope: replacing `StreamRunner`'s poll+compute+buffer with a
Redpanda-backed producer/Flink/cache pipeline that serves the exact same
`_snapshot_dict` shape the SSE route and `eval_stream_metric` already depend
on, so nothing above `app.services.realtime`'s public functions
(`start_runner`, `get_runner`, `stop_runner`, `stop_all_runners`,
`resume_all_streams`) has to change.

Out of scope for v1 (explicit decisions, not oversights):

- **Exactly-once delivery.** Flink checkpointing + a transactional Redpanda
  producer would guarantee it; v1 runs at-least-once with idempotent
  windowed writes (a duplicated tick just gets summed into a window twice at
  worst - same failure mode as today's in-memory buffer racing, not a
  regression).
- **Kafka Connect for the source connectors.** `sqlserver_connector.py` /
  `oracle_connector.py` / `opcda_connector.py` stay hand-rolled, now
  publishing into Redpanda instead of an in-memory deque. Revisit only if
  the connector list grows enough to justify owning Connect's operational
  surface.
- **Schema registry / Avro.** Tick and metric messages are plain JSON. One
  producer type, one consumer type - schema drift isn't a real risk yet.
- **Moving alert rule evaluation into Flink (CEP).** `eval_stream_metric`'s
  threshold logic (`alerts.py:296-330`) stays exactly as-is in Python,
  reading `AlertRule` config from Postgres per call - it just reads the
  windowed snapshot from the new cache instead of `get_runner().snapshot()`.
  Nelson-rule-style sequence/pattern alerts are the natural first candidate
  for real Flink CEP, but that's a separate spec.
- **Per-stream configurable window size.** Flink runs one fixed 5s tumbling
  window for every stream, not each stream's own `interval_seconds`
  (1-300s today). Every stream gets *at least* as fresh data as before (5s
  <= any existing `interval_seconds`), so this is a strict improvement, not
  a regression - full per-key dynamic windowing (Flink `ProcessFunction` +
  per-key timers) is real complexity with no user-visible win yet.

## Architecture

Two Redpanda topics, partitioned by `stream_id` so ordering is preserved per
stream and Flink parallelism scales with partition count:

- **`realtime.ticks`** - one message per row per poll: `{stream_id, org_id,
  column_map, row, status, error, collected_at}`. `column_map` and
  `status`/`error` ride along on every row rather than living in a separate
  config topic - denormalized on purpose; move to a compacted config topic
  if per-row payload size becomes a real concern.
- **`realtime.metrics`** - one message per `(stream_id, window)`, same shape
  as today's `StreamRunner.snapshot()` output (`ts`, `stream_id`, `name`,
  `status`, `error`, `rows_in_window`, `rows_collected`, `duration_s`,
  `last_tick_s`, `metrics`).

```
Integration --> Producer --> Redpanda: realtime.ticks --> Flink job --> Redpanda: realtime.metrics --> Cache consumer --> SSE / alerts.py / history
   (kept)        (new)              (new)                   (new)                (new)                       (new)            (kept, unchanged contract)
```

**Flink job** (new, `backend/streaming/realtime_metrics_job.py`, PyFlink - no
new JVM language in the app itself, just a second deployable process): keyed
by `stream_id`, 5s tumbling window. Per window, replays the same metric
formulas `StreamRunner.snapshot()` uses today (throughput/rate, scrap% /
FPY%, downtime minutes, availability/performance/quality/OEE%) over the
window's rows. `rows_collected` (cumulative) and `duration_s`/`started_at`
(since stream start, not since window start) are carried as Flink keyed
state across windows, matching the old thread's instance-attribute
semantics. `status`/`error` pass through untouched from the latest tick seen
in the window (or the prior window's value if the window is empty) - that's
the producer's connector-health signal, not something Flink computes.

**Cache consumer** (new, `backend/app/services/realtime_cache.py`): an
asyncio background task, started from `app.main`'s startup hook next to
`resume_all_streams()`, consuming `realtime.metrics` and holding
`{stream_id: latest_snapshot}` in memory. **Must use a unique consumer group
per backend replica** (e.g. `f"realtime-cache-{hostname}-{pid}"`), not one
shared group - a shared group would split partitions across replicas and
reintroduce exactly the "not_running_in_this_process" bug this whole change
exists to fix, just for a different reason. Every replica needs to see every
stream's updates, not a shard of them.

## Backend changes

- `app/services/realtime.py`: `StreamRunner` (poll thread + in-memory buffer
  + metric computation) is deleted. `start_runner`/`stop_runner`/
  `resume_all_streams` keep their existing signatures but now start/stop a
  much thinner `TickProducer` (poll the connector, publish raw rows to
  `realtime.ticks`, no buffering, no metric math). `read_history`/
  `delete_history`/`history_path` unchanged - the cache consumer writes to
  the same JSONL files on each `realtime.metrics` message it receives, so
  `stream_history` doesn't change.
- `app/api/routes/realtime.py`: `_snapshot_dict` (line 47) swaps
  `realtime_service.get_runner(stream.id)` for
  `realtime_cache.get_snapshot(stream.id)`. Every route above and below it
  is untouched.
- `app/services/alerts.py`: `eval_stream_metric` (line 296) swaps its
  `get_runner()` call for the same `realtime_cache.get_snapshot()`.
  Threshold comparison logic is untouched.
- New `backend/streaming/` package (deployed separately from the FastAPI
  app - a Flink job isn't something `uvicorn` runs): `realtime_metrics_job.py`
  (the PyFlink job described above) plus a small `schemas.py` shared by the
  producer, the Flink job, and the cache consumer so the JSON shape can't
  drift between the three.

## Deployment

`docker-compose.yml` gets two new services alongside the existing
`db`/`redis`/`backend`/`frontend`:

```yaml
redpanda:
  image: redpandadata/redpanda:latest
  command: redpanda start --smp 1 --memory 1G --overprovisioned --node-id 0 --check=false
  ports: ["9092:9092"]

flink-jobmanager:
  image: flink:1.19-scala_2.12   # + PyFlink installed on top
  command: jobmanager
  environment:
    FLINK_PROPERTIES: "jobmanager.rpc.address: flink-jobmanager"
```

`backend` gains a `REDPANDA_BROKERS` env var and a new `depends_on:
redpanda: {condition: service_started}`. The Flink job itself is submitted
(not baked into an image start command) via `flink run --python
backend/streaming/realtime_metrics_job.py` once the jobmanager is up -
either a one-off `docker-compose run` step in dev, or a real deploy step in
whatever CI/CD does the eventual prod rollout (not designed here; out of
scope until this is closer to prod-bound).

New Python deps: `aiokafka` in `backend/requirements.txt` for the
producer/cache-consumer side (matches the rest of the backend's asyncio
style), and `apache-flink` in a separate `backend/streaming/requirements.txt`
for the job itself - PyFlink pulls in enough that it shouldn't share a
dependency set with the FastAPI app.

## Files touched

Backend:
- `backend/app/services/realtime.py` - `StreamRunner` replaced by
  `TickProducer`; `start_runner`/`stop_runner`/`resume_all_streams`
  internals rewired, signatures unchanged.
- `backend/app/services/realtime_cache.py` - new. Consumer task +
  `get_snapshot(stream_id)`, started/stopped from `app/main.py`'s
  startup/shutdown hooks.
- `backend/app/api/routes/realtime.py` - `_snapshot_dict` reads from
  `realtime_cache` instead of `get_runner()`.
- `backend/app/services/alerts.py` - `eval_stream_metric` reads from
  `realtime_cache` instead of `get_runner()`.
- `backend/streaming/realtime_metrics_job.py` - new PyFlink job.
- `backend/streaming/schemas.py` - new, shared tick/metric message shapes.
- `backend/requirements.txt` - `aiokafka`; new
  `backend/streaming/requirements.txt` - `apache-flink`.
- `app/main.py` - start/stop `realtime_cache`'s consumer task alongside the
  existing `resume_all_streams`/`stop_all_runners` hooks.

Infra:
- `docker-compose.yml` - `redpanda`, `flink-jobmanager` services; `backend`
  gets `REDPANDA_BROKERS` + a new `depends_on` entry.

Nothing changes in `backend/app/models/realtime.py`, any route signature in
`backend/app/api/routes/realtime.py`, the SSE JSON envelope, or any frontend
file - this is entirely an internals swap behind the existing contract.

## Testing

`test_realtime.py`'s existing 8 tests describe the contract this has to keep
passing unchanged (`test_stream_collects_and_computes_metrics`,
`test_stop_then_start_resumes_collection`,
`test_resume_all_streams_reattaches_after_simulated_restart`,
`test_sse_generator_yields_first_snapshot_frame`,
`test_list_streams_attaches_alerts_to_the_right_stream`,
`test_cannot_access_another_orgs_stream`, etc.) - they assert against the
API/SSE surface, not `StreamRunner` internals, so they should run against
the new pipeline with no changes to the test file itself beyond rewiring
`fake_rest_server`. That's the acceptance bar: if any of these need editing
beyond that, the "unchanged contract" claim in this spec is wrong.

New, in `backend/tests/test_realtime_pipeline.py` (needs a real or embedded
Redpanda - `testcontainers`' Redpanda module, or a docker-compose-based test
fixture, since this can't be faked in-process the way `StreamRunner` could):

- A tick published to `realtime.ticks` produces a `realtime.metrics`
  message within one window (5s) with correctly computed `metrics`
  (hand-checked against the same formulas `StreamRunner.snapshot()` used).
- Two consecutive windows correctly carry forward cumulative
  `rows_collected` and `duration_s` (proves the keyed-state carry-forward,
  not just per-window math).
- Two cache-consumer instances with distinct consumer groups both receive
  every message for the same `stream_id` (proves the per-replica-group
  requirement above actually holds - the test this whole spec would be
  wrong without).
- Killing the Flink job mid-stream and restarting it resumes from the last
  committed offset without gap or duplicate window (the actual "why
  Redpanda" proof - replay/restart survival the in-memory `StreamRunner`
  never had).
