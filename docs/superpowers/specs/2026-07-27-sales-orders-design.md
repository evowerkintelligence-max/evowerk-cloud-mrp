# Sales Orders: committed demand for the planning grid (Phase 2)

Date: 2026-07-27
Status: approved design, pending implementation plan
Follows: `docs/superpowers/specs/2026-07-26-planning-grid-design.md` (Phase 1, shipped)

## Context

Phase 1 replaced the multi-screen MRP with one planning grid. Its only
demand source is a number a user types, so the app cannot know a customer
order arrived until a human retypes it. Phase 2 makes real orders a demand
source, so the grid turns red because a commitment is at risk rather than
because an estimate is.

The app has no customer or sales-order concept today. The buy side
(`Supplier` -> `PurchaseOrder` -> `PurchaseOrderLine`) is the mirror to
follow: the sell side should look and behave like its twin so neither the
code nor the UI needs a second set of conventions.

## Goals

- Real customer orders drive the planning grid, the build calculator, and
  generated purchase orders, all from one place.
- Raising an order takes seconds and needs no training.
- The sell side mirrors the buy side closely enough that anyone who knows
  Procurement already knows Sales.

## Non-goals

Deliberately excluded. Each is a separate decision the user has not asked
for, and several compete with the accounting system a customer already runs:

- Prices, invoices, tax, credit terms, currency.
- Partial shipments, delivery addresses, carriers, tracking.
- Returns or credit notes.
- A customer-facing portal.
- Allocating specific stock to specific orders. See "Known limitation".

## 1. Data model

Three tables, each mirroring its buy-side twin in shape, naming and
constraints. All carry `org_id` and are org-scoped like every other model.

**Customer** mirrors `Supplier`: name, contact fields, `is_active`.

**SalesOrder** mirrors `PurchaseOrder`:
- `customer_id`
- `so_number`, a server-computed integer (`max + 1` per org) assigned at
  creation, never client-supplied, never stored pre-formatted. Same
  read-then-insert retry guard `create_purchase_order` uses, since the same
  race exists.
- `status`: `open` | `done` | `cancelled`. Only `open` counts as demand.
- `needed_by`, a date. This is what drives planning dates.
- `created_by`, timestamps.

**SalesOrderLine** mirrors `PurchaseOrderLine`:
- `so_id`, `item_id`, `quantity_ordered`, `quantity_delivered`, `sequence`.
- No `unit_price`. Money is out of scope, and adding a nullable price column
  now would invite exactly the semantics (tax-inclusive or not, discounts)
  that `PurchaseOrderLine.unit_price` documents as unreconstructable after
  the fact.

**Org** gains one setting, alongside the existing `adjust_approval_threshold`
precedent: how committed demand combines with estimated demand. Stored as a
short string rather than a boolean so a third policy can be added without a
migration. Values: `consume` (default) and `add`.

## 2. Demand combination

This is the load-bearing change and it belongs in the engine, not the grid.

`compute_mrp_plan` (`backend/app/services/mrp.py`) currently seeds
independent demand from `DemandForecast` rows alone. It will seed from both
forecasts and open sales order lines, combined per item per date by a pure
function so the build calculator, generated purchase orders, and the grid can
never disagree about how much is needed.

Combination happens **per item across the planning window**, not bucket by
bucket. That matches how the engine already works: `compute_mrp_plan` sums
every forecast bucket inside the horizon into one figure per item before
netting. Comparing an estimate to committed demand week by week would be
more precise, but it would also mean two different summing rules in one
engine, and the window is only 90 days. The date used for planning stays the
earliest date among the contributing rows, exactly as it is today.

The combination, per item:

- `consume` (default): committed demand counts towards estimated demand
  rather than adding to it, so the planned figure is the larger of the two.
  An estimate of 100 with orders for 60 plans 100; orders for 130 plan 130.
  This is standard forecast-consumption practice and prevents buying
  materials for demand that will not materialise twice.
- `add`: the two are summed. Correct for a shop whose estimate deliberately
  excludes booked work.

The function takes the two demand maps and the policy and returns one map. It
has no database access so it is unit tested directly.

An open sales order contributes its outstanding quantity
(`quantity_ordered` minus `quantity_delivered`) at the order's `needed_by`
date. Orders that are `done` or `cancelled` contribute nothing.

