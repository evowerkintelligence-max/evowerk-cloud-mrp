# CBM Sensor Readiness — Design

## Problem

The Predictive Maintenance page (`frontend/src/pages/Predictive.tsx`) shows a fleet table driven
by `GET /cbm/fleet` (`backend/app/services/cbm.py` `fleet_dashboard`). Today that function
queries every `RealtimeStream` with a `machine` set and buckets each into `"learning"` (no
baseline yet, or a mapped sensor still under `MIN_BASELINE` readings) or `"active"` (every mapped
sensor has cleared baseline). It does **not** check whether `RealtimeStream.sensor_map` — the
free-form `{"vib_x": "vibration_x_mm_s", ...}` channel map a user fills in on the Realtime page
(`frontend/src/pages/Realtime.tsx:311`) — has anything in it at all.

A machine with a stream but an empty `sensor_map` will never produce a `SensorReading` (nothing
for `app.services.sensor_consumer` to drain into `SensorBaseline`), so it sits in `"learning"`
forever with no way for the user to tell "this just needs more time" apart from "this will never
progress because nothing is mapped." This spec closes that gap: a machine with no sensor mapped
gets its own status, and the UI tells the user what to do about it instead of leaving it stuck.

This is also the first instance of a more general pattern the product needs: gating a
sensor-dependent feature per-machine based on what data actually exists for that machine, with a
CTA back to where the user configures it, rather than either hiding the feature outright or
silently showing a broken/empty state. CBM/RUL is the only consumer of that pattern today.

## Scope decision: what "ready" means, and why no registry yet

Unlike the data-readiness feature (`docs/superpowers/specs/2026-07-21-data-readiness-design.md`),
which checks column-*type* cardinality across ~11 features via a static requirements registry,
CBM has exactly one requirement and one consumer: **does this machine's stream have at least one
entry in `sensor_map`.** CBM's anomaly detection is channel-agnostic (it runs an I-MR control
chart per whatever sensor name the user mapped, per `backend/app/models/sensor.py`'s docstring) —
there's no fixed set of required channel names to check against, so there's nothing to model as a
per-role requirement table the way OEE needs "4 numeric columns."

Building a generic "sensor feature requirements" registry now, for a single consumer, would be
speculative — the right shape of that abstraction won't be clear until a second sensor-dependent
feature exists to compare against. This spec adds one status branch to the one function that
needs it; if/when a second sensor-gated feature shows up, extract the shared shape then.

## Architecture — backend status branch, no new tables or endpoints

### `backend/app/services/cbm.py` — `fleet_dashboard`

Add a branch before the existing `learning`/`active` check: a stream with `machine` set and an
empty `sensor_map` short-circuits to `status: "unmapped"`, skipping the baseline/anomaly/last-reading
queries entirely (there's nothing to query — no sensor readings can exist for an unmapped stream).

```python
for stream in streams:
    machine = stream.machine
    if not stream.sensor_map:
        out.append({
            "machine": machine, "status": "unmapped", "risk_level": "unknown",
            "failure_probability_30d": None, "active_anomalies": 0,
            "last_reading_at": None, "stream_id": str(stream.id),
        })
        continue
    # ... existing baseline/active_anomalies/last_reading/status logic, unchanged
```

`stream_id` is new on every row (not just `unmapped` ones) — cheap to add, and the frontend needs
it for the CTA link target in a later iteration if per-stream deep-linking is ever added (see
Non-goals). For this iteration the frontend doesn't use it; included now because there's no reason
for `"learning"`/`"active"` rows to have a different shape than `"unmapped"` rows in the same
response array.

The final `out.sort(...)` by risk level already defaults unranked levels (anything not in the
`{high, medium, low}` map) to sort-key `4` via `.get(m["risk_level"], 4)` — `"unknown"` already
falls there today for machines with no probability yet, so `"unmapped"` machines sort into the
same last-place bucket. No sort logic changes needed.

## Frontend — `frontend/src/pages/Predictive.tsx`

Three small changes to the existing fleet table (around line 150):

1. Add `unmapped: "Needs sensor mapping"` to the `STATUS_LABEL` map (line 34). Today an unknown
   status falls back to the raw string via `STATUS_LABEL[m.status] ?? m.status`, so this is
   cosmetic but avoids showing the literal word "unmapped" to a user.
2. The other four columns (`risk_level`, `failure_probability_30d`, `active_anomalies`,
   `last_reading_at`) already render correctly for `"unmapped"` rows with no changes —
   `risk_level: "unknown"` hits the existing `RISK_VARIANT["unknown"] = "neutral"` badge,
   `failure_probability_30d: null` already renders `"—"`, `active_anomalies: 0` renders `0`,
   `last_reading_at: null` already renders `"No readings yet"`.
3. Row click currently always calls `setSelectedMachine(m.machine)`, opening `MachineDetail` —
   which has nothing to show for a machine with zero readings. For `status === "unmapped"` rows,
   skip that and instead render a "Configure" button (`<Button size="sm" variant="outline" asChild><Link to="/realtime">Configure</Link></Button>`)
   in place of the click-to-drill-down behavior, linking to the Realtime page where the user
   already edits `sensor_map` (`Realtime.tsx:311`). Not a deep link to the specific stream —
   `/realtime` already lists every stream for the org, and building stream-specific deep-linking
   is more plumbing than a first cut warrants.

`ponytail: CTA links to /realtime generally, not the specific stream. Add deep-link-to-stream
(e.g. /realtime?stream={id}) if users report losing track of which stream to edit once an org has
more than a couple.`

## Testing

One backend unit test in `backend/tests/test_cbm_api.py` (CBM's existing test file):
create a `RealtimeStream` with `machine` set and `sensor_map={}`, call `fleet_dashboard`, assert
the returned row has `status == "unmapped"`, `risk_level == "unknown"`, and no queries against
`SensorBaseline`/`SensorAnomaly`/`SensorReading` are needed to produce it (i.e. it's
distinguishable from a stream with a mapped sensor and zero readings yet, which must still return
`"learning"`).

Frontend: manual smoke test only, matching this repo's existing convention (no component-render
test harness — see the data-readiness plan's Task 2 Step 6 for the precedent). Steps: create a
realtime stream with a machine set and no sensor map, open Predictive Maintenance, confirm the row
shows "Needs sensor mapping" and a Configure button instead of failure-probability data, and that
clicking the row does not attempt to open `MachineDetail`.

## Explicit non-goals (for this iteration)

- No generic "sensor feature requirements" registry — see Scope decision above. CBM is the only
  consumer; extract a shared pattern only once a second one exists.
- No deep link from the Configure CTA to the specific stream's edit form — links to `/realtime`
  generally.
- No change to `MachineDetail.tsx` — unmapped machines never reach it (the fleet table no longer
  routes there for `"unmapped"` rows).
- No validation of *which* sensor channels are mapped (e.g. requiring specifically
  "vibration"-named or "temperature"-named channels) — any non-empty `sensor_map` counts as
  ready, matching CBM's actual channel-agnostic detection logic.
