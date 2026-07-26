# Planning Action Center (Phase 1 of the MRP overhaul)

Date: 2026-07-26
Status: approved design, pending implementation plan

## Context

The MRP feature works but feels disjointed: demand input, the plan table, PO
generation, the build calculator, and stock health live on different pages
with no spine connecting them. Competitors (Katana, MRPeasy) sell a coherent
story; ours is smeared across ten screens. The product's positioning is
"manufacturing intelligence": the app should tell you what to do, not hand
you tables to read.

Phase 1 gives the MRP module that spine without touching the planning engine
or data model. Phases 2 (sales orders) and 3 (work orders) are out of scope
and tracked in the roadmap conversation, not this spec.

## Goals

- One screen that answers "what should I do today", demoable in 2 minutes.
- Kill the disjointed feel: clear separation of input (demand), output
  (plan), and action (Today tab).
- Reuse the existing engine, endpoints, and UI patterns. No new data model.

## Non-goals

- No sales orders, work orders, dismiss/snooze persistence, or stock-health
  changes. The Action Center list is recomputed truth, not a todo app.
- No changes to the MRP engine (`backend/app/services/mrp.py`).

## 1. Information architecture

`/planning` becomes a tab hub like Procurement (`PageTabs` pattern), with
three tabs:

- **Today** (default): the new Action Center. This is the demo screen.
- **Demand**: everything that is input: forecast form + table, CSV import,
  suggest-from-history. Moved unchanged from the current page.
- **Plan**: the full MRP table with horizon controls and send-to-report, for
  power users who want the gross/net breakdown. Also moved unchanged.

Nothing else moves. Build Calculator and Generate from Plan stay in
Procurement; stock health stays in Warehouse. The Action Center links into
them instead of duplicating them.

## 2. The Action Center ("Today" tab)

One ranked list answering "what should I do": each MRP plan line with
`suggested_order_quantity > 0` becomes an action row:

> 🔴 **Order 400 × ALU-6061** — order by **Jul 22 (overdue)**, need by
> Aug 12 · on hand 120, 3 days of supply
>
> 🟡 **Build 50 × PUMP-A2** — need by Aug 5 · covers forecast + safety stock

Rows reuse the Morning Brief card language from `Dashboard.tsx` (colored
left border by urgency, status badge, one-line detail), not a new pattern.

A summary strip on top: **N overdue, N due this week, N scheduled**. That is
the 2-minute demo opener.

### Ranking

A pure TypeScript function (`frontend/src/pages/planning/rank.ts`), unit
tested, no I/O:

- Effective date per row: buy rows use `suggested_order_date`, falling back
  to `need_by_date` when the item has no lead time on file; make rows use
  `need_by_date` (manufactured lines never get a `suggested_order_date`).
- Tiers: **overdue** (effective date before today), **due this week**
  (within 7 days), **scheduled** (later or undated).
- Inventory-analytics elevation (client-side join on `item_id` against
  `/inventory/analytics`): status `stockout` forces the overdue tier;
  status `low` forces at least due-this-week. A stockout shows red
  regardless of dates.
- Within a tier: by effective date ascending; undated rows last, sorted by
  `net_requirement` descending.

### Actions per row

- **Buy rows**: checkbox selection, then one **"Draft POs for selected"**
  button that reuses the generate-POs backend (grouped by preferred
  supplier). The inline result shows the created draft POs (linking to
  Procurement) and any items with no preferred supplier, with a nudge to
  assign one under Items. The existing endpoint takes only a horizon, so
  extending it with an item filter is in scope (see section 3).
- **Make rows**: a **"Plan build"** link to Procurement's Build Calculator
  pre-seeded with the item and quantity via query params, same pattern as
  the existing `?tab=generate` deep link. In Phase 3 this becomes "create
  work order".
- **Empty state**: "Nothing to do — demand is covered through
  {horizon_end}." That state sells too.

### Role gating

Both PO-generation endpoints require the owner role. The list is visible to
everyone; the "Draft POs for selected" button renders only for owners
(role from `/orgs/me`, same pattern as `Warehouse.tsx` approvals).

No dismiss/snooze persistence: drafting a PO already removes the line
naturally, because `on_draft` offsets `net_requirement` in the engine, which
also makes a second click safe (no double-ordering).

## 3. Data flow and backend

The Today tab composes two existing endpoints client-side:

- `GET /mrp/plan?horizon_start=&horizon_end=` (default horizon today + 90
  days, matching the engine default)
- `GET /inventory/analytics` (status, days_of_supply per item)

No new backend model, no new endpoint. **One backend change**: extend
`GeneratePosRequest` (`backend/app/api/routes/forecasts.py`) with an
optional `item_ids: list[str] | None`. When provided, `generate_pos`
restricts the orderable shortfall lines to those items before grouping by
preferred supplier; when omitted, behavior is unchanged. Quantities are
still recomputed server-side from the plan (never trusted from the client),
matching the existing endpoints.

## 4. Changes to existing surfaces

- Current `Planning.tsx` is split into three tab panels under
  `frontend/src/pages/planning/` (mostly cut-and-paste); `Planning.tsx`
  becomes the hub, mirroring `Procurement.tsx`.
- `BuildCalculatorPanel` accepts an optional initial item and quantity
  (query params through Procurement, like `GeneratePosPanel`'s horizon).
- Dashboard's Morning Brief: item rows retarget from `/warehouse` to
  `/planning`, and the header gains an "Open Action Center" link so the two
  "tell me what to do" surfaces point at each other.
- Nav entry stays `/planning`, label stays "Planning"; keywords gain
  "action center", "what to do", "shortages".

## 5. Testing

- Unit test for the ranking/enrichment function: overdue ordering, stockout
  elevation, low elevation, buy/make date fallback, undated-last ordering.
- Render test for the Today tab following the existing
  `ProductionSetup.render.test.tsx` pattern (summary strip, a buy row, a
  make row, empty state).
- Backend test for the `item_ids` filter on generate-POs: filtered subset
  ordered, omitted field keeps current behavior.
