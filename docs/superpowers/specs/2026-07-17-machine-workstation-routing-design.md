# Machine / Work Center / Routing Master Data

Status: Approved for planning
Date: 2026-07-17

## Problem

This app has no shop-floor asset registry. `machine` and `line` are free-text
`String` columns scattered across unrelated tables (`DowntimeIncident.machine`,
`MaintenanceSchedule.machine`, `PaceSession.line`), each typed independently by
whoever filled in the form - "CNC-01", "cnc 1", "CNC01" can all refer to the
same physical machine with no way to know that. There is also no concept of a
process routing: `Bom`/`BomLine` model what an item is made of, not the
sequence of operations/stations it passes through to get made.

This is sub-project A of a two-part effort to turn the existing single-shot
what-if [Simulation](../../../frontend/src/pages/Simulation.tsx) calculator
into a real discrete-event simulation (DES) engine. A DES cannot exist without
knowing what stations exist, their capacity/cycle-time/failure
characteristics, and what order a product moves through them - that's this
project. The DES engine itself (background-job execution, stochastic
breakdowns, queueing, results) is sub-project B, specced separately once this
schema exists to build against.

Independent of the simulation motivation, this registry is useful on its own:
it gives Downtime, Maintenance Schedule, and Pace Tracking a real, consistent
notion of "which machine" instead of three independently-typo'd free-text
columns.

## Scope

In scope:
- New tables: `WorkCenter`, `Machine`, `Routing`, `RoutingStep`.
- Retrofit migration: `DowntimeIncident.machine`, `MaintenanceSchedule.machine`
  get a new `machine_id` FK; `PaceSession.line` gets a new `work_center_id`
  FK. Old free-text columns are kept permanently as an audit fallback for
  pre-migration rows; they stop being written to once the forms below ship.
- Automated backfill: exact-match (trimmed, case-insensitive) free-text
  values merge into one registry row; anything else (fuzzy-close to another
  value, or unique) becomes its own row flagged `needs_review=true`.
- CRUD API + admin UI for `WorkCenter`, `Machine`, `Routing`/`RoutingStep`.
- Updating the three existing forms (Downtime, Maintenance Schedule, Pace
  Tracking) to pick from the registry instead of free-typing.

Out of scope (deliberately, not an oversight):
- **The DES engine itself** - sub-project B, specced separately against this
  schema once it exists.
- **A manual free-text-to-registry mapping review UI as a blocking step.**
  The backfill runs automatically; `needs_review` rows are cleaned up through
  the normal Machine/WorkCenter edit screens, not a bespoke reconciliation
  tool.
- **Multi-plant/multi-site hierarchy above `WorkCenter`.** Everything is
  `org_id`-scoped like the rest of the schema; no separate "Site" or "Plant"
  entity is introduced here.
- **Capacity-planning math, MTBF/MTTR statistical sampling, or anything that
  runs a simulation.** This project only stores the numbers; sub-project B
  decides how to sample them.

## Architecture

```
Existing free-text columns (unchanged, kept as audit fallback):
  DowntimeIncident.machine (String)
  MaintenanceSchedule.machine (String)
  PaceSession.line (String)
       |
       | one-time backfill script (per org, per column):
       |   1. group distinct values (trim + casefold)
       |   2. exact-match groups -> one registry row
       |   3. everything else -> its own row, needs_review=true
       v
  New registry (this project):
    WorkCenter (routing step type, e.g. "CNC Milling")
       |-- Machine (physical unit, e.g. "CNC-01"), N per WorkCenter
       |
    Item (existing) --< Routing (versioned, like Bom) --< RoutingStep --> WorkCenter
       v
  New nullable FKs added alongside the old free-text columns:
    DowntimeIncident.machine_id -> Machine
    MaintenanceSchedule.machine_id -> Machine
    PaceSession.work_center_id -> WorkCenter
       v
  Existing forms (Downtime, Maintenance Schedule, Pace Tracking) switch from
  free-text input to a Select bound to the registry for all new rows.
```

