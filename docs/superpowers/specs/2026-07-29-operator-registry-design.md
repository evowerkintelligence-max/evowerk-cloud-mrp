# Operator Registry

Status: Approved for planning
Date: 2026-07-29

Prerequisite for `2026-07-29-operator-performance-shifts-design.md`, for
`2026-07-29-operator-identity-capture-design.md` (which depends on this one
in turn), and for the live operator board spec that follows both. Build
order: this spec first, then either of the other two.

## Problem

The system has no concept of an operator as an entity.

Historically this gap was papered over by Pace Tracking, whose
`PaceSession.operator` was a free-text string typed at session start. Pace
Tracking has been removed: the product direction is sensor-first, with
dataset upload as the fully supported path for customers without sensors, and
a manual per-unit tap-counting flow fits neither. Its removal does not
introduce this gap, it only removes the thing that was incidentally covering
it. Dataset operator columns remain, and are likewise whatever an MES export
happened to write, with no registry behind them.

The models directory has `Machine`, `WorkCenter`, `User` (tenant logins), and
`StaffAccount` (EvoWerk's own employees). Nothing represents a customer's
operators.

The consequence, once operator-level features exist: two people typing
`J Kim` and `Kim` become two operators, someone who forgets becomes
`operator 1`, and a historical leaderboard computed from a dataset column
cannot be reconciled with a live board computed from a different capture
path. The same person appears twice with different numbers, on a screen that
ranks people.

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
a management UI; and a migration that backfills the roster from operator
names already recorded in datasets. A `badge_id` field is included here
because it belongs to the entity, though nothing in this spec issues or reads
it — that is `2026-07-29-operator-identity-capture-design.md`.

Out of scope (explicit decisions, not oversights):

- **How identity gets captured live.** Reader scans, QR check-ins, and
  schedule-based inference are `2026-07-29-operator-identity-capture-design.md`
  in full. This spec is the roster those tiers resolve against, not the
  capture mechanism itself.
- **Operator authentication beyond a badge ID.** No PIN, no biometric. A
  reader scan or a QR tap records who was *selected/scanned*, not who was
  verifiably present — a limitation the capture spec states plainly and this
  registry inherits. Adding stronger auth later is additive.
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
- `badge_id` (String 64, nullable), what a reader scan carries. Nullable for
  the same reason as `code`, and because most rows are created by dataset
  auto-discovery or a QR tap, neither of which has a badge to record.
- `status` (String 16, default `active`)
- `needs_review` (Boolean, default False)
- `created_at`

`UniqueConstraint(org_id, normalized_name)`. `UniqueConstraint(org_id,
badge_id)` where `badge_id` is not null, so two people cannot resolve to the
same badge within an org while still allowing every badge-less row through.

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

**Live capture must resolve to the roster, not free text.** This is where
phantom operators are born, and unlike historical data it is a moment where
the system can ask for the right answer at no cost. The identity-capture
spec's three tiers all resolve through `resolve_or_create_operator` rather
than storing a typed string: a reader scan resolves by `badge_id`, a QR tap
either picks an existing roster row or creates one explicitly (an intentional
act, not a typo), and a work-order assignment already stores an
`operator_id` because it is a foreign key from the start, never free text.
This registry has no live-entry surface of its own; every capture path is
owned by the identity-capture spec and simply calls into this one.

### Merge

`POST /operators/{id}/merge` with a target operator id. Repoints every
capture event and every `WorkOrder.assigned_operator_id` from the source to
the target, then deletes the source.

Merge exists because without it the cleanup queue cannot be cleaned.
Renaming `J Kim` to `J. Kim` when `J. Kim` already exists violates the unique
constraint, so rename alone leaves the duplicate permanently.

### API

- `GET /operators`, supporting a `needs_review` filter for the cleanup queue
- `POST /operators`
- `PATCH /operators/{id}` for name, code, status, and clearing `needs_review`
- `POST /operators/{id}/merge`
- `DELETE /operators/{id}`, rejected with 409 when a capture event or a
  `WorkOrder.assigned_operator_id` references it, directing the caller to
  merge or deactivate instead

### Migration

Create `operators`, with `badge_id` from day one so the identity-capture spec
does not need a second migration to add it. No backfill source exists yet in
this migration: unlike the machine registry (which had existing free-text
`machine` columns across several live tables to seed from), there is no
existing operator data in this codebase to migrate, since Pace Tracking is
gone and dataset operator columns are backfilled lazily, the first time each
dataset is analyzed, not by this migration. The roster starts empty and fills
as datasets are analyzed and capture events start arriving.

### Frontend

An **Operators** tab on the Production page, alongside Work centers, Machines,
and Routings, mirroring `MachinesPanel` in
`frontend/src/pages/Production.tsx`.

The list shows name, code, badge status, and last-seen time (most recent
capture event or dataset resolution, whichever is newer). Rows with
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
- Merging repoints the source's capture events and work-order assignments to
  the target and deletes the source.
- `DELETE` on an operator referenced by a capture event or a work-order
  assignment returns 409.
- A `badge_id` already used by another operator in the same org is rejected;
  the same `badge_id` in a different org is accepted.

## Open questions

None blocking.

Worth revisiting once real data exists: whether `FUZZY_MATCH_THRESHOLD` of 0.8,
inherited from the machine matcher, suits human names. Machine codes like
`CNC-01` and `CNC-02` are near-identical by edit distance but genuinely
different assets, whereas `Kim` and `Kin` are more likely one person. The
threshold may want to differ per entity type.
