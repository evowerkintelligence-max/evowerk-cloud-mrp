# Live Downtime Tracking

Status: Approved for planning
Date: 2026-07-16

## Problem

The existing "Downtime" analytics page (`backend/app/api/routes/downtime.py`,
`backend/app/services/downtime.py`, `backend/app/analytics/downtime_calc.py`,
`frontend/src/pages/analytics/Downtime.tsx`) computes Pareto-by-category,
Pareto-by-machine, MTTR, and a daily trend - but only over a CSV/Excel file
someone already uploaded. It has no live capture path: incidents have to
already exist as rows in a file before this page can say anything about them.

Pace Tracking (`backend/app/services/pace_tracking.py`) proved the pattern
for closing this kind of gap: a live session that materializes into a normal,
immutable `Dataset` row via the exact same ingestion path a CSV upload uses
(`datasets.create_dataset`), so every downstream analytics feature works on
it with zero changes. But Pace Tracking's own downtime capture (pause/resume
with a `breakdown`/`setup`/`other` reason) only produces 3 aggregate minute
buckets per session - enough for the OEE Six Big Losses chart, not enough for
this page's Pareto-by-category/MTTR, which need one row per incident (its own
duration, reason, machine, timestamp).

This spec adds a live multi-machine downtime board: operators mark a machine
down with a reason and shift, mark it back up, and every closed incident
continuously (re)materializes into one perpetually-updated `Dataset` per org
- so the existing Downtime page (and, for shift comparison, the existing
Shifts page) can be pointed at it like any other dataset, live.

## Scope

In scope: a live board tracking any number of machines' up/down state
independently and concurrently; per-incident capture (machine, reason, shift,
start/end time); a fixed 6-value reason taxonomy pre-mapped to
planned/unplanned; continuous re-materialization of closed incidents into one
auto-updating `Dataset` per org; RBAC for incident actions vs. discard; a new
frontend page.

Out of scope for v1 (explicit product decisions, not oversights):
- No shift *schedule* (no time-of-day-based auto-tagging). Shift is a
  free-text field the reporter picks per incident, same treatment as
  `machine` - not a config screen, not derived from a clock.
- No machine/asset registry. A machine is a string; "known machines" for
  autocomplete is just `DISTINCT machine` over past incidents, not a managed
  list.
- No org-configurable reason codes - the 6 reasons and their planned/
  unplanned mapping are hardcoded, same posture as Pace Tracking's 3 pause
  reasons.
