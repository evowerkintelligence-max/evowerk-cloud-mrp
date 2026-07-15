# Manual/Assembly-Line Pace Tracking

Status: Approved for planning
Date: 2026-07-15

## Problem

The OEE engine (`backend/app/analytics/oee_calc.py`, `backend/app/api/routes/oee.py`,
`frontend/src/pages/analytics/Oee.tsx`) assumes a pre-existing dataset with
planned/operating-time and unit-count columns, uploaded as CSV/Excel and
column-mapped by the user. That model has no answer for an assembly line with
no machine sensors or time-tracking system, where the only data source is an
operator tapping a button per completed unit. This gap was flagged in a prior
competitive analysis against TeepTrak (memory: `teeptrak-comparison`, gap #5).

This spec adds a live tap-tracking session that, on completion, materializes
into a normal, immutable `Dataset` row via the exact same ingestion path a CSV
upload uses (`datasets.create_dataset`) - so every downstream engine feature
(Availability/Performance/Quality, Six Big Losses, RCA, TEEP, the
clamped-value and duplicate/date-gap sanity checks) works on pace-tracking
data with zero changes, because from `oee.py`'s perspective it's just another
dataset.

## Scope

In scope: session lifecycle (start/tap/pause/resume/stop) for one line at a
time; downtime-reason capture (breakdown/setup/other) accumulated correctly
across multiple pause cycles; materialization of a stopped session into one
`Dataset` row via `create_dataset`; RBAC for session actions vs. session
discard; a new frontend page with tap buttons and session controls.

Out of scope for v1 (explicit product decisions, not oversights):
- No cross-session history/trend stitching - each Stop produces a
  standalone 1-row Dataset. Trend charts and RCA drop-periods have nothing
  to show until multiple sessions exist and a user manually compares
  datasets; no auto-rebuild-from-history.
- No multi-device/offline tap queuing, no per-operator login/kiosk auth
  beyond the existing org login. No uniqueness enforcement on `line` - the
  system won't stop two overlapping sessions from being started against the
  same line label; that's a business-process expectation, not a guarded
  constraint.
- No editing/undo of an individual tap after the fact (a wrong tap is
  absorbed into the aggregate; only the whole session can be discarded pre-stop).

## Data model

New file `backend/app/models/pace_tracking.py`, alongside `realtime.py`
(closest existing precedent: a durable row + simple lifecycle state, not the
read-only `Dataset` model):

```python
class PaceSession(Base):
    __tablename__ = "pace_sessions"
    id: UUID (pk)
    org_id: UUID (fk orgs.id, cascade delete, indexed)
    created_by: UUID (fk users.id)
    line: str                      # free text, e.g. "Assembly Line 3"
    operator: str                  # free text, e.g. "J. Alvarez"
    target_rate: float | None      # units/min, optional - same meaning as
                                    # OeeComputeRequest.target_rate
    status: str                    # "active" | "paused" | "stopped"
    pause_reason: str | None       # "breakdown" | "setup" | "other" - only
                                    # meaningful while status == "paused"
    paused_at: datetime | None     # when the current pause began
    unplanned_dt_min: float = 0.0  # accumulator - breakdown pause time
    planned_dt_min: float = 0.0    # accumulator - setup/changeover pause time
    other_dt_min: float = 0.0      # accumulator - "other" pause time (not
                                    # exported as its own column - see Materialization)
    started_at: datetime
    stopped_at: datetime | None
    dataset_id: UUID | None        # fk datasets.id, set once materialized

class PaceTap(Base):
    __tablename__ = "pace_taps"
    id: UUID (pk)
    session_id: UUID (fk pace_sessions.id, cascade delete, indexed)
    kind: str                      # "good" | "reject"
    ts: datetime
```

`PaceTap` rows are the raw event log (kept for audit/debugging); the session
row's accumulators plus `SELECT COUNT(*) ... WHERE kind = ...` are what
materialization actually reads.

Migration: one new file adding both tables, following the existing
`f3a8b2e7c641_report_inbox_items.py` pattern (org-scoped FK + cascade delete).

## Session lifecycle (state machine)

States: `active -> paused -> active -> ... -> stopped` (terminal). Every
mutating endpoint checks the current status and 409s on an invalid
transition (tap/pause while `stopped`; pause while already `paused`; resume
while not `paused`).

**Multi-cycle pause accounting** (the thing to get right): a shared internal
helper `_finalize_pause(session, now) -> None` does the actual accumulation -
computes `elapsed_min = (now - session.paused_at) / 60`, then runs an atomic
SQL increment (`UPDATE pace_sessions SET unplanned_dt_min = unplanned_dt_min
+ :elapsed WHERE id = :id`, not a Python load-mutate-commit round trip, so
two near-simultaneous requests can't lose an update) into whichever of
`unplanned_dt_min`/`planned_dt_min`/`other_dt_min` matches `pause_reason`,
then clears `paused_at`/`pause_reason`. Both `resume()` and `stop()` (when
called while `status == "paused"`) call this same helper before proceeding -
`stop()` while paused must finalize the in-progress pause first, otherwise
that final open pause window never goes through `resume()` and silently
never gets counted. One code path, called from two places, so the two can't
drift apart.

A session paused three times for "breakdown" across a shift ends up with all
three durations summed in `unplanned_dt_min` - each `resume()`/`stop()` call
adds to the running total, never overwrites it.

## RBAC

Roles in this app are `owner`/`member` only (`app/models/user.py`). Operator
identity here is a free-text label, not a login - the authenticated user
acting on a session and the "operator" name on the row are different things,
so gating mutations by `created_by` would break the exact shift-handoff case
this feature exists for.

- **View** (`GET /pace/sessions`, `GET /pace/sessions/{id}`): org-scoped
  only (`current.org_id` filter), any authenticated member. Same as the
  reports inbox list today.
- **Tap / Pause / Resume / Stop** on an `active`/`paused` session:
  org-scoped only, **no creator check**. This is the deliberate
  shift-handoff allowance - Operator B must be able to pick up Operator A's
  still-running session and eventually stop it.
- **Discard** (new `DELETE /pace/sessions/{id}`, for "started by mistake" -
  throws away taps, produces no Dataset): creator-or-owner, identical rule
  to `reports.py:delete_inbox_item` - `if session.created_by != current.id
  and current.role != "owner": raise HTTPException(403, ...)`. Logged via
  `log_event(action="pace_session.discarded", ...)`, matching this branch's
  audit-log convention for deletes.
- `DELETE` (discard) works regardless of status - `active`, `paused`, or
  `stopped` (the main use case is discarding a session started by mistake
  before it's ever stopped; discarding a `stopped` one just removes it from
  the list, its Dataset is untouched). Everything else - tap/pause/resume/
  stop - 409s on a `stopped` session for everyone regardless of role;
  that's a status check, not a permission check.

## Materialization

On `POST /pace/sessions/{id}/stop`:

1. If `status == "paused"`, call `_finalize_pause()` first.
2. `planned_min = (stopped_at - started_at)` in minutes.
3. `operating_min = planned_min - unplanned_dt_min - planned_dt_min - other_dt_min`
   (floored at 0 defensively, though a correct state machine shouldn't need it).
4. `total_units = COUNT(*) WHERE kind IN ('good','reject')`,
   `good_units = COUNT(*) WHERE kind = 'good'`.
5. Build a one-row CSV in memory:
   `line,operator,planned_min,operating_min,total_units,good_units,unplanned_dt_min,planned_dt_min,target_rate`
   (`target_rate` column included only if the session set one; downstream
   `oee.py` already treats a missing/blank target-rate column as
   fully-estimated Performance, so this needs no special-casing).
6. Call `datasets.create_dataset(db, org_id=..., created_by=..., name=f"Pace: {line} - {operator} ({stopped_at:%Y-%m-%d %H:%M})", filename="pace_session.csv", data=csv_bytes)` -
   the exact same function the upload endpoint calls, so sniffing/validation
   is identical to any other dataset. Store the resulting `dataset.id` on
   `PaceSession.dataset_id`.
7. Response includes `dataset_id` so the frontend can link straight to it.

`other_dt_min` is deliberately **not** an exported CSV column: it still
reduces `operating_min` (that time truly wasn't running), but the resulting
gap between `planned_min` and `operating_min + unplanned_dt_min +
planned_dt_min` is exactly what `oee_calc._six_big_losses` already buckets
as "Unclassified Downtime" - no new bucket type needed.

## API

New router `backend/app/api/routes/pace_tracking.py`, prefix `/pace`:

- `POST /pace/sessions` - `{line, operator, target_rate?}` -> creates
  session (`status="active"`, `started_at=now()`).
- `POST /pace/sessions/{id}/tap` - `{kind: "good"|"reject"}` -> 409 unless
  `active`.
- `POST /pace/sessions/{id}/pause` - `{reason: "breakdown"|"setup"|"other"}`
  -> 409 unless `active`.
- `POST /pace/sessions/{id}/resume` -> 409 unless `paused`; runs
  `_finalize_pause()`.
- `POST /pace/sessions/{id}/stop` -> 409 if already `stopped`; runs
  materialization above.
- `DELETE /pace/sessions/{id}` -> creator-or-owner only (see RBAC).
- `GET /pace/sessions` - org-scoped list, newest first (for resuming after a
  page reload / picking up someone else's active session).
- `GET /pace/sessions/{id}` - live status: elapsed time, tap counts by kind,
  current pause state.

## Frontend

New page `frontend/src/pages/analytics/PaceTracking.tsx`, nav entry next to
OEE/Downtime/Scrap in `frontend/src/lib/nav.ts` and a route in `App.tsx`
(`/analytics/pace-tracking`). Not built on `EngineScreen` (that component is
shaped around "map columns from an uploaded dataset," which doesn't fit a
live session) - a standalone page with its own state.

Flow: Start form (Line, Operator, optional target rate) -> live screen with
large Good/Reject tap buttons, live counters (good/reject/elapsed), and
Start/Pause▾/Resume/Stop controls (Pause▾ opens a small reason picker:
Breakdown/Setup-Changeover/Other; while paused, a banner shows the reason
and elapsed pause time with a Resume button). `GET /pace/sessions/{id}`
polled on mount/reload so an in-progress session survives a page refresh.
On Stop, show a confirmation with a link to the new Dataset (deep-linked
into `/analytics/oee`, pre-selecting that dataset in the existing picker).

## Files touched

Backend:
- `backend/app/models/pace_tracking.py` - `PaceSession`, `PaceTap`.
- `backend/alembic/versions/` - new migration for both tables.
- `backend/app/services/pace_tracking.py` - lifecycle functions
  (`start_session`, `record_tap`, `pause_session`, `resume_session`,
  `stop_session` incl. `_finalize_pause`/materialization, `discard_session`).
- `backend/app/api/routes/pace_tracking.py` - the 8 endpoints above.
- `backend/app/main.py` - register the new router.

Frontend:
- `frontend/src/pages/analytics/PaceTracking.tsx` - new page.
- `frontend/src/lib/nav.ts`, `frontend/src/App.tsx` - nav entry + route.

## Testing

Backend (`pytest`, following `test_oee.py`/`test_reports.py` conventions):
- Full lifecycle: start -> tap good x N -> tap reject x M -> stop ->
  materialized Dataset has the expected `planned_min`/`operating_min` (no
  pauses, so `operating_min == planned_min`) /`total_units`/`good_units`.
- Multi-cycle pause accounting: pause(breakdown) -> resume ->
  pause(breakdown) -> resume -> stop; assert `unplanned_dt_min` is the SUM
  of both pause windows, not just the last one.
- Stop-while-paused: pause -> stop (no resume call); assert the open pause
  window is still counted (the exact bug being guarded against).
- Mixed reasons: one breakdown pause + one setup pause + one "other" pause;
  assert `unplanned_dt_min`/`planned_dt_min` reflect only their own reasons,
  `operating_min` subtracts all three, and the materialized dataset run
  through `/analytics/oee` puts the "other" gap into "Unclassified Downtime".
- State-machine guards: tap/pause/resume/stop all 409 on a `stopped`
  session; pause 409s if already `paused`; resume 409s if not `paused`.
- RBAC: a different org member can tap/pause/resume/stop someone else's
  active session (handoff allowed); `DELETE` 403s for a non-creator
  non-owner, 204s for the creator and for an owner.
- End-to-end: materialized dataset fed into `POST /analytics/oee` produces
  sane Availability/Performance/Quality matching hand-calculated values from
  the tap counts and pause durations (same style as today's `test_oee.py`
  additions).

Frontend: no automated test runner in this repo (consistent with prior
specs' precedent). Verify manually in-browser: `tsc -b` typecheck, then
start a session, tap several good/reject, pause/resume through all three
reasons, stop, follow the link into the OEE dashboard and confirm the
numbers match.
