# Inventory Adjust Approval + Cycle Count

Status: Approved for planning
Date: 2026-07-14

## Problem

`POST /inventory/{item_id}/adjust` (`backend/app/api/routes/inventory.py`) is
a raw signed quantity delta with no upper bound and no second set of eyes.
Today only `owner` can call it at all, which is safe but means every routine
stock correction needs an owner, and there's still nothing stopping an owner
from fat-fingering a large adjustment - it posts immediately either way.

SAP EWM and Manhattan Active WM both enforce the same shape for this: count
what you see -> compute the variance -> require a reason code -> require
supervisor approval above a threshold, before it posts. This spec brings
that pattern in, sized to what this app actually needs (no batch/scheduled
count sessions, no ABC classification - see Scope).

This is the first of several planned Warehouse-feature specs (UI polish,
location hierarchy, directed putaway, stock reservation - tracked
separately); it's sequenced first because it's the highest value relative to
effort and doesn't depend on anything else.

## Scope

In scope: a `manager` role with day-to-day inventory access but a gated
adjust path; a fixed reason-code vocabulary; an org-configurable quantity
threshold; a pending-approval queue for over-threshold adjustments; a new
"count" entry point that computes the delta from a counted quantity instead
of requiring the user to type one; keeping the existing raw delta-entry
adjust for paperwork-only corrections that have no physical count behind
them.

Out of scope (deferred to later specs): batch/scheduled cycle-count
sessions, ABC-classification-driven count frequency, per-location count
assignment, dollar-value thresholds (no persistent item cost field exists
today - see prior conversation), dual-control between two owners.

## Roles and permissions

`User.role` (`String(50)`, already unconstrained - no migration needed for
the value itself) gains a third value, `manager`, alongside the existing
`owner` and `member`.

| Action | member | manager | owner |
|---|---|---|---|
| consume | no | yes | yes |
| transfer | no | yes | yes |
| adjust / count, `\|delta\| <= threshold` | no | yes, posts immediately | yes, posts immediately |
| adjust / count, `\|delta\| > threshold` | no | yes, creates pending request | yes, posts immediately (owner is the approver, nothing queues above them) |
| view pending queue | no | yes (all pending in org, not just own) | yes |
| approve / reject pending request | no | no | yes |
| edit approval threshold | no | no | yes |

`consume_inventory` and `transfer_inventory` in `inventory.py` change from
`require_role("owner")` to `require_role("owner", "manager")`.
`adjust_inventory` (and the new `count_inventory`) keep `require_role("owner",
"manager")` at the dependency level, then branch on `current.role` internally
for the threshold check.

