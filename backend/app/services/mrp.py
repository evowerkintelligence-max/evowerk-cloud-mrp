from __future__ import annotations

import uuid
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Bom, BomLine, Item
from app.models.inventory import InventoryBalance
from app.models.lot import Lot
from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.services.demand import (
    combine_demand,
    earliest_dates,
    load_committed_demand,
    load_demand_policy,
    load_forecast_demand,
)

# Open-PO supply comes only from POs a supplier has actually been told to ship.
# A draft PO is still being edited (lines can still change or it can be
# cancelled outright), so counting it as on-order would let a never-submitted
# draft silently suppress a real replenishment suggestion.
OPEN_PO_STATUSES = ("submitted", "partially_received")


class MrpPlanLine(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    # True if this item is produced by an active BOM: its net requirement is a
    # *build* suggestion (make it), not a *buy* one (order it). The frontend
    # labels the two differently.
    is_manufactured: bool
    # Demand this item was forecast for directly (independent demand).
    independent_demand: Decimal
    # Demand exploded onto this item from parent assemblies' net requirements
    # (dependent demand). gross_demand = independent_demand + dependent_demand.
    dependent_demand: Decimal
    gross_demand: Decimal
    on_hand: Decimal
    on_order: Decimal
    # Outstanding quantity on open *draft* POs - planned but not yet committed
    # supply. Kept separate from on_order (submitted+) so the two stay visible,
    # but it offsets net_requirement the same way, so drafting an order removes
    # the item from the shortfall and a second generation can't double-order it.
    on_draft: Decimal
    net_requirement: Decimal
    # The stable "how much to order/build" field for API consumers and PO
    # generation. Equal to net_requirement, except for purchased items with a
    # supplier min_order_qty above the net requirement - those round up to the
    # MOQ. Kept distinct from net_requirement so BOM explosion (which must
    # stay on the true net - see compute_mrp_plan) never sees the rounded-up
    # figure.
    suggested_order_quantity: Decimal
    # Earliest date, within the horizon, that this item's demand must be
    # covered by - the min forecast period_start reaching it, either directly
    # (independent demand) or via a parent's own need date propagated down
    # through explosion. None if unreachable from any dated forecast (shouldn't
    # happen for a line that made it into the plan, but demand-driven design
    # elsewhere in this module treats "no source" as equally safe to omit).
    need_by_date: date | None
    # need_by_date offset back by the item's lead_time_days - when to actually
    # place the order so it lands in time. None if there's no lead time on
    # file (an unset lead time is a real "don't know" - see Item.lead_time_days
    # - not a same-day default) or the item is manufactured (a build action,
    # not a purchase, has no supplier lead time to offset by).
    suggested_order_date: date | None


async def _sum_by_item(db: AsyncSession, query) -> dict[uuid.UUID, Decimal]:
    result = await db.execute(query)
    return {item_id: (total or Decimal(0)) for item_id, total in result.all()}


async def load_bom_children(
    db: AsyncSession, org_id: uuid.UUID
) -> dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]]:
    """Manufacturing graph over ACTIVE BOMs only: produced item_id -> list of
    (component_item_id, quantity_per_unit). Draft/archived BOMs are ignored -
    only the currently-active BOM defines how an item is really built, and
    activation already enforces one-active-per-item and acyclicity.

    quantity_per_unit already folds in the line's scrap_pct (the user's own
    failure leeway for that component) - qty * (1 + scrap_pct/100) - so every
    caller of this map explodes against the real, order-enough figure without
    having to know scrap exists.
    """
    rows = await db.execute(
        select(Bom.item_id, BomLine.component_item_id, BomLine.quantity, BomLine.scrap_pct)
        .join(BomLine, BomLine.bom_id == Bom.id)
        .where(Bom.org_id == org_id, Bom.status == "active")
    )
    children: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]] = defaultdict(list)
    for parent_id, child_id, qty, scrap_pct in rows.all():
        children[parent_id].append((child_id, qty * (1 + scrap_pct / 100)))
    return children