## Data model

All new tables follow the existing `org_id`-scoping, `Uuid` PK, and
`UniqueConstraint("org_id", "code", ...)` conventions used by `Location`.

```python
class WorkCenter(Base):
    __tablename__ = "work_centers"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_work_centers_org_code"),)

    id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]                      # FK orgs.id, cascade delete, indexed
    code: Mapped[str]                               # String(50)
    name: Mapped[str]                                # String(255)
    status: Mapped[str]                              # "active" | "inactive" | "retired", default "active"
    machine_capacity: Mapped[int]                    # count of interchangeable machines, default 1
    default_cycle_time_seconds: Mapped[float | None]
    default_mtbf_hours: Mapped[float | None]
    default_mttr_hours: Mapped[float | None]
    created_at: Mapped[datetime]


class Machine(Base):
    __tablename__ = "machines"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_machines_org_code"),)

    id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    work_center_id: Mapped[uuid.UUID]                # FK work_centers.id, cascade delete, indexed
    code: Mapped[str]
    name: Mapped[str]
    status: Mapped[str]                              # "active" | "inactive" | "retired", default "active"
    mtbf_hours: Mapped[float | None]                 # overrides WorkCenter.default_mtbf_hours when set
    mttr_hours: Mapped[float | None]                 # overrides WorkCenter.default_mttr_hours when set
    needs_review: Mapped[bool]                       # default False; set True by backfill on fuzzy/unique auto-create
    created_at: Mapped[datetime]


class Routing(Base):
    __tablename__ = "routings"
    __table_args__ = (UniqueConstraint("org_id", "item_id", "version", name="uq_routings_org_item_version"),)

    id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    item_id: Mapped[uuid.UUID]                       # FK items.id, cascade delete, indexed
    version: Mapped[int]                              # server-computed (max version for item_id + 1)
    status: Mapped[str]                               # "draft" | "active" | "archived"
                                                        # one-active-per-item enforced in the activate route,
                                                        # identical pattern to Bom.status
    created_at: Mapped[datetime]


class RoutingStep(Base):
    __tablename__ = "routing_steps"
    __table_args__ = (UniqueConstraint("routing_id", "sequence", name="uq_routing_steps_routing_sequence"),)

    id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    routing_id: Mapped[uuid.UUID]                    # FK routings.id, cascade delete, indexed
    sequence: Mapped[int]                             # 1-based step order
    work_center_id: Mapped[uuid.UUID]                # FK work_centers.id
    cycle_time_seconds: Mapped[float | None]          # overrides WorkCenter.default_cycle_time_seconds when set
    setup_time_seconds: Mapped[float | None]
```

New nullable FK columns on existing tables (additive migration, no data loss):

```python
DowntimeIncident.machine_id: Mapped[uuid.UUID | None]      # FK machines.id, SET NULL on delete
MaintenanceSchedule.machine_id: Mapped[uuid.UUID | None]   # FK machines.id, SET NULL on delete
PaceSession.work_center_id: Mapped[uuid.UUID | None]       # FK work_centers.id, SET NULL on delete
```

`ondelete="SET NULL"` (not cascade) on these three - a retired/deleted machine
should never take historical downtime/maintenance/pace rows down with it; the
old free-text value remains as the fallback display.

## Backfill migration

One Alembic migration (schema) + one data-migration script, run per org,
per source column:

1. Read all distinct non-null free-text values for that org/column.
2. Group by `value.strip().casefold()`. Each group's canonical `Machine`/
   `WorkCenter` row uses the most common original casing/spacing as `name`,
   and a slugified version as `code` (deduped with a numeric suffix on
   collision, same approach `Location.code` uniqueness already assumes).
3. A group formed from >1 distinct raw string is an exact-match merge -
   `needs_review=False`.
