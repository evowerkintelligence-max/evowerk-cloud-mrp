# Work Orders: closing the make loop (Phase 3)

Date: 2026-07-27
Status: approved design, pending implementation plan
Follows: `docs/superpowers/specs/2026-07-27-sales-orders-design.md` (Phase 2, shipped)

## Context

Phase 1 gave the shop one planning grid. Phase 2 made real customer orders a
demand source, so the grid goes red because a commitment is at risk. Both
phases tell the user what to make. Neither lets them record that they made
it.

The make loop is open at both ends. Manufacturing never moves stock: a shop
can build 50 pumps and the app still believes the seals are on the shelf and
the pumps are not. Nothing produced can fulfil a sales order. And the
estimate the build calculator already carries, `yield_loss_pct`, has nothing
recording actual yield against it.

The definition side already exists and is not being rebuilt here: `Bom` is
the recipe, and `Routing` / `RoutingStep` / `WorkCenter` / `Machine` describe
how a thing is made. What is missing is the execution record, the row that
says this job is running now.

Plant analytics are a separate gap worth naming. `PaceSession` keys off a
free-text `line` and `operator` with no link to an item, work center or job,
so OEE today cannot say what was being made. This phase does not fix that
(see Non-goals) but it is the reason work orders carry their own timing and
yield data rather than relying on the analytics subsystems.

## Goals

- Record production: a job consumes components and produces finished goods
  through the existing stock paths.
- Close the loop on the screen where the decision is made, so the grid's
  "you need 50 pumps" becomes one button.
- Stop the grid double-counting: a running job is incoming supply, so the
  app never tells you to make the same 50 pumps twice.
- Produce real yield data (planned vs good vs scrap, with start and finish
  times) per product.

## Non-goals

- **No operation-level tracking.** A job records which routing it used but
  is not started and finished step by step. The operator sees one button,
  not a checklist. This keeps the Phase 1 principle that engine concepts
  stay invisible.
- **No shop-floor execution.** No operator clock-in, machine assignment, or
  queue scheduling.
- **No analytics integration.** Pace sessions, OEE, downtime and throughput
  are untouched. Work orders emit their own yield and output data; wiring
  that into the existing dashboards, and giving `PaceSession` a real job
  reference, is a later phase.
- **No work-order-to-sales-order link.** The grid already nets demand from
  open sales orders, so making to plan serves those orders implicitly.
  Adding an explicit link would add a visible concept for no gain today.
- **No partial finishing.** A job is finished once, with its good and scrap
  counts. Units are not booked in as they come off the line.
- **No money.** Consistent with Phase 2: no cost, labour rate or valuation
  anywhere on a work order.

## 1. Data model

Three tables. Every quantity is `Numeric(12, 4)`, matching the rest of the
app.

**`WorkOrder`**

- `wo_number`: server-computed `max + 1` per org, never user-supplied, plain
  int. Same allocation and race guard as `SalesOrder.so_number` and
  `PurchaseOrder.po_number`, including the `begin_nested()` savepoint retry.
- `item_id`: what is being made.
- `bom_id`: the recipe *version* in force when the job started. Snapshotted,
  not looked up live, so editing a BOM next month cannot rewrite what this
  job consumed.
- `routing_id`: nullable. Recorded because the definition exists and is
  useful history; not tracked step by step.
- `quantity_planned`, `quantity_good`, `quantity_scrapped`.
- `status`: `in_progress` | `done` | `cancelled`. See section 2.
- `location_id`: where components come from and finished goods go, resolved
  via `resolve_location_id(db, org_id, None)` to the org default, the same
  fallback sales order completion uses.
- `started_at`, `finished_at`: the timing half of the yield data.
- `notes`, `created_by`, `created_at`, `updated_at`.

**`WorkOrderComponent`**, one row per component item.

- `wo_id`, `item_id`, `sequence`.
- `quantity_required`: BOM quantity times planned quantity, including that
  BOM line's own `scrap_pct`.
- `quantity_consumed`: what actually left stock.

This cannot be re-derived from the BOM later, which is exactly why it is
stored: the BOM may change while the job is running, and cancel has to
return what was taken rather than what the recipe now says.

**`WorkOrderComponentLot`**, one row per lot draw, for lot-tracked
components only.

- `wo_component_id`, `lot_id`, `quantity`.

Written from the `LotConsumption` list that `consume_lot_fefo` already
returns. It exists solely so cancel can put stock back in the lots it came
out of. `LotTransaction` carries no job reference, so there is no way to
recover the split from the existing ledger.

## 2. Lifecycle

`in_progress -> done`, or `in_progress -> cancelled`. There is deliberately
no unstarted state, and creating a job starts it.

That is not simplification for its own sake. If a not-yet-started job
counted as supply, it would zero out the finished good's net requirement,
and `_explode_and_net` would then never explode its components. The grid
would read "all in stock", the operator would start the job, and it would
fail for want of seals. Only `in_progress` counts as supply, and by that
point the components have physically left stock, so `on_hand` already
reflects them and there is nothing left to double-count.

A scheduled or draft state can be added later without rework: `status` is a
string, and the engine counts only `in_progress`.

**Start** (that is, create) resolves the item's active BOM, computes each
component requirement, consumes stock, and stamps `started_at`.