async def load_on_hand(db: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """Total on-hand per item: active lot quantities for lot-tracked items plus
    the general inventory_balances for everything else. The two are disjoint (an
    item is one or the other - see Item.is_lot_tracked), so summing them is just
    "whichever applies". on_hold lots are excluded: they can't be consumed, so
    counting them would understate a real shortage.
    """
    on_hand = await _sum_by_item(
        db,
        select(Lot.item_id, func.sum(Lot.quantity_remaining))
        .where(Lot.org_id == org_id, Lot.status == "active")
        .group_by(Lot.item_id),
    )
    inventory_on_hand = await _sum_by_item(
        db,
        select(InventoryBalance.item_id, func.sum(InventoryBalance.quantity_on_hand))
        .where(InventoryBalance.org_id == org_id)
        .group_by(InventoryBalance.item_id),
    )
    for item_id, qty in inventory_on_hand.items():
        on_hand[item_id] = on_hand.get(item_id, Decimal(0)) + qty
    return on_hand


def resolve_horizon(horizon_start: date | None, horizon_end: date | None) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    hs = horizon_start if horizon_start is not None else today
    he = horizon_end if horizon_end is not None else today + timedelta(days=90)
    if he < hs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_end must not be before horizon_start")
    return hs, he


async def compute_mrp_plan(
    db: AsyncSession, org_id: uuid.UUID, horizon_start: date, horizon_end: date
) -> list[MrpPlanLine]:
    """Material Requirements Planning: net demand against supply, exploding
    forecasts through active BOMs so a forecast of a finished good becomes
    concrete requirements for the components it's built from.

        net_requirement = max(0, gross_demand - on_hand - on_order - on_draft)
        gross_demand    = independent_demand + dependent_demand

    - independent_demand: the item's own demand from both sources, combined
      under the org's demand_policy (`consume` by default, so the larger of the
      two rather than their sum): forecast buckets whose period_start is in
      [horizon_start, horizon_end], and outstanding quantity on open sales
      orders whose needed_by is in the same window. See services.demand.
    - dependent_demand: quantity_per x parent net_requirement, summed over every
      parent assembly that lists this item in its active BOM.
    - on_hand: for lot-tracked items, sum of *active* lot quantity_remaining
      (on_hold lots - QA hold/recall - are excluded; they can't be consumed, so
      counting them would understate a real shortage). For non-lot-tracked
      items, the general inventory_balances on-hand. The two are disjoint (an
      item is one or the other), so summing them is just "whichever applies".
    - on_order: open PO supply, quantity_ordered - quantity_received across PO
      lines on submitted/partially_received POs.
    - on_draft: outstanding quantity on open *draft* POs - planned but not yet
      committed supply. Offsets the requirement like on_order (so drafting an
      order removes the shortfall and a repeat generation can't double-order),
      but reported separately so submitted vs still-a-draft stays visible.

    The explosion/netting engine itself lives in _explode_and_net, seeded here
    from both demand sources - see that function for the full algorithm.
    Reused as-is (different seed) by services.mrp.compute_build_plan for the
    build calculator, which has no forecast rows to read.
    """
    policy = await load_demand_policy(db, org_id)
    forecast_qty, forecast_dates = await load_forecast_demand(db, org_id, horizon_start, horizon_end)
    committed_qty, committed_dates = await load_committed_demand(
        db, org_id, horizon_start, horizon_end
    )
    independent = combine_demand(forecast_qty, committed_qty, policy)
    # Gate on the COMBINED map, not the forecast alone: a shop with real orders
    # and no typed estimates has genuine demand and must get a real plan.
    if not independent:
        return []
    need_by = earliest_dates(forecast_dates, committed_dates)
    return await _explode_and_net(db, org_id, independent, need_by)


async def _explode_and_net(
    db: AsyncSession,
    org_id: uuid.UUID,
    seed_gross: dict[uuid.UUID, Decimal],
    seed_due: dict[uuid.UUID, date] | None,
) -> list[MrpPlanLine]:
    """The shared explosion/netting core behind compute_mrp_plan (seeded from
    forecasts) and compute_build_plan (seeded from a single build quantity).

    This is *netted* explosion, not gross: each assembly is netted against its
    own on-hand/on-order first, and only the resulting net requirement explodes
    to its components. Finished-good and sub-assembly stock therefore correctly
    reduces how much of each raw component you actually need to buy. Items are
    processed parents-before-components (topological order over the active-BOM
    DAG) so an assembly's net is final before it explodes downward.

    Demand-driven: items reached by neither `seed_gross` nor an explosion
    aren't planning actions, so they're omitted.

    Safety stock (item.safety_stock_qty) is added to the net requirement
    rather than subtracted from available stock - same arithmetic, but it says
    *why*: the buffer is demand you must satisfy on top of the forecast, so
    that a forecast miss or a late delivery eats the buffer instead of a
    stockout. None means "no buffer policy", treated as 0 here, which is
    byte-for-byte the pre-safety-stock formula - an org that never sets the
    field sees no plan change at all. The buffer deliberately flows through
    explosion untouched: an assembly's inflated net becomes its components'
    gross, which is the correct multi-level behavior (buffering the parent
    means actually having the parts to build the buffer). Components carry
    their own buffer on top of that if they set one.

    Lead-time offsetting and MOQ rounding are modelled on top of the netting
    above:
    - need_by_date: the earliest date (from seed_due) reaching this item,
      propagated down through explosion. None if seed_due is None/empty or
      this item is never reached by a dated seed (e.g. every build-calculator
      call, which has no dates at all).
    - suggested_order_date = need_by_date - item.lead_time_days, when both are
      known and the item is purchased (not manufactured).
    - suggested_order_quantity rounds net_requirement up to item.min_order_qty
      for purchased items when the MOQ is higher. Explosion always uses the
      raw net, never the MOQ-rounded figure.
    """
    if not seed_gross:
        return []
    need_by = dict(seed_due) if seed_due else {}

    on_hand = await load_on_hand(db, org_id)
    on_order = await _sum_by_item(
        db,
        select(
            PurchaseOrderLine.item_id,
            func.sum(PurchaseOrderLine.quantity_ordered - PurchaseOrderLine.quantity_received),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrder.org_id == org_id, PurchaseOrder.status.in_(OPEN_PO_STATUSES))
        .group_by(PurchaseOrderLine.item_id),
    )
    on_draft = await _sum_by_item(
        db,
        select(
            PurchaseOrderLine.item_id,
            func.sum(PurchaseOrderLine.quantity_ordered - PurchaseOrderLine.quantity_received),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrder.org_id == org_id, PurchaseOrder.status == "draft")
        .group_by(PurchaseOrderLine.item_id),
    )
    children = await load_bom_children(db, org_id)

    relevant: set[uuid.UUID] = set()
    stack = list(seed_gross.keys())
    while stack:
        node = stack.pop()
        if node in relevant:
            continue
        relevant.add(node)
        for child_id, _ in children.get(node, []):
            stack.append(child_id)

    indegree: dict[uuid.UUID, int] = dict.fromkeys(relevant, 0)
    for parent_id in relevant:
        for child_id, _ in children.get(parent_id, []):
            if child_id in relevant:
                indegree[child_id] += 1
    queue = deque(node for node in relevant if indegree[node] == 0)
    order: list[uuid.UUID] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for child_id, _ in children.get(node, []):
            if child_id in relevant:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    queue.append(child_id)
    if len(order) != len(relevant):
        order.extend(node for node in relevant if node not in set(order))

    items = {
        item.id: item
        for item in (await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(relevant))))
        .scalars()
        .all()
    }

    gross: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for item_id, qty in seed_gross.items():
        gross[item_id] += qty

    lines: list[MrpPlanLine] = []
    for item_id in order:
        item = items.get(item_id)
        if item is None:
            continue
        total_gross = gross[item_id]
        hand = on_hand.get(item_id, Decimal(0))
        order_qty = max(Decimal(0), on_order.get(item_id, Decimal(0)))
        draft_qty = max(Decimal(0), on_draft.get(item_id, Decimal(0)))
        safety = item.safety_stock_qty or Decimal(0)
        net = max(Decimal(0), total_gross - hand - order_qty - draft_qty + safety)
        due = need_by.get(item_id)

        for child_id, qty_per in children.get(item_id, []):
            gross[child_id] += net * qty_per
            if due is not None:
                existing_due = need_by.get(child_id)
                need_by[child_id] = due if existing_due is None else min(existing_due, due)

        if total_gross <= 0:
            continue
        indep = seed_gross.get(item_id, Decimal(0))
        is_manufactured = item_id in children

        suggested_qty = net
        suggested_date = None
        if not is_manufactured and net > 0:
            if item.min_order_qty is not None and item.min_order_qty > net:
                suggested_qty = item.min_order_qty
            if due is not None and item.lead_time_days is not None:
                suggested_date = due - timedelta(days=item.lead_time_days)

        lines.append(
            MrpPlanLine(
                item_id=str(item_id),
                item_sku=item.sku,
                item_name=item.name,
                is_manufactured=is_manufactured,
                independent_demand=indep,
                dependent_demand=total_gross - indep,
                gross_demand=total_gross,
                on_hand=hand,
                on_order=order_qty,
                on_draft=draft_qty,
                net_requirement=net,
                suggested_order_quantity=suggested_qty,
                need_by_date=due,
                suggested_order_date=suggested_date,
            )
        )

    lines.sort(key=lambda ln: (-ln.net_requirement, ln.item_sku))
    return lines


