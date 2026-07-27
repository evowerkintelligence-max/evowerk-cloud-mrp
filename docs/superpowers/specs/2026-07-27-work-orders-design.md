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
- Surface jobs that have gone quiet. A job nobody finishes is worse than a
  job nobody starts, because its components are already gone, so both are
  chased where the shop already looks each morning.
- Survive ordinary mistakes: a job can be corrected rather than only
  cancelled and retyped.
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
- **No hand-ranked job queue.** Contested stock is settled by need date
  (section 3), not by a priority number someone maintains. Re-prioritising
  is done by changing a job's date, which is a thing the shop already
  understands, rather than by a second ordering concept that can disagree
  with the dates.
- **No work-order-to-sales-order link.** The grid already nets demand from
  open sales orders, so making to plan serves those orders implicitly.
  Adding an explicit link would add a visible concept for no gain today.
- **No incremental booking.** Units are not added to stock as they come off
  the line; a job is finished once. Finishing for less than planned is fully
  supported and accounted for (section 2), which is the case that actually
  matters; watching a job tick up in real time is an operations feature, not
  an inventory one.
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
- `scrap_reason`: nullable free text captured at finish. A scrap count with
  no cause tells the shop its yield is poor without telling it why, which is
  the question the number always prompts next.
- `remainder_disposition`: nullable, `not_started` | `consumed`. What
  happened to the components of units never attempted, recorded so an
  under-run that returned stock to the shelf can be explained a month later.
  Null on a job that met or exceeded its planned quantity.
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

**Edit**, while still `scheduled`, changes quantity, `scheduled_start`,
location or notes, and recomputes the component requirements and therefore
the reservation. Without it, correcting a job raised for 50 that should have
been 100 means cancelling and retyping it, which is the single most common
thing a shop will want to do to a job and the fastest way to make the app
feel hostile.

Editing re-snapshots `bom_id` to the item's currently active recipe, so a
job always reflects the recipe as of its last edit. That is the rule that
explains itself: if the BOM was wrong and has been fixed, editing the job
picks the fix up, and a job nobody touches keeps the recipe it was raised
on. Editing is refused once a job has started, because its components are
already consumed.

**Start** consumes the components, branching per item on lot tracking, and
stamps `started_at`.

**Finish** takes `quantity_good` and `quantity_scrapped`, adds only the good
units to stock, and stamps `finished_at`. Scrap needs no stock movement: the
components left at start, so scrapped units simply never arrive.

**Under-run is accounted for, not waved through.** When good plus scrap
comes to less than planned, some units were never attempted, and their
components were consumed at start but may well still be on the shelf. The
app cannot tell which, and both wrong answers are bad: assume the material
is still there and the books invent stock the shop does not have; assume it
is gone and the shop is told to buy what is sitting in front of it. So
finish asks, and the answer is required rather than defaulted:

- `not_started`: the remainder's components are untouched and are returned
  to stock, restoring lot-tracked quantities to the lots they came from.
- `consumed`: the material was used or spoiled getting as far as the job
  got, and stays consumed.

Returned quantities are the remainder's share of each component's
`quantity_required`, and the disposition is stored on the job so the
decision is auditable later. The question is only asked on an under-run,
so the ordinary finish stays two numbers and a button.

**Cancel** from `scheduled` moves nothing, because nothing was consumed.
From `in_progress` it returns every consumed component, restoring
lot-tracked stock to the exact lots it was drawn from.

Cancel on a running job is exactly `finish(good 0, scrap 0,
remainder not_started)`: nothing was made and every component is back on the
shelf. It is kept as its own action because that is a real and common
outcome worth one click, but it is defined in terms of finish rather than
being a second path that moves stock its own way, and the confirmation says
in as many words that it means nothing was made and all materials are back.
A job where any work actually happened is finished instead, where the
under-run question above is asked properly.

## 3. Holding, consuming and producing stock

### Reservations

A scheduled job holds its components. Reserved quantity per item and
location is the outstanding requirement across `scheduled` jobs:
`sum(quantity_required - quantity_consumed)`. It is computed from
`WorkOrderComponent` on demand, not stored, because a stored counter is a
second source of truth that can drift from the rows it summarises.

**Only jobs inside the planning horizon hold stock**, the same bound section
4 applies to the plan. A job scheduled for next quarter is intent, not a
claim on today's shelf; letting it block a job that needs to run this week
would be the worst of both worlds, since that far-future job is not even in
the plan the shop is looking at. A job with no `scheduled_start` holds
immediately.