Threshold of `0` is a valid owner setting and needs no special-casing: a
`delta` of exactly `0` is already rejected upstream ("quantity must be a
non-zero correction"), so `abs(delta) <= 0` only ever matches nothing,
meaning every manager adjustment requires approval - which is the intended
behavior for an org that wants full control.

## Data model

`Org` gains:

```
adjust_approval_threshold  numeric(12,4) not null default 50
```

Arbitrary but safe starting default; owner-editable immediately. Existing
orgs have no `manager`-role users yet, so this has no effect until someone
is promoted.

`InventoryTransaction` gains three nullable columns, populated only for
`transaction_type="adjustment"`:

```
counted_quantity  numeric(12,4) nullable   -- set only when posted via /count
expected_quantity numeric(12,4) nullable   -- system on-hand at time of count, for the audit trail
reason_note        varchar(500) nullable    -- elaboration; required by the API when reason="other"
```

The existing `reason` column (`varchar(100)`, currently freeform) is reused,
not renamed - for `transaction_type="adjustment"` its value is now
app-constrained (via a Pydantic `Literal`, not a DB check constraint,
matching this codebase's existing light-touch validation style) to one of:
`cycle_count | damage | found_stock | shrinkage | correction | other`.
Other transaction types (`receipt`, `consumption`, `transfer_out`,
`transfer_in`) are unaffected and keep freeform/absent reason.

New table `inventory_adjustment_requests`, one row per over-threshold
manager submission:

```
inventory_adjustment_requests
  id                            uuid pk
  org_id                        uuid fk orgs.id, cascade delete
  item_id                       uuid fk items.id
  location_id                   uuid fk locations.id, cascade delete
  quantity_delta                numeric(12,4) not null
  counted_quantity              numeric(12,4) nullable
  expected_quantity_at_submit   numeric(12,4) not null   -- display only; see below
  reason_code                   varchar(20) not null
  reason_note                    varchar(500) nullable
  status                         varchar(20) not null default 'pending'  -- pending | approved | rejected
  requested_by                  uuid fk users.id
  requested_at                  timestamptz not null
  reviewed_by                   uuid fk users.id, nullable
  reviewed_at                   timestamptz, nullable
  review_note                    varchar(500) nullable
```

`expected_quantity_at_submit` is informational only - it tells the approving
owner "here's what on-hand looked like when this was requested." It is
**not** re-validated at approval time beyond what `apply_inventory_delta`
already guarantees: approval calls the same guarded UPDATE used everywhere
else in this file, so if on-hand changed since submission such that applying
`quantity_delta` now would drive it negative, the approval call fails with
the existing `InsufficientInventory` 409 - same safety property the
concurrency suite already covers, no new guard code needed.

One migration adds the new column on `orgs`, the three new columns on
`inventory_transactions`, and the new `inventory_adjustment_requests` table.

## Backend API

`backend/app/api/routes/inventory.py`:

- `AdjustRequest` gains `reason_code: ReasonCode` (required) and
  `reason_note: str | None` (required by validator when `reason_code ==
  "other"`, otherwise optional). **Breaking change**: `reason` is optional
  today, so any existing caller of `/adjust` that omits `reason_code` starts
  getting a 422. Accepted as part of this spec, not deferred - call it out in
  the PR description rather than let it fall out silently under "reused, not
  renamed."
- `adjust_inventory`: after existing validation (non-zero, non-lot-tracked,
  location resolution), branch on role:
  - `owner`, or `manager` with `abs(quantity) <= org.adjust_approval_threshold`:
    unchanged path, calls `apply_inventory_delta` and returns immediately.
  - `manager` over threshold: instead of applying, insert an
    `InventoryAdjustmentRequest` row (`status="pending"`,
    `expected_quantity_at_submit` = current on-hand at that location, fetched
    in the same call) and return it. No balance/transaction write happens.
    Audit-logged via the existing `log_event` helper as
    `inventory.adjustment_requested`, following the `inventory.consumed` /
    `inventory.adjusted` / `inventory.transferred` naming already in this
    file.
  - Response model becomes a small union:
    `AdjustmentOutcome { applied: bool, item: InventoryItemResponse | None,
    request: AdjustmentRequestResponse | None }` - exactly one of `item` /
    `request` is populated depending on `applied`.
- New `POST /inventory/{item_id}/count`: body `{ location_id, counted_quantity,
  reason_code = "cycle_count", reason_note }`. Looks up current on-hand at
  that location (0 if no balance row), computes
  `delta = counted_quantity - current`, rejects `delta == 0` with a 400
  ("no variance - nothing to post"), then delegates to the same internal
  helper `adjust_inventory` uses (extracted as a shared function taking an
  already-computed delta) so the threshold/approval logic isn't duplicated.
  Returns the same `AdjustmentOutcome` shape as `/adjust`. On the eventual
  `InventoryTransaction` (whether posted immediately or later via approval),
  `counted_quantity` and `expected_quantity` are populated; for the plain
  `/adjust` path both stay null. `InventoryTransactionResponse` (used by the
  existing `GET /inventory/{item_id}` transaction history) gains the same
  three fields (`counted_quantity`, `expected_quantity`, `reason_note`) so
  the frontend can show the variance inline in the history table without a
  separate request - this is how the UI surfaces count results even for the
  immediate-apply case, where `AdjustmentOutcome` itself only returns the
  updated balances, not the transaction detail.
- New `GET /inventory/adjustment-requests?status=pending` (default filter;
  `status` also accepts `approved`/`rejected`/omitted-for-all): `owner` and
  `manager`, org-scoped, no per-user filtering (managers see the whole
  org's queue, matching the "view pending queue" row above).
- New `POST /inventory/adjustment-requests/{id}/approve`: `owner` only.
  Loads the request (404 if wrong org / already reviewed - `status !=
  "pending"` is a 409, "already reviewed"), calls `apply_inventory_delta`
  with the stored `item_id/location_id/quantity_delta`,
  `transaction_type="adjustment"`, `reason=reason_code`, `reason_note`,
  `counted_quantity`, `expected_quantity=expected_quantity_at_submit`, and
  **`created_by=request.requested_by`** (the ledger attributes the movement
  to whoever asked for it, not the approver - the approver is recorded on
  the request row and in the audit log via the existing `log_event` helper,
  action `inventory.adjustment_approved`).
  Marks the request `approved`, `reviewed_by=current.id`, `reviewed_at=now`.
- New `POST /inventory/adjustment-requests/{id}/reject`: `owner` only, body
  `{ review_note: str | None }`. Marks `rejected`, no balance impact, audit
  logged as `inventory.adjustment_rejected`.

`backend/app/api/routes/orgs.py`:
- `PATCH /orgs/me`: `owner` only, body `{ adjust_approval_threshold: Decimal
  }`. Extends the currently GET-only router with its first mutation.
  Audit-logged as `org.threshold_updated`.

## Frontend

Role gating reuses the existing pattern, not a new one: `Items.tsx` already
fetches `/orgs/me` (`queryKey: ["org-me"]`) and derives
`const isOwner = org.data?.role === "owner"` to gate owner-only UI. The
Approvals tab and the manager/owner branching in `InventoryPanel` should
fetch the same `org-me` query and derive `isOwner` / `isManager` from it,
not invent a separate auth-state mechanism.

`Inventory.tsx` (`InventoryPanel`):
- The existing "Adjust" form stays, relabeled to make its purpose explicit
  (e.g. "Adjust (no count)"), and gains a required reason-code select
  (`<Select>`, same component already used for location pickers) plus a
  conditional note field when "Other" is selected.
- New "Count" form alongside it: quantity input is now framed as "Counted
  quantity" rather than a delta; on submit, shows the computed variance
  before/after (`expected N -> counted M -> variance ±K`) either in the
  success state or, if it needs approval, in the pending confirmation.
  Reason code defaults to "Cycle count" but is still an editable select (a
  count can also surface damage, e.g. "counted correctly but 3 units are
  broken").
- Both forms' mutations handle the new `AdjustmentOutcome` shape: `applied:
  true` refreshes the balances/detail queries as today; `applied: false`
  shows a distinct confirmation ("Submitted for owner approval") instead of
  silently doing nothing.

New third tab on the Warehouse page, "Approvals" (`Warehouse.tsx` `TABS`
array gains a third entry; new `ApprovalsPanel` component alongside
`InventoryPanel`/`LocationsPanel`):
- Table of pending requests: item, location, delta, counted/expected (if
  from a count), reason, requested by/at.
- `manager`: read-only.
- `owner`: approve/reject buttons per row (reject opens a small note input),
  plus the threshold setting (a single labeled number input + save button)
  at the top of the panel.
- Tab is visible to `manager` and `owner`; hidden for `member` (who has no
  inventory access at all, unchanged).

## Testing

Backend (extending `test_inventory.py`, new `test_inventory_adjustment_requests.py`):
- Permission matrix: `member` blocked from all three actions; `manager`
  allowed consume/transfer/adjust/count; threshold boundary (`==` applies
  immediately, `>` queues) for both manager-submitted adjust and count.
- Count endpoint: variance computed correctly against an existing balance
  and against no balance row (treated as 0); `delta == 0` rejected with 400.
- Approval flow: approve applies the delta and writes the transaction with
  `created_by` = original requester, not the approver; reject leaves
  balance untouched; both mark `reviewed_by`/`reviewed_at`; re-reviewing an
  already-reviewed request 409s.
- `reason_code` validation: rejected values 422; `other` without
  `reason_note` rejected.
- `PATCH /orgs/me` threshold update: owner-only, persists, affects
  subsequent threshold checks.

Concurrency (`test_concurrency.py`): one new test - a pending request
approved after a concurrent consume has dropped on-hand below what the
request needs gets a 409 from `apply_inventory_delta`'s existing guard, and
the request stays `pending` (not silently marked approved on failure).

No changes needed to the existing consume/adjust/transfer concurrency tests
- their guard logic is unchanged, only who's allowed to call them and what
happens before the guard is exercised.