**Finish** takes `quantity_good` and `quantity_scrapped`, adds only the good
units to stock, and stamps `finished_at`. Scrap needs no stock movement:
the components left at start, so scrapped units simply never arrive. Good
and scrap may exceed the planned quantity, because over-runs happen and
refusing them would make the operator lie.

**Cancel** returns every consumed component, restoring lot-tracked stock to
the exact lots it was drawn from, and is the escape hatch for a mis-keyed
job.

## 3. Consuming and producing stock

There is no single stock path, and this is the part most likely to be got
wrong. `apply_inventory_delta` maintains `InventoryBalance` and does not
itself check for lot tracking; sending a lot-tracked item down that path
writes a balance row alongside the lots and double-counts the stock. Lot
stock is consumed with `consume_lot_fefo` and created as a new `Lot`. So
both start and finish branch per item on `is_lot_tracked`, exactly as sales
order completion does.

Finishing a lot-tracked product needs a `lot_number` for the units being
created, mirroring how a purchase order receipt names the lot it is
receiving into.

Everything in a start, a finish or a cancel shares one transaction, so a job
that is short on its third component rolls back the first two.

## 4. Reaching the engine

`compute_mrp_plan` nets `gross_demand - on_hand - on_order - on_draft`.
Work orders add a fourth supply source, `in_production`: the sum of
`quantity_planned` across `in_progress` jobs, per item.

This goes inline in `app/services/mrp.py` beside the existing three, not in
a new service module. Phase 2 created `app/services/demand.py` because
demand combination holds a real policy decision (`consume` vs `add`) worth
isolating and unit-testing. Work-order supply has no policy: it is one
grouped sum. A module created for symmetry with Phase 2 would be a module
created for no reason.

`MrpPlanLine` carries `in_production` as its own field rather than folding
it into `on_order`, following the note already in that file about keeping
supply sources separately visible.

## 5. Screens

**Planning grid.** Each product row gains a Make action, which raises and
starts a job for the quantity the grid is already recommending. It is
disabled when materials are short, because starting would only 409. The row
shows what is in production, in the same quiet secondary style as Phase 2's
committed line.

**Production page.** Lists running and recent jobs with number, product,
planned quantity, status and timing, and is where a job is finished or
cancelled. It mirrors the Sales and Procurement pages, so the app needs no
third set of conventions.

Finish opens a confirmation carrying the good and scrap inputs, since it
moves stock and cannot be undone. Cancel likewise says the components will
go back.

## 6. Roles

Raising, finishing and cancelling a job is owner or manager, consistent with
Phase 2 treating order entry as a shop-floor supervisor action. Reading is
any member. Gating is enforced in the backend and mirrored in the frontend
via `useCurrentUser()`, following `Planning.tsx`.

Feature key `production`. The migration that ships it must append the key to
every org already holding entitlements, or the feature arrives 403-ing for
every existing customer;
`backend/tests/test_feature_migration_coverage.py` enforces this.

## 7. Error handling

House voice: lowercase, terse, no trailing period, concrete numbers or SKU
interpolated. 400 for malformed input, 409 for state or stock conflicts.

- Components short at start: 409 naming the shortfall, surfaced from the
  existing inventory and lot messages rather than a new pre-flight check.
- Item has no active BOM: 400, naming the SKU. A thing with no recipe
  cannot be built.
- Finishing a lot-tracked product with no `lot_number`: 400.
- Negative good or scrap: 400. Quantities beyond `Numeric(12, 4)` range:
  400, matching the bound the planning grid write and sales order lines use,
  because Postgres would otherwise raise a numeric overflow as a 500.
- Finishing or cancelling a job that is not `in_progress`: 409.
- The job row is locked `for_update` on finish and cancel, so two concurrent
  calls cannot both pass the status check.

## 8. Testing

- Start consumes exactly BOM quantity times planned, including each BOM
  line's `scrap_pct`, on both the lot-tracked and non-lot-tracked branch.
- A job short on its **third** component rolls back the first two. A
  single-component shortfall proves nothing, because there is nothing to
  roll back; this is the specific gap Phase 2's review found.
- Finish adds only good units to stock, and scrap is recorded without
  moving stock.
- Cancel restores the **exact lot split**, not merely the right total.
- An `in_progress` job nets as supply; `done` and `cancelled` do not.
- A running job does not suppress planning for its own components, since
  those are already consumed and reflected in `on_hand`.
- Org scoping: cross-org access 404s. Role gates: a member can read but
  cannot raise, finish or cancel.
- The `production` feature key is backfilled onto existing orgs.

## Known limitations

- **No unstarted jobs.** A job cannot be raised today to start tomorrow.
  Section 2 records why, and the upgrade path.
- **Single location per job.** Components come from and finished goods go
  to one location, the org default. A shortfall message names the quantity
  but not which location it was short at.
- **Over-run is unbounded.** Nothing stops finishing a job for 50 with 5000
  good units beyond the column range check, because there is no legitimate
  ceiling to enforce and refusing real output would be worse.
- **Analytics stay decoupled.** Yield and output data exist per job, but
  OEE still cannot say what was being made until a later phase gives
  `PaceSession` a job reference.