Because partial shipments are out of scope, `quantity_delivered` is only
ever 0 or the full ordered quantity, so today the subtraction is a no-op on
every open order. The column still earns its place: it mirrors
`PurchaseOrderLine.quantity_received`, it records what was actually
delivered rather than inferring it from status, and it is what a later
partial-shipment feature would build on. Writing the subtraction now means
that feature does not have to revisit the demand maths.

## 3. Screens

**New: Sales**, a tab hub with the same structure as `Procurement.tsx`.

- **Orders**: a list (number, customer, product, quantity, needed by,
  status), a compact form to raise one, and a "Mark done" action per open
  order.
- **Customers**: list plus form, mirroring the Suppliers tab.

Nav gains one entry, gated by a `sales` feature key like its neighbours.

**Changed: the planning grid.** The Planned cell gains one read-only line
beneath the existing input showing committed demand, for example
`40 ordered`. A product with no open orders renders exactly as it does
today, so the screen is unchanged for anyone not using orders. Wording on a
row whose committed demand is at risk is stronger than one where only an
estimate is at risk, because missing a real order is a different problem.

**Changed: settings.** One plain-language question, defaulting to the
recommended answer: "When real orders come in, do they count towards your
plan, or on top of it?" No MRP vocabulary, and specifically not the words
"forecast consumption".

## 4. Marking an order done

One action, one transaction: the order flips to `done`, its lines'
`quantity_delivered` is set to the ordered quantity, and the finished goods
leave stock through the existing inventory consume path rather than a second
mechanism.

- Insufficient stock: refused with a plain message naming the shortfall.
  Inventory is never driven negative.
- Already done or cancelled: refused. The status check and the stock movement
  share one transaction, so a double submit cannot consume stock twice.
- Lot-tracked items consume through the same path the Warehouse screen uses,
  so lot semantics are inherited rather than reimplemented.

Cancelling an open order sets `cancelled` and moves no stock.

## 5. Roles

Matching the buy side and the Phase 1 decisions:

- Raising, editing, cancelling and completing orders: owner or manager.
  These are shop-floor supervisor actions, the same reasoning that made the
  planning grid write owner-or-manager.
- Creating and editing customers: owner or manager.
- Reading orders and customers: any member.
- Changing the demand-combination setting: owner only. It changes what every
  planning number in the app means.

## 6. Error handling

- Deleting or deactivating a customer with open orders is blocked rather than
  orphaning them.
- An order line for an item with no BOM is valid; it plans as something to
  buy rather than build.
- An order whose `needed_by` falls *after* the grid's fixed 90-day window
  still exists but does not count yet; it slides into the plan as the window
  reaches it, the same way a future-dated forecast does.
- An order whose `needed_by` has *passed* still counts, in full. This is
  where the sell side deliberately departs from the forecast side: a
  forecast bucket in the past is spent, but an open order in the past is not
  spent, it is late, and the goods are still owed. Dropping it would turn
  the grid green for the most at-risk commitment in the shop. Lateness is
  surfaced rather than silently folded in: `is_overdue` on the order, a
  "Late" badge on the Sales list, and `committed_overdue` driving
  "N ordered, late" on the planning row.

  *Amended 2026-07-27, after implementation.* The original wording of these
  two bullets was a single sentence claiming an out-of-window order "still
  counts in the engine ... mirrors how out-of-window forecasts already
  behave", which is self-contradictory: out-of-window forecasts do not
  count. Code review caught it; the split above is the settled answer.
- Quantities are validated at the trust boundary the same way the planning
  grid write validates them: positive, and within the `Numeric(12, 4)`
  column's range, rejected with a plain message rather than a 500.

## 7. Testing

Backend:
- The combination function as a direct unit test: both policies, orders below
  and above the estimate, the boundary where they are equal, empty on either
  side.
- Engine integration: an open order alone produces a plan; a cancelled or
  done order does not; the two policies produce different net requirements
  for the same data.
- Order lifecycle: creation assigns sequential numbers per org, completion
  moves stock, completion refuses when short, completion refuses twice,
  cancel moves no stock.
- Org isolation on every new endpoint.
- Role gating, including that a manager can complete an order and a plain
  member cannot.

Frontend:
- Render tests for both Sales tabs, following
  `ProductionSetup.render.test.tsx`.
- A render test for the grid's committed-demand line, including that a
  product with no orders renders unchanged.

## Known limitation

Orders are demand, netted against total stock exactly as forecasts are.
The grid therefore answers "can I cover everything I have committed to",
not "which order gets the stock when I cannot". Per-order allocation is a
materially larger feature and is deliberately deferred until asked for.