- No editing or annotating (notes/comments) on an incident, open or closed.
- No uniqueness enforcement beyond "one open incident per machine name" - two
  differently-spelled labels for the same physical machine are the reporter's
  problem, not a guarded constraint (same posture as Pace Tracking's `line`).
- No manual "export" step: materialization is automatic on every incident
  close. (Considered and rejected - see Materialization.)

## Data model

New file `backend/app/models/downtime_tracking.py`:

```python
class DowntimeIncident(Base):
    __tablename__ = "downtime_incidents"
    id: UUID (pk)
    org_id: UUID (fk orgs.id, cascade delete, indexed)
    started_by: UUID (fk users.id)
    machine: str        # free text, e.g. "Line 3 - Press 2"
    reason: str          # breakdown | changeover | material_shortage |
                          # planned_maintenance | quality_hold | other
    shift: str           # free text, e.g. "A" / "1st" / "Night"
    started_at: datetime
    ended_at: datetime | None   # NULL = still down

class DowntimeLiveDataset(Base):
    __tablename__ = "downtime_live_datasets"
    org_id: UUID (pk, fk orgs.id, cascade delete)
    dataset_id: UUID (fk datasets.id, cascade delete)
```

`DowntimeLiveDataset` is a one-row-per-org pointer, created the first time any
incident closes for that org and overwritten (its `dataset_id` never
changes) on every close after that.

Reason -> planned/unplanned is a hardcoded dict in
`downtime_tracking.py`, not a column:

```python
_REASON_PLANNED = {
    "breakdown": False,
    "changeover": True,
    "material_shortage": False,
    "planned_maintenance": True,
    "quality_hold": False,
    "other": False,
}
```

Migration: one new file adding both tables, following the existing
`f3a8b2e7c641_report_inbox_items.py` pattern (org-scoped FK + cascade
delete).

## RBAC

Same posture as Pace Tracking (`app/models/user.py` only has `owner`/
`member`):

- **View** (`GET /downtime-tracking/incidents`, `/machines`, `/shifts`,
  `/live-dataset`): org-scoped only, any authenticated member.
- **Start / End** (`POST /incidents`, `POST /incidents/{id}/end`):
  org-scoped only, no creator check - this is a shared board, any member on
  shift should be able to close out a machine someone else marked down
  (same shift-handoff reasoning as Pace Tracking's tap/pause/resume/stop).
- **Discard** (`DELETE /incidents/{id}`, for "marked down by mistake," only
  while still open - see below): creator-or-owner, identical rule to
  `pace_tracking.py`'s `discard_session_route`. Logged via
  `log_event(action="downtime_incident.discarded", ...)`.
- Discard is rejected (409) once an incident has `ended_at` set - a closed
  incident has already been folded into the materialized dataset; deleting
  it after the fact would mean silently rewriting history a user might
  already be looking at on the Downtime page. Correcting a mis-recorded
  *closed* incident is out of scope for v1 (see Scope).
- Starting an incident for a `machine` that already has one open is a 409,
  not a queue - one open incident per machine name at a time.

## Materialization

Manual "Export" was considered and rejected: the whole point of a live board
is that the Downtime page reflects reality without someone remembering to
click a button, and Pace Tracking's one-shot-on-Stop model doesn't fit a
board that never stops. Instead:

On `POST /downtime-tracking/incidents/{id}/end`:

1. Set `ended_at = now()`.
2. Query every closed incident for the org (`ended_at IS NOT NULL`), ordered
   by `started_at`.
3. Build an in-memory CSV: header
   `machine,reason,planned,shift,duration_min,started_at,ended_at`, one row
   per closed incident (`duration_min = (ended_at - started_at) / 60`,
   `planned = _REASON_PLANNED[reason]`).
4. Look up `DowntimeLiveDataset` for the org.
   - **First close ever for this org:** call `datasets.create_dataset(...)`
     (same function the upload endpoint and Pace Tracking both use) with
     `name="Live Downtime Board"`, store the new `dataset_id` in a new
     `DowntimeLiveDataset` row.
   - **Otherwise:** call a new `datasets.overwrite_dataset_file(db, dataset,
     data=csv_bytes)` that re-sniffs columns/row_count and rewrites the
     existing `Dataset` row's file **in place**, keeping the same
     `dataset_id` (so anything that bookmarked/deep-linked it keeps working).
5. Response includes the live `dataset_id`.

`overwrite_dataset_file` writes **atomically** (temp file in the same
directory, then `os.replace`) rather than reusing `storage.save_dataset_file`'s
plain `path.write_bytes` - that function was only ever called once, before
the `Dataset` row exists and before any reader could be mid-request against
it; this file gets rewritten repeatedly while the Downtime page may be
reading it concurrently, so a torn write is a real new failure mode this
introduces, not a preexisting one. This also means updating the docstring on
`app/models/dataset.py::Dataset` ("read-only after upload... no mutable state
here beyond deletion") to note the one exception.

Every column name (`machine`, `reason`, `duration_min`, `started_at`) already
matches the Downtime page's existing auto-mapping regex hints
(`/machine/i`, `/reason/i`, `/duration/i`, `/start/i`) - **no changes needed
to `Downtime.tsx` or the analytics engine.** To see planned-vs-unplanned
Pareto instead of by-reason, a user points the page's existing "Category"
field at the `planned` column instead of `reason`. To see it by shift, the
existing Shifts page (`Shifts.tsx`/`shifts.py`) can be pointed at the same
live dataset with its shift-column field mapped to `shift`.

## API

New router `backend/app/api/routes/downtime_tracking.py`, prefix
`/downtime-tracking`:

- `POST /incidents` - `{machine, reason, shift}` -> creates incident
  (`started_at=now()`, `ended_at=None`); 409 if `machine` already has one
  open.
- `POST /incidents/{id}/end` -> 409 if already ended; runs materialization
  above; response includes `live_dataset_id`.
- `DELETE /incidents/{id}` -> creator-or-owner; 409 if already ended (see
  RBAC).
- `GET /incidents?open=true` -> org-scoped list of currently-open incidents
  (the board), newest first.
- `GET /machines` -> `DISTINCT machine` over the org's incidents, for
  autocomplete.
- `GET /shifts` -> `DISTINCT shift` over the org's incidents, for
  autocomplete.
- `GET /live-dataset` -> `{dataset_id: str | null}` - lets the frontend show
  a "View live analytics" link even when nothing has closed since the page
  last loaded.

## Frontend

New page `frontend/src/pages/analytics/DowntimeTracking.tsx`, nav entry in
`frontend/src/lib/nav.ts` (`/analytics/downtime-tracking`, label "Downtime
Tracking", icon `AlertTriangle` - not yet imported in `nav.ts`, needs adding
to the `lucide-react` import list) and a route in `App.tsx`. Not built on
`EngineScreen` (shaped around "map columns from an uploaded dataset," doesn't
fit a live multi-machine board) - a standalone page, styled like
`PaceTracking.tsx`.

Flow:
- **Report downtime** mini-form: Machine (text input + `<datalist>` from
  `GET /machines`), Reason (select, 6 fixed options with human labels, same
  pattern as Pace Tracking's `_PAUSE_REASONS`/`PAUSE_LABELS`), Shift (text
  input + `<datalist>` from `GET /shifts`, pre-filled from `localStorage` so
  an operator isn't retyping it on every incident during one shift, but
  editable), "Mark down" button.
- **Currently down** list: `GET /incidents?open=true`, polled every 5s (same
  `POLL_MS` pattern as `PaceTracking.tsx`). Each row: machine, reason badge,
  shift, live elapsed timer, "Mark up" button, and (for the reporter or an
  owner) a discard control.
- Once `GET /live-dataset` returns a non-null id, show a persistent "View
  live analytics" button deep-linking to
  `/analytics/downtime?dataset_id=<id>` (same query-param convention
  `EngineScreen` already reads on mount).
- Extract `PaceTracking.tsx`'s `useElapsed`/`asUtcMs` helpers into
  `frontend/src/pages/analytics/shared.tsx` so both pages import one copy
  instead of duplicating the UTC-naive-timestamp workaround.

## Files touched

Backend:
- `backend/app/models/downtime_tracking.py` - `DowntimeIncident`,
  `DowntimeLiveDataset`.
- `backend/alembic/versions/` - new migration for both tables.
- `backend/app/services/downtime_tracking.py` - `start_incident`,
  `end_incident` (incl. materialization), `discard_incident`,
  `list_open`, `list_machines`, `list_shifts`.
- `backend/app/services/datasets.py` - new `overwrite_dataset_file`.
- `backend/app/services/storage.py` - new atomic `replace_dataset_file`
  (temp file + `os.replace`).
- `backend/app/models/dataset.py` - docstring update noting the one
  mutable-after-upload exception.
- `backend/app/api/routes/downtime_tracking.py` - the 7 endpoints above.
- `backend/app/main.py` - register the new router.

Frontend:
- `frontend/src/pages/analytics/DowntimeTracking.tsx` - new page.
- `frontend/src/pages/analytics/shared.tsx` - extract `useElapsed`/`asUtcMs`.
- `frontend/src/pages/analytics/PaceTracking.tsx` - switch to the shared
  helpers instead of its local copies.
- `frontend/src/lib/nav.ts`, `frontend/src/App.tsx` - nav entry + route.

## Testing

Backend (`pytest`, following `test_pace_tracking.py` conventions), new
`backend/tests/test_downtime_tracking.py`:

- Start an incident -> appears in `GET /incidents?open=true`; starting a
  second incident for the same `machine` while open -> 409.
- End an incident -> disappears from the open list; `GET /live-dataset`
  returns a dataset whose one row has the expected `duration_min` (hand-
  computed from start/end) and `planned` matching `_REASON_PLANNED[reason]`.
- Close a second incident -> the live dataset now has 2 rows (not
  overwritten/replaced) and the same `dataset_id` as after the first close.
- `shift` flows through: two incidents with different `shift` values produce
  two distinct values in the materialized `shift` column.
- Tenant isolation: org B cannot see org A's open incidents, cannot end
  org A's incident, and org A's live dataset is invisible to org B.
- Discard: creator can discard their own open incident (204, removed from
  open list); a different non-owner member gets 403; an owner can discard
  anyone's; discarding an already-closed incident is 409.
- End-to-end: after closing 3+ incidents with a mix of reasons/machines,
  `GET /live-dataset`'s `dataset_id` fed straight into
  `POST /analytics/downtime` (`duration_col=duration_min,
  category_col=reason, machine_col=machine, timestamp_col=started_at`)
  produces Pareto/MTTR output matching hand-calculated values from the
  known incidents (same style as the existing
  "pace-tracking materializes into working OEE" test).
- Atomic overwrite: closing incidents back-to-back doesn't corrupt the
  dataset file (read it back via DuckDB after each close and confirm row
  count matches expectations - guards against a non-atomic write leaving a
  torn/partial CSV under concurrent access).

Frontend: no automated test runner in this repo (consistent with prior
specs' precedent). Verify manually in-browser: `tsc -b` typecheck, then mark
2-3 machines down with different reasons/shifts, mark them back up, follow
the "View live analytics" link and confirm the Downtime page's Pareto/MTTR
reflect what was just entered, then mark another machine down/up and reload
the Downtime page to confirm it picks up the new row without re-uploading
anything.
