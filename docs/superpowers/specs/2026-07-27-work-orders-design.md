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

Plant analytics are the other half. `PaceSession` keys off a free-text
`line` and `operator` with no link to an item, work center or job, so OEE
today cannot say what was being made. Work orders give it something real to
point at.

## Goals

- Record production: a job consumes components and produces finished goods
  through the existing stock paths.
- Close the loop on the screen where the decision is made, so the grid's
  "you need 50 pumps" becomes one button.
- Let a shop plan ahead: a job can be scheduled today and started tomorrow,
  and a scheduled job holds its components so another job cannot quietly
  eat them first.
- Make the schedule date mean something: a job late to start says so, and a
  job scheduled beyond the planning horizon does not distort today's plan.
- Stop the grid double-counting: a job in flight is incoming supply, so the
  app never tells you to make the same 50 pumps twice.
- Produce real yield data (planned vs good vs scrap, with start and finish
  times) per product, and let OEE say which product a session was running.

## Non-goals

- **No operation-level tracking.** A job records which routing it used but
  is not started and finished step by step. The operator sees one button,
  not a checklist. This keeps the Phase 1 principle that engine concepts
  stay invisible.
- **No shop-floor execution.** No operator clock-in, machine assignment, or
  queue scheduling.
- **No cross-location sourcing within one job.** A job names one location,
  and its components come from and its output goes to that location. Pulling
  one job's components from several locations at once is a warehouse
  feature, not a work-order one.
- **No priority between scheduled jobs.** Reservations are held in the order
  jobs were raised; there is no way to say one job outranks another and
  should take contested stock. A shop that needs that is asking for
  scheduling, which is the phase after this one.
- **No work-order-to-sales-order link.** The grid already nets demand from
  open sales orders, so making to plan serves those orders implicitly.
  Adding an explicit link would add a visible concept for no gain today.
- **No partial finishing.** A job is finished once, with its good and scrap
  counts. Units are not booked in as they come off the line.
- **No rescheduling of a running job.** `scheduled_start` can be changed
  while a job is scheduled, not after it starts.
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
- `bom_id`: the recipe *version* in force when the job was raised.
  Snapshotted, not looked up live, so editing a BOM next month cannot
  rewrite what this job reserved or consumed.
- `routing_id`: nullable. Recorded because the definition exists and is
  useful history; not tracked step by step.
- `quantity_planned`, `quantity_good`, `quantity_scrapped`.
- `status`: `scheduled` | `in_progress` | `done` | `cancelled`.
- `location_id`: where components come from and finished goods go. Chosen
  when the job is raised, defaulting to the org default via
  `resolve_location_id(db, org_id, None)`.
- `scheduled_start`: nullable date. When the shop intends to begin. Drives
  the horizon bound in section 4 and the late flag on the Production page,
  which is derived in the response rather than stored, exactly as Phase 2
  derives a sales order's `is_overdue`.
- `started_at`, `finished_at`: the timing half of the yield data.
- `notes`, `created_by`, `created_at`, `updated_at`.

**`WorkOrderComponent`**, one row per component item.

- `wo_id`, `item_id`, `sequence`.
- `quantity_required`: BOM quantity times planned quantity, including that
  BOM line's own `scrap_pct`. Computed when the job is raised.
- `quantity_consumed`: what actually left stock, still 0 while scheduled.

This cannot be re-derived from the BOM later, which is exactly why it is
stored: the BOM may change between raising and starting a job, and both the
reservation and the cancel path have to reflect what this job actually
committed to rather than what the recipe now says.

**`WorkOrderComponentLot`**, one row per lot draw, for lot-tracked
components only.

- `wo_component_id`, `lot_id`, `quantity`.

Written from the `LotConsumption` list that `consume_lot_fefo` already
returns. It exists solely so cancel can put stock back in the lots it came
out of. `LotTransaction` carries no job reference, so there is no way to
recover the split from the existing ledger.

## 2. Lifecycle

```
scheduled ──start──> in_progress ──finish──> done
    │                      │
    └──────cancel──────────┴──> cancelled
```

**Raise** computes the component requirements from the item's active BOM and
writes them, but moves no stock. The job is `scheduled`, and from this
moment it holds those components against other jobs (section 3). From the grid's
Make button the common path is raise-and-start in one action; raising for a
later date is the same call with `scheduled_start` set and `start_now`
false.

**Start** consumes the components, branching per item on lot tracking, and
stamps `started_at`.

**Finish** takes `quantity_good` and `quantity_scrapped`, adds only the good
units to stock, and stamps `finished_at`. Scrap needs no stock movement: the
components left at start, so scrapped units simply never arrive.