async def compute_build_plan(
    db: AsyncSession,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    build_quantity: Decimal,
    yield_loss_pct: Decimal,
) -> list[MrpPlanLine]:
    """The build calculator: 'I'm about to build build_quantity of item_id -
    what do I need to buy'. Reuses _explode_and_net (the same engine
    compute_mrp_plan uses) seeded from a single ad hoc quantity instead of
    demand forecasts - so it nets against real on-hand/on-order/on-draft
    stock exactly like planning does, and each component's own recipe-level
    scrap_pct (see BomLine.scrap_pct / load_bom_children) still applies.

    yield_loss_pct is a *different* scrap concept from BomLine.scrap_pct:
    it's the expected fraction of finished-good ATTEMPTS that fail entirely
    (QA rejects, scrapped builds), so it inflates the build quantity itself
    before exploding through the recipe - "I need build_quantity GOOD units,
    but only (1 - yield_loss_pct) of what I attempt will pass QA, so attempt
    build_quantity / (1 - yield_loss_pct/100)".

    No seed_due is passed - an ad hoc build has no forecast date, so every
    resulting line's need_by_date/suggested_order_date is None.
    """
    if build_quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "build_quantity must be positive")
    if yield_loss_pct < 0 or yield_loss_pct >= 100:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "yield_loss_pct must be at least 0 and less than 100"
        )
    has_active_bom = await db.scalar(
        select(Bom.id).where(Bom.org_id == org_id, Bom.item_id == item_id, Bom.status == "active")
    )
    if has_active_bom is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This item has no active BOM - nothing to build")

    attempted_qty = build_quantity / (1 - yield_loss_pct / 100)
    return await _explode_and_net(db, org_id, {item_id: attempted_qty}, None)
