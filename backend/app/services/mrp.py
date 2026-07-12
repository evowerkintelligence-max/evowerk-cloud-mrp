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
from app.models.forecast import DemandForecast
from app.models.inventory import InventoryBalance
from app.models.lot import Lot
from app.models.procurement import PurchaseOrder, PurchaseOrderLine

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


async def _min_date_by_item(db: AsyncSession, query) -> dict[uuid.UUID, date]:
    result = await db.execute(query)
    return dict(result.all())


async def _load_bom_children(
    db: AsyncSession, org_id: uuid.UUID
) -> dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]]:
    """Manufacturing graph over ACTIVE BOMs only: produced item_id -> list of
    (component_item_id, quantity_per_unit). Draft/archived BOMs are ignored -
    only the currently-active BOM defines how an item is really built, and
    activation already enforces one-active-per-item and acyclicity.
    """
    rows = await db.execute(
        select(Bom.item_id, BomLine.component_item_id, BomLine.quantity)
        .join(BomLine, BomLine.bom_id == Bom.id)
        .where(Bom.org_id == org_id, Bom.status == "active")
    )
    children: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]] = defaultdict(list)
    for parent_id, child_id, qty in rows.all():
        children[parent_id].append((child_id, qty))
    return children


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

    - independent_demand: the item's own forecast buckets whose period_start is
      in [horizon_start, horizon_end].
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

    This is *netted* explosion, not gross: each assembly is netted against its
    own on-hand/on-order first, and only the resulting net requirement explodes
    to its components. Finished-good and sub-assembly stock therefore correctly
    reduces how much of each raw component you actually need to buy. Items are
    processed parents-before-components (topological order over the active-BOM
    DAG) so an assembly's net is final before it explodes downward.

    Demand-driven: items reached by neither a forecast nor an explosion aren't
    planning actions, so they're omitted. Time-phasing per bucket (splitting the
    horizon into multiple periods rather than netting it as one) and lot-size
    multiples (rounding to a pack size above the MOQ) are intentionally not
    modelled yet.

    Lead-time offsetting and MOQ rounding *are* modelled, on top of the netting
    above:
    - need_by_date: the earliest in-horizon forecast date reaching this item,
      propagated down through explosion (a component's need date is its
      earliest-needing parent's need date - this module doesn't model
      manufacturing lead time, so a produced parent's own build time isn't
      subtracted before propagating to its components; only supplier lead time
      on purchased items is).
    - suggested_order_date = need_by_date - item.lead_time_days, when both are
      known and the item is purchased (not manufactured - a build has no
      supplier lead time to offset by).
    - suggested_order_quantity rounds net_requirement up to item.min_order_qty
      for purchased items when the MOQ is higher. Explosion always uses the
      raw net, never the MOQ-rounded figure, so one item's MOQ can't inflate
      what its components need.
    """
    independent = await _sum_by_item(
        db,
        select(DemandForecast.item_id, func.sum(DemandForecast.quantity))
        .where(
            DemandForecast.org_id == org_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
        .group_by(DemandForecast.item_id),
    )
    if not independent:
        return []

    # Earliest in-horizon forecast date per item - seeds need_by below. A
    # separate query (not reused from `independent`) because this needs MIN,
    # not SUM, over the same rows.
    need_by = await _min_date_by_item(
        db,
        select(DemandForecast.item_id, func.min(DemandForecast.period_start))
        .where(
            DemandForecast.org_id == org_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
        .group_by(DemandForecast.item_id),
    )

    # On-hand comes from two disjoint sources: active lots (lot-tracked items)
    # and inventory_balances (everything else). Merge into one item->qty map.
    on_hand = await _sum_by_item(
        db,
        select(Lot.item_id, func.sum(Lot.quantity_remaining))
        .where(Lot.org_id == org_id, Lot.status == "active")
        .group_by(Lot.item_id),
    )
    # Sum a non-lot-tracked item's on-hand across all its locations - MRP plans
    # against total org availability, not per location.
    inventory_on_hand = await _sum_by_item(
        db,
        select(InventoryBalance.item_id, func.sum(InventoryBalance.quantity_on_hand))
        .where(InventoryBalance.org_id == org_id)
        .group_by(InventoryBalance.item_id),
    )
    for item_id, qty in inventory_on_hand.items():
        on_hand[item_id] = on_hand.get(item_id, Decimal(0)) + qty
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
    # Draft POs are planned-but-uncommitted supply: they offset the net
    # requirement (so drafting an order, then re-planning or re-generating,
    # doesn't double-count the shortfall) but stay a distinct bucket from
    # submitted on_order. A draft's quantity_received is always 0, but the same
    # outstanding expression is used for uniformity.
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
    children = await _load_bom_children(db, org_id)

    # Every item touched by the plan: the forecast items plus everything
    # reachable from them through the manufacturing graph.
    relevant: set[uuid.UUID] = set()
    stack = list(independent.keys())
    while stack:
        node = stack.pop()
        if node in relevant:
            continue
        relevant.add(node)
        for child_id, _ in children.get(node, []):
            stack.append(child_id)

    # Topological order, parents before components (Kahn). Active BOMs are
    # acyclic (enforced at activation), so this consumes every relevant node.
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
        # A cycle slipped past activation's guard (shouldn't happen). Surface
        # the leftovers un-exploded rather than dropping them or looping.
        order.extend(node for node in relevant if node not in set(order))

    items = {
        item.id: item
        for item in (await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(relevant))))
        .scalars()
        .all()
    }

    gross: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for item_id, qty in independent.items():
        gross[item_id] += qty

    lines: list[MrpPlanLine] = []
    for item_id in order:
        item = items.get(item_id)
        if item is None:
            # BOM/forecast referencing a deleted item - skip rather than 500.
            continue
        total_gross = gross[item_id]
        hand = on_hand.get(item_id, Decimal(0))
        # Clamp: a fully-received line contributes 0; the aggregate can't
        # legitimately go negative, but guard against a data quirk manufacturing
        # phantom supply.
        order_qty = max(Decimal(0), on_order.get(item_id, Decimal(0)))
        draft_qty = max(Decimal(0), on_draft.get(item_id, Decimal(0)))
        net = max(Decimal(0), total_gross - hand - order_qty - draft_qty)
        due = need_by.get(item_id)

        # Explode the NET (not gross) requirement onto components, and
        # propagate this item's own need date down so a component's need_by
        # is the earliest date any parent actually needs it by. Processing in
        # topological order guarantees total_gross (and due) above is final
        # before either propagation happens.
        for child_id, qty_per in children.get(item_id, []):
            gross[child_id] += net * qty_per
            if due is not None:
                existing_due = need_by.get(child_id)
                need_by[child_id] = due if existing_due is None else min(existing_due, due)

        if total_gross <= 0:
            # Reached but not actually required (e.g. a component of an assembly
            # fully covered by stock) - nothing to plan.
            continue
        indep = independent.get(item_id, Decimal(0))
        is_manufactured = item_id in children

        # MOQ rounding and lead-time offset are purchasing concerns, so they
        # only apply to purchased (not manufactured) items with a real
        # shortfall - a build has no supplier MOQ or lead time to round/offset
        # by.
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

    # Most urgent first (biggest shortfall), then a stable sku tiebreak.
    lines.sort(key=lambda ln: (-ln.net_requirement, ln.item_sku))
    return lines