**Cancel** from `scheduled` moves nothing, because nothing was consumed.
From `in_progress` it returns every consumed component, restoring
lot-tracked stock to the exact lots it was drawn from. It is the escape
hatch for a mis-keyed job.

## 3. Holding, consuming and producing stock

### Reservations

A scheduled job holds its components. Reserved quantity per item and
location is the outstanding requirement across `scheduled` jobs:
`sum(quantity_required - quantity_consumed)`. It is computed from
`WorkOrderComponent` on demand, not stored, because a stored counter is a
second source of truth that can drift from the rows it summarises.

Starting a job checks each component against what is actually free:

```
available = on_hand - reserved by OTHER scheduled jobs at this location
```

If that is short, the start is refused with a 409 naming the job holding the
stock, so the answer is "WO-7 has them" rather than a bare shortage. The
same figure drives whether the grid's Make button is enabled, so a job the
user cannot start is not offered.

This is deliberately an execution-time guard only. It must **not** be
subtracted from `on_hand` in the planning engine: section 4 already counts a
scheduled job's components as demand, and netting the same reservation twice
would tell the shop to buy components it has already got.

Blocking is genuinely what the shop wants here. Two scheduled jobs each
needing 100 of 150 on hand means one of them cannot run, and saying so when
the second is started beats discovering it halfway through.

Under concurrency the check is advisory: two simultaneous starts can both
read the same free quantity and pass. The physical consumption below is
still guarded, so one of them fails there instead, with a shortfall message
rather than a reservation message.

### Stock paths

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
`quantity_planned` across `scheduled` and `in_progress` jobs, per item.

A scheduled job needs one more thing, and getting it wrong is the trap in
this design. If a scheduled job counted as supply and nothing else, it would
zero out the finished good's net requirement, `_explode_and_net` would never
explode its components, and the grid would read "all in stock" for a job
that cannot actually run. So a scheduled job also contributes **its stored
component requirements as demand**:

- `scheduled`: supply of the finished good, plus demand for its components.
- `in_progress`: supply of the finished good only, because its components
  have physically left stock and `on_hand` already reflects them.

**The schedule date bounds this.** A scheduled job counts only when its
`scheduled_start` falls on or before the horizon end; a job scheduled for
next quarter is not this quarter's plan and neither its supply nor its
component demand is counted. Both move together, always: counting the supply
without the component demand would under-order components, and the reverse
would over-order them.

There is deliberately no lower bound, matching the rule Phase 2 settled for
late sales orders. A job that should have started last week is still going
to happen, so it keeps counting rather than vanishing from the plan on the
morning it slips. A job with no `scheduled_start` counts as immediate.

An `in_progress` job is unbounded by date: it is running now, whatever its
schedule said.

The component figures come from `WorkOrderComponent`, not from re-exploding
the BOM, so a job always plans against the recipe it was raised on.

Worked through, this is neutral. A forecast for 50 pumps with a scheduled
job for 50 gives pump gross 50 less supply 50, so net 0 and no explosion,
plus 100 seals of component demand from the job itself: the same 100 seals
the explosion would have produced. With no forecast at all, the job still
raises demand for its own 100 seals, which is what the shop actually needs.

`MrpPlanLine` carries `in_production` as its own field rather than folding
it into `on_order`, following the note already in that file about keeping
supply sources separately visible.

## 5. Screens

**Planning grid.** Each product row gains a Make action, which raises and
starts a job for the quantity the grid is already recommending. It is
disabled when materials are short, because starting would only 409. The row
shows what is in production, in the same quiet secondary style as Phase 2's
committed line, and says when that quantity is scheduled rather than
running.

**Production page.** Lists scheduled, running and recent jobs with number,
product, planned quantity, location, status and timing, and is where a job
is started, finished or cancelled. A scheduled job whose `scheduled_start`
has passed carries a Late badge, the same treatment and wording Phase 2 gave
an overdue sales order, so the app has one vocabulary for "this should have
happened by now". It mirrors the Sales and Procurement
pages, so the app needs no third set of conventions. Raising a job for a
later date is done here, where the date and location inputs live, rather
than adding either to the grid row.

Finish opens a confirmation carrying the good and scrap inputs, since it
moves stock and cannot be undone. Cancel likewise says whether components
will go back.

## 6. Feeding plant analytics

`PaceSession` gains a nullable `work_order_id`. A session can be started
from a job, in which case it inherits that job's product; sessions started
the existing free-text way are unchanged and the column stays null, so
nothing already shipped breaks.

On stop, `stop_session` appends a `product` column to the materialised CSV
when the session is linked, exactly the way it already appends `target_rate`
conditionally. That carries the product into the `Dataset` the OEE engine
reads, so OEE, downtime and throughput can group by what was actually being
made without any change to the engine itself.

