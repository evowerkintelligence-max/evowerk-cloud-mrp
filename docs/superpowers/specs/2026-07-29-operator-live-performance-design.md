# Operator Live Performance (Operators tab)

Status: Approved for planning
Date: 2026-07-29

This is the "live operator board" feature explicitly deferred by
`2026-07-29-operator-performance-shifts-design.md` ("It gets its own spec
after identity capture ships."). That spec covers a different question —
ranking operators against a *dataset column* on the Shift Comparison engine.
This spec covers who is actually on the floor and how much floor-time they
log, sourced from the live identity-capture system
(`app.models.capture_event.CaptureEvent`, `app.models.operator.Operator`),
which has already shipped (see recent commits registering these models and
the `/operators` + `/checkin` routes).

## Problem

The Operators tab (`OperatorsPanel` in `frontend/src/pages/Production.tsx`)
manages the operator roster — add, edit, merge, delete — but shows nothing
about what operators actually do. Every badge/QR check-in is captured
(`CaptureEvent`: operator, machine, started_at, ended_at) and the org already
configures a weekly shift schedule (`ShiftSchedule`, used today by the Shift
Comparison page to resolve a "current shift" from wall-clock time). Nobody
combines the two: there is no way to see, per shift, who was actually
checked in, for how long, or across how many machines.

## Scope

In scope: a new card on the Operators tab showing, per operator per shift,
over a configurable recent window: total checked-in minutes, check-in count,
and distinct machines worked; a ranked chart of total time for a selected
shift; a "currently on floor" live indicator; graceful behavior when no
shift schedule is configured.

Out of scope:

- **Output/quality metrics** (units, scrap, OEE per operator). `CaptureEvent`
  only records presence at a machine, not what was produced. The only
  quantity data in the system (`WorkOrder.quantity_good/quantity_scrapped`)
  attaches to a work order via `assigned_operator_id`, a plan-level
  assignment, not an observed one — mixing the two would conflate "who was
  scheduled" with "who was present." That distinction is real work
  (`resolve_attribution` already treats them as separate tiers) and belongs
  in its own spec if wanted later.
- **The dataset-column operator dimension on Shift Comparison.** Covered by
  `2026-07-29-operator-performance-shifts-design.md`, queued as the next
  piece of work after this ships.
- **Splitting a single capture event across a shift boundary.** An event is
  bucketed entirely by its `started_at`, matching the simplification
  `resolveCurrentShift` already makes for "now."
- **Per-operator access control / editing this data.** Read-only view.

## Design

### Backend: one read-only endpoint

`GET /operators/capture-events?days=N` (`N: int = Query(7, ge=1, le=90)`),
authenticated via `get_current_user` (read-only, no role restriction — same
tier as `list_operators`).

Query: `CaptureEvent` joined to `Operator` (name) and `Machine` (name),
filtered to `org_id == current.org_id` and `started_at >= now - N days`,
ordered by `started_at`. Response:

```python
class CaptureEventItem(BaseModel):
    operator_id: str
    operator_name: str
    machine_id: str
    machine_name: str
    started_at: datetime
    ended_at: datetime | None
```

Returns a flat list. No shift math server-side — see below for why.

### Why shift-bucketing happens in the browser

`ShiftSchedule.start_time`/`end_time` are "HH:MM browser-local" strings with
no stored timezone (see the model's own docstring — this matches the rest of
the app's no-timezone-handling design). `CaptureEvent.started_at` is stored
UTC. The existing `resolveCurrentShift` in `Shifts.tsx` already reconciles
these correctly for "now" by converting to the browser's local hour and
minute before comparing against the schedule. Doing the same bucketing
server-side would require guessing a timezone the backend doesn't have.
Instead, `resolveCurrentShift(schedule, now: Date)` is generalized to
`resolveShiftForTime(schedule, at: Date)` (same body, parameter renamed),
used both for the existing "now" call site and for every historical capture
event's `started_at`. One function, two call sites, no duplicated logic.

### Frontend: new card in `OperatorsPanel`

Fetches (react-query):
- `/analytics/shifts/schedule` (already used by `Shifts.tsx`; the hook
  `useShiftSchedule` is exported from `shared.ts`/moved somewhere importable
  from both pages, or duplicated inline if extraction proves awkward — a call
  during implementation, not a spec decision)
- `/operators/capture-events?days=N`, `N` from a days selector (7 / 30 / 90,
  default 7)

Client-side aggregation, per `(operator_id, shift_label)`:
- `total_minutes`: sum of `(ended_at ?? now) - started_at`, clamped ≥ 0
- `checkin_count`: number of events
- `machine_count`: count of distinct `machine_id`

If no shift schedule rows exist, every event buckets into a single
`"Unassigned"` pseudo-shift and the card shows a one-line message pointing at
the Shift Comparison page's schedule editor.

Card layout (reusing `EChart`/`rankedBarOption`/`C` from
`pages/analytics/shared.ts`, same pattern as Shift Comparison's "By machine"
card):
- Days selector (7/30/90)
- Shift selector + ranked bar chart: categories = operator names, values =
  total hours, for the selected shift
- Table: Operator | Shift | Total time | Check-ins | Machines
- A "currently on floor" badge next to any operator with an open event
  (`ended_at IS NULL`) anywhere in the fetched window, independent of the
  shift/days filters — a live status, not a historical aggregate

### Error handling & edge cases

- No shift schedule → "Unassigned" bucket + pointer message (not an error).
- Event straddling a shift boundary → bucketed by `started_at` only.
- Operator with zero events in the window → absent from the table, not a
  zero-row.
- `days` outside 1–90 → 422 at the request boundary, no clamping.

## Testing

Backend: one test on `/operators/capture-events` — seeds capture events
across two orgs and across the date cutoff, asserts org-scoping and that
events older than `days` are excluded.

Frontend: one render test for the new card in `Production.tsx` (mirrors the
existing `*.render.test.tsx` pattern already used for `Production.tsx` and
`CheckIn.tsx`), covering the no-schedule-configured empty state and a
populated table with a "currently on floor" badge.

## Open questions

None blocking. Whether `useShiftSchedule` gets extracted to a shared module
or duplicated is an implementation-time call, not a design decision — both
are a few lines either way.
