# Operator Registry

Status: Approved for planning
Date: 2026-07-29

Prerequisite for `2026-07-29-operator-performance-shifts-design.md` and for the
live operator board spec that follows it.

## Problem

The system has no concept of an operator as an entity.

`PaceSession.operator` (`backend/app/models/pace_tracking.py`) is a free-text
string typed at session start, validated only as non-empty
(`backend/app/api/routes/pace_tracking.py`). `PaceSession.created_by` is a
real authenticated `users.id`, but on a shared shop-floor tablet that is one
login for the whole station, not the person doing the work. Dataset operator
columns are likewise whatever an MES export happened to write.

The models directory has `Machine`, `WorkCenter`, `User` (tenant logins), and
`StaffAccount` (EvoWerk's own employees). Nothing represents a customer's
operators.

The consequence, once operator-level features exist: two people typing
`J Kim` and `Kim` become two operators, someone who forgets becomes
`operator 1`, and the historical leaderboard computed from a dataset column
cannot be reconciled with a live board computed from typed session names. The
same person appears twice with different numbers, on a screen that ranks
people.

The codebase already faced this for machines. `Machine`
(`backend/app/models/machine.py`) is an org-scoped registry with a
`needs_review` flag documented as "a cleanup queue, not a blocker", and
`registry_matching.py` holds the free-text-to-registry logic. Honest caveat:
only `normalized_similarity` there is currently exercised, by
`backend/tests/test_registry_matching.py`. `resolve_or_create_machine` has no
caller in this branch, and the `scripts/backfill_machine_registry.py` its
docstring references does not exist. So this spec inherits a proven similarity
function and a clear pattern, not a working pipeline.

## Scope

In scope: an `Operator` entity; free-text resolution with a fuzzy-duplicate
cleanup queue; a merge operation so the cleanup queue can actually be cleaned;
roster selection when starting a pace session; a management UI; and a
migration that backfills the roster from operator names already recorded.

Out of scope (explicit decisions, not oversights):

- **Operator authentication.** No PIN, no badge scan, no per-operator login.
  Starting a session means picking a name from a list, which records who was
  *selected*, not who was verifiably present. This is a deliberate v1
  limitation and should not be described to customers as verified identity.
  Adding a PIN later is additive and does not invalidate this model.
- **Linking operators to `User` accounts.** Shop-floor operators typically
  have no login, and coupling the roster to tenant users would drag in
  licensing and onboarding.
- **Skills, certifications, shift assignments, labour cost.** An operator here
  is an identity for attributing production data, nothing more.
- **Retrofitting the `Machine` registry.** `resolve_or_create_machine` stays
  as-is. This spec adds a sibling, it does not fix machines.

## Design

### Model

`Operator` (`backend/app/models/operator.py`), mirroring `Machine`:

- `id`, `org_id` (FK `orgs.id` cascade, indexed)
- `name` (String 255), the display name, shown as originally spelled
- `normalized_name` (String 255), trim plus casefold of `name`
- `code` (String 50, nullable), an employee ID when the customer has one.
  Nullable because auto-discovery from a dataset yields only a name.
- `status` (String 16, default `active`)
- `needs_review` (Boolean, default False)
- `created_at`

`UniqueConstraint(org_id, normalized_name)`.

The unique constraint is on the normalized name rather than the display name
because normalization is the identity rule: `J. Kim` and `j. kim` must not
coexist. Storing it as a column rather than computing it per query makes
resolution an indexed lookup instead of a scan.

### Resolution

`resolve_or_create_operator(db, org_id, raw_name) -> Operator` joins
`resolve_or_create_machine` in `registry_matching.py`:

1. Normalize the input, then look up `(org_id, normalized_name)`. A hit
   returns the existing row. This is the overwhelmingly common path and costs
   one indexed query.
2. On a miss, compare against existing operator names using the existing
   `normalized_similarity`. Create the new row either way, setting
   `needs_review = True` when something scores at or above
   `FUZZY_MATCH_THRESHOLD` (0.8), since a near-miss is more likely a typo than
   a new hire.

A near-duplicate creates a row and flags it. It never blocks and never
silently merges, because silently merging two real people is worse than
showing two rows and asking.

### Population, and why it differs by path

**Dataset analysis auto-discovers.** Analyzing a dataset with an operator
column resolves every distinct value through the above, so a customer with
historical data gets a roster with no setup step. Historical analysis is never
blocked by an unknown name, because the data already exists and refusing to
show it helps nobody.

**Live pace sessions must pick from the roster.**
`PaceSessionCreateRequest.operator: str` becomes
`operator_id: uuid.UUID`. Free text at the point of entry is where phantom
operators are born, and unlike historical data this is a moment where the
system can ask for the right answer at no cost. Creating a genuinely new
operator from the session screen stays possible, it is just an explicit act
rather than a typo.

This is a breaking API change, made safe by the fact that no frontend code
calls `/pace/sessions` today.

`PaceSession` gains `operator_id` (FK `operators.id`) and **keeps** its
`operator` string column, populated from the roster row's name at session
creation. The string is a deliberate snapshot: `stop_session` materializes a
session into an immutable Dataset whose `operator` column records who ran the
work at that time, and renaming someone in the roster later must not rewrite
history. It also leaves the materialization path in
`backend/app/services/pace_tracking.py` unchanged.

### Merge

`POST /operators/{id}/merge` with a target operator id. Repoints every
`PaceSession.operator_id` from the source to the target, then deletes the
source.

Merge exists because without it the cleanup queue cannot be cleaned.
Renaming `J Kim` to `J. Kim` when `J. Kim` already exists violates the unique
constraint, so rename alone leaves the duplicate permanently. Merge does not
touch the denormalized `operator` string on existing sessions or any already
materialized Dataset, for the same immutability reason given above.

### API

- `GET /operators`, supporting a `needs_review` filter for the cleanup queue
- `POST /operators`
- `PATCH /operators/{id}` for name, code, status, and clearing `needs_review`
- `POST /operators/{id}/merge`
- `DELETE /operators/{id}`, rejected with 409 when sessions reference it,
  directing the caller to merge or deactivate instead

### Migration

Create `operators`. Add nullable `operator_id` to `pace_sessions`. Backfill
one roster row per distinct existing `PaceSession.operator` value per org,
resolving through the same normalization so pre-existing case variants
collapse, then point each session at its row.

`operator_id` stays nullable rather than being made non-null after backfill,
because a session whose operator row is later deleted should degrade to its
name snapshot rather than fail to load.

### Frontend

An **Operators** tab on the Production page, alongside Work centers, Machines,
and Routings, mirroring `MachinesPanel` in
`frontend/src/pages/Production.tsx`.

The list shows name, code, status, and session count. Rows with
`needs_review` sort first under a banner naming the likely duplicate and
offering Merge or Keep both, so the cleanup queue is the first thing a
customer sees rather than something buried behind a filter.

## Testing

- Resolving an existing name, in different case and with surrounding
  whitespace, returns the same row and creates nothing.
- Resolving a name close to an existing one creates a row with
  `needs_review = True`; resolving a clearly unrelated name creates one with
  `needs_review = False`.
- Two orgs each resolving the same name get separate rows.
- Merging repoints the source's sessions to the target and deletes the source;
  the sessions' `operator` string snapshots are unchanged.
- `DELETE` on an operator with sessions returns 409.
- Creating a pace session with an `operator_id` from another org is rejected.
- The migration collapses `J Kim` and `j kim` in existing sessions into one
  roster row and points both sessions at it.

## Open questions

None blocking.

Worth revisiting once real data exists: whether `FUZZY_MATCH_THRESHOLD` of 0.8,
inherited from the machine matcher, suits human names. Machine codes like
`CNC-01` and `CNC-02` are near-identical by edit distance but genuinely
different assets, whereas `Kim` and `Kin` are more likely one person. The
threshold may want to differ per entity type.