**The more urgent job wins the material.** Claims are ranked by
`scheduled_start`, earliest first, with the lower `wo_number` breaking ties
so the ordering is total and stable. Starting a job counts only the claims
that outrank it:

```
available = on_hand - reserved by scheduled jobs that outrank this one
```

So a job due Friday cannot be blocked by one due next month, and the job the
shop needs soonest gets the seals. Whoever happens to click first decides
nothing, which is the property that makes this a priority rule rather than a
race.

Re-prioritising is changing a job's date, using the edit path in section 2.
That is deliberately the only lever: a separate priority number would be a
second ordering concept free to disagree with the dates, and when the two
disagree nobody can say which the plan followed.

If a start is short, it is refused with a 409 naming the job holding the
stock and when that job is due, so the answer is "WO-7 needs them on
Friday" rather than a bare shortage. The same figure drives whether the
grid's Make button is enabled, so a job the user cannot start is not
offered.

This is deliberately an execution-time guard only. It must **not** be
subtracted from `on_hand` in the planning engine: section 4 already counts a
scheduled job's components as demand, and netting the same reservation twice
would tell the shop to buy components it has already got.

Blocking is genuinely what the shop wants here. Two scheduled jobs each
needing 100 of 150 on hand means one of them cannot run, and saying so when
the second is started beats discovering it halfway through.

**The check is made atomic by locking the stock it is about to read.**
Before computing availability, a start takes a row lock on each component's
stock: the `InventoryBalance` row for a non-lot component, and the candidate
`Lot` rows for a lot-tracked one. A second start touching any of the same
components blocks until the first commits, then recomputes against what is
genuinely left, so the loser gets the reservation message it deserves rather
than a confusing shortfall further in. Jobs with no components in common
never contend, which is the right place for the cost to fall.

Locks are taken **in a deterministic order**, by item id and then lot id.
This is not incidental: two jobs sharing two components and locking them in
opposite orders would deadlock, and that is a genuinely unpleasant failure
to diagnose in production. The ordering requirement is stated here because
the test suite cannot enforce it: the suite runs on SQLite, where
`FOR UPDATE` is a no-op, so this is guaranteed by the query and by review
rather than by a concurrency test.

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

A scheduled job is editable in place here: quantity, date, location and
notes, which is the correction path described in section 2.

Finish opens a confirmation carrying the good, scrap and scrap-reason
inputs, since it moves stock and cannot be undone. Cancel's confirmation
states plainly that the components go back as though the job never ran, and
points at finishing with scrap instead if any work was actually done.

The grid's Make button offers the quantity still needed **after** jobs
already in flight, so pressing it twice raises a second job for what is left
rather than silently duplicating the first. When nothing is left to make,
the row says a job is already covering it instead of offering Make again.

## 6. Chasing jobs, and feeding plant analytics

### Jobs that have gone quiet

A flag on a page nobody opens is not chasing anything. Two failure modes
need to reach the user without being looked for:

- **Late to start.** A `scheduled` job whose `scheduled_start` has passed.
  It is holding components while not making anything.
- **Stalled.** An `in_progress` job running far longer than a job like it
  should. This is the worse of the two and the one a shop will actually hit:
  the components are already consumed, and the job counts as incoming
  supply, so the plan is quietly promising 50 pumps that nobody is making.

Both surface through `app/services/morning_brief.py`, which is already the
app's "what needs a human today" list and is computed on request, so this
needs no scheduler and no new delivery mechanism. The planning grid's
summary bar carries the count alongside its existing material warnings, so
the condition is visible on the screen the shop opens first.

**"Far longer" is measured, not assumed.** A fixed number of days is wrong
for any shop it was not chosen for, so the expected duration of a job comes
from the best evidence available, in order:

1. **Its routing.** Sum over the routing's steps of
   `setup_time_seconds + cycle_time_seconds x quantity_planned`. This is the
   shop's own stated intent for how long the work takes, and the data is
   already there.
2. **Its own history.** Failing a routing with usable times, the median
   duration per unit of the last `DURATION_SAMPLE` (5) completed jobs for
   the same item, scaled to this job's quantity. The median rather than the
   mean so one job left open over a holiday does not move the bar.