This is the whole analytics scope for this phase. Migrating the existing
free-text `line` and `operator` fields to real work-center and user
references is a separate piece of work.

## 7. Roles

Raising, starting, finishing and cancelling a job is owner or manager,
consistent with Phase 2 treating order entry as a shop-floor supervisor
action. Reading is any member. Gating is enforced in the backend and
mirrored in the frontend via `useCurrentUser()`, following `Planning.tsx`.

Feature key `production`. The migration that ships it must append the key to
every org already holding entitlements, or the feature arrives 403-ing for
every existing customer;
`backend/tests/test_feature_migration_coverage.py` enforces this.

## 8. Error handling

House voice: lowercase, terse, no trailing period, concrete numbers or SKU
interpolated. 400 for malformed input, 409 for state or stock conflicts.

- Components held by another scheduled job: 409 naming the job that holds
  them, so the user knows which job to cancel or start first.
- Components short at start: 409 naming the shortfall **and the location**.
  The location is added in the shared inventory and lot services rather than
  wrapped at this caller, so every existing caller's message improves too;
  the code lookup happens only on the error branch.
- Item has no active BOM: 400, naming the SKU. A thing with no recipe
  cannot be built.
- Finishing a lot-tracked product with no `lot_number`: 400.
- Negative good or scrap: 400. Quantities beyond `Numeric(12, 4)` range:
  400, matching the bound the planning grid write and sales order lines use,
  because Postgres would otherwise raise a numeric overflow as a 500.
- **Over-run guard.** Good plus scrap above twice the planned quantity is
  refused with 400, naming the planned figure and suggesting a second job.
  Over-runs are real and are allowed up to that point; 5000 against a
  planned 50 is a typo, and silently stocking it corrupts inventory in a way
  that is tedious to unpick. The bound is a typo guard, not a business rule,
  which is why it is a fixed multiple and not configurable.
- Starting, finishing or cancelling a job in the wrong state: 409.
- The job row is locked `for_update` on start, finish and cancel, so two
  concurrent calls cannot both pass the status check.

## 9. Testing

- Raising a job writes component requirements from the active BOM,
  including each BOM line's `scrap_pct`, and moves no stock.
- Start consumes exactly those stored requirements, on both the lot-tracked
  and non-lot-tracked branch.
- A job short on its **third** component rolls back the first two. A
  single-component shortfall proves nothing, because there is nothing to
  roll back; this is the specific gap Phase 2's review found.
- The shortfall message names the location.
- Finish adds only good units to stock, and scrap is recorded without
  moving stock.
- Over-run at exactly twice planned is accepted; above it is refused.
- Cancel from `scheduled` moves no stock. Cancel from `in_progress`
  restores the **exact lot split**, not merely the right total.
- A `scheduled` job nets as supply *and* raises demand for its components,
  so a scheduled job with no stock leaves the grid asking for its
  components rather than reading "all in stock".
- A scheduled job holds its components: with 150 on hand and two scheduled
  jobs needing 100 each, the second is refused at start, and the message
  names the job holding the stock.
- Starting the holding job first, then the second, still refuses the
  second, now as a plain shortfall.
- Cancelling the holding job releases the stock and the second job starts.
- A job's own reservation never blocks itself.
- Reservations do not reach the planning engine: a scheduled job with
  exactly enough components on hand leaves a net requirement of zero for
  those components, not a request to buy them again.
- A job scheduled beyond the horizon end contributes neither supply nor
  component demand; moving its date inside the horizon makes both appear
  together.
- A scheduled job whose date has passed still counts, and reports itself as
  late to start.
- An `in_progress` job nets as supply but does **not** re-raise component
  demand, since those components are already consumed.
- `done` and `cancelled` jobs count for nothing.
- A job raised against a BOM that is later edited still starts against the
  recipe it was raised on.
- A pace session linked to a job carries a `product` column into its
  dataset; an unlinked session's dataset is byte-for-byte what it is today.
- Org scoping: cross-org access 404s. Role gates: a member can read but
  cannot raise, start, finish or cancel.
- The `production` feature key is backfilled onto existing orgs.

## Known limitations

- **The reservation check is advisory under concurrency.** Two starts racing
  can both read the same free quantity and pass it; the physical consumption
  still guards, so one fails there with a shortfall message instead of a
  reservation one. Closing this properly means locking every component row
  for the duration of a start, which costs more in contention than the race
  costs in confusion.
- **Nothing chases a late job.** A scheduled job past its date says so
  wherever it is displayed, but no notification goes out and nothing
  escalates; a job scheduled and forgotten stays quietly late, holding its
  components, until someone looks at the Production page.