4. A group of exactly 1 raw string is checked against every other group's
   canonical name using normalized Levenshtein distance
   (`1 - distance / max(len(a), len(b))`); if similarity >= 0.8 against any
   other group, both are created as separate rows with `needs_review=True`.
   Otherwise `needs_review=False` (it's just a normal single-machine name,
   not a review case).
5. Backfill the new FK column on every source row from its free-text value's
   resolved registry row.

`needs_review` rows are ordinary rows from day one - visible and usable
immediately, just flagged in the list UI so cleanup is discoverable instead
of mandatory-before-shipping.

## API

New routers under `backend/app/api/routes/`, mirroring `locations.py`'s shape
(Pydantic request/response models, `require_role("owner")` on mutations,
`log_event` audit logging, `IntegrityError` -> 409 on duplicate `code`):

- `work_centers.py`: `GET/POST /work-centers`, `PATCH /work-centers/{id}`
- `machines.py`: `GET/POST /machines` (filterable by `work_center_id`,
  `needs_review`), `PATCH /machines/{id}`
- `routings.py`: `GET/POST /routings` (filterable by `item_id`),
  `POST /routings/{id}/activate`, `GET/POST/DELETE /routings/{id}/steps`

Status transitions (`active` -> `inactive`/`retired`) go through `PATCH`;
there is no `DELETE` on `WorkCenter`/`Machine` once referenced by any
`RoutingStep`, `DowntimeIncident`, `MaintenanceSchedule`, or `PaceSession` row
- the route returns 409 and the client is expected to retire instead.

## Frontend

New pages under a "Line Setup" section in nav, matching `Locations.tsx`'s
existing list/create pattern:
- `WorkCenters.tsx` - list + create/edit, `needs_review` badge/filter.
- `Machines.tsx` - list + create/edit, filterable by work center,
  `needs_review` badge/filter.
- `Routings.tsx` - per-`Item` routing builder: pick an item, see its
  versions, add/reorder/remove steps against the `WorkCenter` list, activate
  a draft version.

Existing forms updated from free-text input to `Select` bound to the
registry:
- Downtime incident form -> `machine_id` select (falls back to showing the
  legacy free-text value read-only for rows created before this ships).
- Maintenance schedule form -> `machine_id` select, same fallback.
- Pace tracking session form -> `work_center_id` select, same fallback.

## Error handling

- Duplicate `code` within an org -> 409, same `IntegrityError` pattern as
  `Location`.
- Deleting/retiring a `Machine`/`WorkCenter` referenced elsewhere -> blocked
  (409) for hard delete; `status="retired"` is always allowed (it's not a
  delete, existing references keep resolving).
- Missing `cycle_time_seconds`/`mtbf_hours`/`mttr_hours` at every level
  (step -> machine -> work center default) is allowed here - null cascades
  all the way through. This project only stores the numbers; sub-project B's
  DES engine is responsible for refusing to run against incomplete data, not
  this one.
- Backfill script is idempotent and re-runnable: re-running after new
  free-text rows have been added since the last run only processes values
  that don't already have a resolved FK, never creates duplicate registry
  rows for already-matched values.

## Testing

- `test_work_centers.py`, `test_machines.py`, `test_routings.py`: create /
  list / duplicate-code-conflict (409) / retire-blocks-hard-delete /
  filter-by-needs_review, matching `test_locations.py`'s and `test_bom.py`'s
  existing style.
- `test_routing_versioning.py` (or folded into `test_routings.py`): mirrors
  `Bom`'s version/status tests - server-computed version increment,
  one-active-per-item enforcement on activate.
- `test_machine_backfill.py`: synthetic messy dataset (exact-case
  duplicates, near-duplicate typos, unique names) run through the backfill
  script; asserts exact merge counts, correct `needs_review` flags, and that
  every source row's new FK resolves to the expected registry row.
  Re-running the script on the same fixture is asserted to be a no-op
  (idempotency).