3. **A cold-start constant**, `DEFAULT_JOB_DAYS` (7), used only by a shop
   with neither routing times nor a single completed job of that item. It is
   what the app knows on day one, and it stops being used as soon as the
   shop has told it anything better.

A job is stalled once elapsed time exceeds
`max(2 x expected, expected + 1 day)`. The additive term is the same lesson
as the over-run guard's `+ 10`: doubling alone is far too tight at small
values, and would call a two-hour job stalled after four hours.

Per item, not per org, because a shop making both a five-minute bracket and
a three-week assembly has no single sensible number, which is precisely the
problem a constant cannot solve.

### Feeding plant analytics

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

- Components held by a job that outranks this one: 409 naming that job and
  its due date, so the user knows which job to move, cancel or start first.
- Finishing below the planned quantity without a `remainder_disposition`:
  400. It is required precisely because neither possible default is safe.
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
- **Over-run guard.** Good plus scrap above `max(2 x planned, planned + 10)`
  is refused with 400, naming the planned figure and suggesting a second
  job. Over-runs are real and are allowed up to that point; 5000 against a
  planned 50 is a typo, and silently stocking it corrupts inventory in a way
  that is tedious to unpick. The `+ 10` term matters more than it looks: a
  plain doubling refuses a job planned for 1 that yielded 3, which is
  routine on a prototype or small-batch line and would make the guard feel
  broken to exactly the shops least able to work around it. The bound is a
  typo guard, not a business rule, which is why it is fixed rather than
  configurable.
- **Duplicate finished lot number.** Finishing into a lot number the item
  already has adds to that lot via the existing `find_or_create_lot`, rather
  than erroring or creating a second lot with the same name. That is already
  how a purchase order receipt behaves, and a shop finishing a second batch
  into the same physical lot is doing something ordinary, not something
  wrong.
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
- A job scheduled beyond the horizon holds no stock, so a job needed this
  week can still start.
- Editing a scheduled job's quantity recomputes its components and its
  reservation, and picks up a BOM that changed since it was raised. Editing
  a started job is refused.
- Over-run: a job planned for 1 accepts 3 good, and a job planned for 50
  refuses 5000.
- Finishing into an existing lot number adds to that lot rather than
  creating a second lot with the same name.
- A scheduled job past its date appears in the morning brief.
- Expected duration comes from the routing when it has usable times, from
  the median of completed jobs for that item when it does not, and from the
  constant only when there is neither. A job inside its expected duration is
  not flagged; the same job well past it is.
- The history median ignores an outlier: four one-hour jobs and one job left
  open for a week still expect roughly an hour.
- Two items with very different durations are judged separately, so a job on
  the slow one is not flagged while a job on the fast one is.
- Under-run: finishing 30 good and 2 scrap against a planned 50 with
  `not_started` returns the 18 units' components, restoring lot quantities
  to their original lots; with `consumed` it returns nothing. Omitting the
  disposition is refused.
- Finishing at or above the planned quantity needs no disposition.
- Cancelling a running job leaves stock exactly as it was before the start.
- A job due sooner starts even though a job due later holds the stock, and
  the later job is then refused with a message naming the earlier one.
- Moving a job's date earlier moves it ahead in the queue, so re-prioritising
  by date actually changes who can start.
- A job never blocks itself, and ties between two jobs due the same day are
  broken by `wo_number` rather than being non-deterministic.
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

These are consequences of decisions taken above, not gaps left unexamined.

- **Chasing is in-app, not push.** Late and stalled jobs reach the morning
  brief and the grid summary, which is where this shop's users already look,
  but nothing emails or notifies anyone. Push needs a scheduler the app does
  not have, and adding one to deliver two lines of text would be a poor
  trade.
- **A brand new shop gets the cold-start constant.** With no routing times
  and no completed jobs, stalled detection falls back to seven days until
  the first job of an item completes. It self-corrects with use, but the
  first week is a guess.
- **Under-run returns components proportionally.** A job that made half its
  units returns half of each remaining component, which is right for material
  cut per unit and wrong for a setup piece consumed whole at the start of
  the run. Getting that exact needs per-component consumption points, which
  is operation tracking.
- **The queue is ordered by date alone.** Two jobs due the same day are
  settled by which was raised first, which is stable and explainable but has
  nothing to do with which matters more. A shop that needs to say "this one
  first regardless" is asking for scheduling.
