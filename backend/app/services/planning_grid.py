from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Item
from app.models.forecast import DemandForecast
from app.models.procurement import Supplier
from app.services.demand import combine_demand, load_committed_demand, load_demand_policy
from app.services.demand_forecasting import DEFAULT_LOOKBACK_DAYS, compute_weekly_suggestions
from app.services.demand_history import daily_consumption_by_item
from app.services.mrp import MrpPlanLine, compute_mrp_plan, load_bom_children, load_on_hand
from app.services.planning_loads import (  # noqa: F401 - re-exported, see below
    GRID_HORIZON_DAYS,
    load_production_supply,
    resolve_grid_window,
)
from app.services.work_orders import component_requirements, reserved_by_higher_priority

# GRID_HORIZON_DAYS and resolve_grid_window live in planning_loads.py now (a
# leaf module mrp.py and work_orders.py also import from) but are re-exported
# here unchanged: this was their original home, and every other caller -
# api/routes/planning_grid.py, tests/test_planning_grid.py - still does
# `from app.services.planning_grid import resolve_grid_window`.

# How far back a consumed item counts as something the user still plans. Reuses
# the suggestion engine's own lookback: an item outside it can never carry a
# suggestion, so a row for it could never be anything but empty.
CONSUMPTION_LOOKBACK_DAYS = DEFAULT_LOOKBACK_DAYS

_STATUS_RANK = {"missing": 0, "arriving": 1, "in_stock": 2}


@dataclass
class GridProduct:
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    # Sum of the item's forecast quantities inside the window - the one number
    # the user types. 0 means "not planning any right now".
    planned_qty: Decimal
    # Earliest forecast period inside the window, None when planned_qty is 0.
    needed_by: date | None
    # How many separate forecast rows inside the window make up planned_qty.
    # 0 when nothing is planned, 1 for the normal single-date case, more than
    # 1 only for an imported or legacy plan spread across several dates - the
    # frontend needs this to warn before collapsing them into one on edit.
    planned_bucket_count: int
    # What customers have actually ordered inside the window, as opposed to
    # planned_qty which is the estimate someone typed. Outstanding quantity on
    # open sales orders only - see services.demand.load_committed_demand.
    committed_qty: Decimal
    # True when some of that committed quantity was due before today. The
    # goods are still owed, so it still counts as demand, but a late
    # commitment is a different problem from an upcoming one and the row says
    # so rather than looking like any other red row.
    committed_overdue: bool
    on_hand: Decimal
    # in_stock | arriving | missing, judged over the components reachable from
    # this product through active BOMs (or the product itself when it has none).
    materials_status: str
    missing_materials: list[str]
    # Meaningful only when materials_status == "arriving": true when every
    # covering component is on a draft PO only, none of them actually
    # submitted or in flight. A draft can still be edited or cancelled, so
    # this is the difference between "on the way" and "nothing has been
    # sent to a supplier yet".
    arriving_is_drafted: bool
    # Earliest date the planned quantity can actually be finished: today when
    # every component is on the shelf, otherwise today plus the longest lead
    # time among the components that must still arrive. None when nothing is
    # planned. POs do not carry a promised delivery date yet, so an
    # already-ordered component is assumed to take its full lead time again -
    # the honest pessimistic read until PO dates exist.
    ready_by: date | None
    # None collapses two different situations that read identically to the
    # user otherwise: nothing is planned yet (this stays False), and
    # something is planned but a component's lead time is not on file so
    # ready_by cannot be computed at all (this is True). "Not scheduled" is
    # the right copy for the first; "Lead time unknown" is the right copy
    # for the second, and confusing them reads as the plan itself being
    # unscheduled when it is the data that is incomplete.
    ready_by_unknown: bool
    # What history says this product's demand will be over the window, offered
    # only when the user has not planned anything yet - a hint to accept, never
    # a silent write. None when there is no consumption history to learn from.
    suggested_qty: Decimal | None
    # Units already being made, as opposed to planned_qty which is the estimate
    # someone typed and committed_qty which is what customers ordered.
    in_production: Decimal
    # What a job raised from this row would be for: the row's demand under the
    # org's demand_policy, less what jobs are already making. Computed here
    # rather than by the frontend from the three fields above, because doing it
    # in two places is how the committed-only row came to have no Make button
    # at all while every other cell on it read from committed demand.
    outstanding_qty: Decimal
    # False when the product has no active recipe or its components are short,
    # because offering Make on a job that would only 409 is a trap. Also false
    # when jobs already cover the whole row, where there is nothing left to
    # raise.
    can_make: bool
    # Why can_make is False when outstanding_qty > 0 (i.e. the button would
    # otherwise be offered): no active recipe, or which component is short
    # and by how much. None when can_make is True or nothing is outstanding,
    # since the row already has other copy for those cases.
    cannot_make_reason: str | None
    # The engine's own numbers for this product and its components, shown when
    # the user expands the row. The only place MRP vocabulary stays visible.
    detail: list[MrpPlanLine]


_URGENCY_RANK = {"overdue": 0, "unknown": 1, "soon": 2, "later": 3}

# An order date inside this many days is "soon" - near enough that the user
# should act on this visit rather than the next one.
SOON_DAYS = 7


@dataclass
class GridToBuy:
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    # Already rounded up to the item's minimum order quantity by the engine.
    quantity: Decimal
    supplier_name: str | None
    order_by: date | None
    # overdue | unknown | soon | later. "unknown" is a component that must be
    # ordered but has no lead time on file - not the same as "later", which
    # means a lead time is known and there is comfortably enough runway.
    urgency: str


@dataclass
class PlanningGrid:
    horizon_start: date
    horizon_end: date
    products: list[GridProduct]
    to_buy: list[GridToBuy]


def _reachable_components(
    root: uuid.UUID, children: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]]
) -> set[uuid.UUID]:
    """Every component below root in the active-BOM graph, at any depth."""
    seen: set[uuid.UUID] = set()
    stack = [child for child, _ in children.get(root, [])]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(child for child, _ in children.get(node, []))
    return seen


async def _reserved_against_a_new_job(db: AsyncSession, org_id: uuid.UUID, reserved_for) -> dict:
    """What existing scheduled jobs hold ahead of the job a Make press would
    raise: undated, at the default location, numbered next.

    Asking the real reservation rule about that hypothetical job is what keeps
    the button and the start endpoint agreeing. Reproducing the ranking here
    instead is how the grid would come to offer Make on a job that 409s.
    """
    from app.models.work_order import WorkOrder
    from app.services.locations import get_default_location_id

    next_number = (
        await db.scalar(select(func.max(WorkOrder.wo_number)).where(WorkOrder.org_id == org_id))
        or 0
    ) + 1
    prospective = WorkOrder(
        id=uuid.uuid4(),
        org_id=org_id,
        location_id=await get_default_location_id(db, org_id),
        scheduled_start=None,
        wo_number=next_number,
    )
    return await reserved_for(db, org_id, prospective)


async def compute_planning_grid(
    db: AsyncSession, org_id: uuid.UUID, as_of: date | None = None
) -> PlanningGrid:
    """One row per product a user plans, with a traffic-light material status.

    A "product" is an item with an active BOM that no other active BOM consumes
    (the top of a build tree), plus any item carrying a forecast in the window -
    that second half covers a purchased item sold as-is, which has no BOM but is
    still something the user plans. Sub-assemblies never get their own row: the
    engine handles them, and showing them would be exactly the disjointed detail
    this screen exists to hide. Because only active BOMs are read, archiving a
    BOM drops its materials out of `components` and, if they have consumption
    history, promotes them straight to their own top-level rows.

    Status is judged over the components reachable from the product (or the
    product itself when it has no BOM, where "the material" is the thing you buy):

        missing   - some component still has a net requirement, so it must be ordered
        arriving  - nothing is short, but some component is only covered thanks to
                    an open or drafted PO rather than stock on the shelf
        in_stock  - every component is covered by stock on hand
    """
    horizon_start, horizon_end = resolve_grid_window(as_of)

    # Loaded once and threaded into compute_mrp_plan below, rather than this
    # function AND the engine each fetching a byte-identical copy: two
    # org-wide explosions per grid read used to run the same on-hand,
    # bom-children, in-production, committed-demand and demand-policy queries
    # twice, which is also how the committed-demand widening (C2) went
    # unnoticed in two places at once.
    children = await load_bom_children(db, org_id)
    on_hand = await load_on_hand(db, org_id)
    production_by_item = await load_production_supply(db, org_id, horizon_end)
    committed_qty_by_item, committed_dates = await load_committed_demand(
        db, org_id, horizon_start, horizon_end
    )
    policy = await load_demand_policy(db, org_id)

    plan = await compute_mrp_plan(
        db,
        org_id,
        horizon_start,
        horizon_end,
        demand_policy=policy,
        committed=(committed_qty_by_item, committed_dates),
        children=children,
        on_hand=on_hand,
        production_supply=production_by_item,
    )
    by_item = {uuid.UUID(line.item_id): line for line in plan}
    reserved = await _reserved_against_a_new_job(db, org_id, reserved_by_higher_priority)

    forecast_rows = (
        await db.execute(
            select(
                DemandForecast.item_id,
                func.sum(DemandForecast.quantity),
                func.min(DemandForecast.period_start),
                func.count(DemandForecast.id),
            )
            .where(
                DemandForecast.org_id == org_id,
                DemandForecast.period_start >= horizon_start,
                DemandForecast.period_start <= horizon_end,
            )
            .group_by(DemandForecast.item_id)
        )
    ).all()
    planned = {
        item_id: (qty, first, count) for item_id, qty, first, count in forecast_rows
    }
    # The same combination the engine nets against, so the Make quantity and
    # the row's colour are answering the same question.
    demand_by_item = combine_demand(
        {item_id: total for item_id, (total, _, _) in planned.items()},
        committed_qty_by_item,
        policy,
    )

    produced = set(children)
    components = {child for kids in children.values() for child, _ in kids}
    consumed = set(
        await daily_consumption_by_item(
            db, org_id, datetime.now(timezone.utc) - timedelta(days=CONSUMPTION_LOOKBACK_DAYS)
        )
    )
    # Committed items union in the same way planned ones do, outside the
    # `- components` subtraction: a component sold to a customer directly is a
    # legitimate product, and an order is a firmer claim to a row than a typed
    # estimate is.
    product_ids = ((produced | consumed) - components) | set(planned) | set(committed_qty_by_item)
    # ids that only made the cut because of consumption history - not a
    # produced top-level item, not something the user has forecast, and not
    # something a customer has ordered.
    consumed_only_ids = (
        (consumed - components) - produced - set(planned) - set(committed_qty_by_item)
    )
    if not product_ids:
        return PlanningGrid(
            horizon_start=horizon_start, horizon_end=horizon_end, products=[], to_buy=[]
        )

    wanted = product_ids | set(by_item)
    items = {
        item.id: item
        for item in (
            await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(wanted)))
        )
        .scalars()
        .all()
    }

    products: list[GridProduct] = []
    for product_id in product_ids:
        item = items.get(product_id)
        if item is None:
            continue
        qty, first_period, bucket_count = planned.get(product_id, (Decimal(0), None, 0))
        evaluation = _reachable_components(product_id, children) or {product_id}

        missing_materials: list[str] = []
        arriving = False
        # True only as long as every "arriving" component's shortfall is
        # covered solely by a draft PO (on_order and in_production both 0).
        # A draft is still being edited and can be changed or cancelled
        # outright, unlike a submitted PO or a job already in flight, so a
        # row covered only by drafts must not read "Arriving" as if
        # something had actually been sent to a supplier.
        arriving_is_drafted = True
        ready_by = horizon_start
        # An arriving/missing component with no lead time on file makes the
        # whole product's ready_by unknowable, not same-day: see
        # Item.lead_time_days - null means "don't know", never zero.
        unknown_lead = False
        for component_id in evaluation:
            line = by_item.get(component_id)
            # A sub-assembly's own shortfall is a build instruction, not a
            # material anyone has to order, and the parts it is built from are
            # already in this same set. Only purchased lines set the colour and
            # the date, so a product whose every buyable part is on the shelf
            # reads green even when a sub-assembly still has to be built.
            if line is None or line.is_manufactured:
                continue
            component = items.get(component_id)
            lead_days = component.lead_time_days if component is not None else None
            if line.net_requirement > 0:
                missing_materials.append(line.item_sku)
                if lead_days is None:
                    unknown_lead = True
                else:
                    ready_by = max(ready_by, horizon_start + timedelta(days=lead_days))
            elif line.on_hand < line.gross_demand:
                arriving = True
                if line.on_order > 0 or line.in_production > 0:
                    arriving_is_drafted = False
                if lead_days is None:
                    unknown_lead = True
                else:
                    ready_by = max(ready_by, horizon_start + timedelta(days=lead_days))
        missing_materials.sort()

        if missing_materials:
            status = "missing"
        elif arriving:
            status = "arriving"
        else:
            status = "in_stock"

        detail = sorted(
            (by_item[i] for i in {product_id, *evaluation} if i in by_item),
            key=lambda ln: (ln.item_id != str(product_id), ln.item_sku),
        )

        committed = committed_qty_by_item.get(product_id, Decimal(0))
        in_production = production_by_item.get(product_id, Decimal(0))
        # Demand under the org's own policy, not the typed forecast alone: a
        # shop working from sales orders types nothing here, and basing Make on
        # planned_qty left the firmest demand in the shop with no way to build
        # it. combine_demand is the same rule the engine nets against.
        outstanding = max(Decimal(0), demand_by_item.get(product_id, qty) - in_production)
        can_make = outstanding > 0 and product_id in children
        # Why the button would be disabled, captured at the point can_make
        # breaks rather than thrown away. Without this a row can read green
        # ("All in stock") while Make stays dead, because materials_status
        # judges the full planned quantity with no reservations subtracted
        # while can_make judges outstanding against on-hand minus what other
        # jobs already hold - two different questions that can disagree.
        cannot_make_reason: str | None = None
        if outstanding > 0 and product_id not in children:
            cannot_make_reason = "This product has no active bill of materials."
        elif can_make:
            for component_id, required in await component_requirements(
                db, org_id, product_id, outstanding, children=children
            ):
                # Org-wide on-hand, the same approximation every other cell on
                # this screen makes; start checks the job's own location, so a
                # multi-site org can still see a refusal here it was not warned
                # about.
                free = on_hand.get(component_id, Decimal(0)) - reserved.get(
                    component_id, Decimal(0)
                )
                if free < required:
                    can_make = False
                    component_item = items.get(component_id)
                    sku = component_item.sku if component_item is not None else str(component_id)
                    cannot_make_reason = (
                        f"Not enough {sku} free to start: {free} available, {required} needed."
                    )
                    break

        products.append(
            GridProduct(
                item_id=product_id,
                item_sku=item.sku,
                item_name=item.name,
                planned_qty=qty,
                needed_by=first_period,
                planned_bucket_count=bucket_count,
                committed_qty=committed,
                committed_overdue=(
                    committed > 0
                    and committed_dates.get(product_id, horizon_start) < horizon_start
                ),
                on_hand=on_hand.get(product_id, Decimal(0)),
                materials_status=status,
                missing_materials=missing_materials,
                arriving_is_drafted=arriving and arriving_is_drafted,
                # Committed demand counts as something to make: a row that
                # exists only because a customer ordered it still needs a
                # date, and a blank one on a red row is the least useful
                # answer this screen can give.
                ready_by=None if (qty <= 0 and committed <= 0) or unknown_lead else ready_by,
                ready_by_unknown=unknown_lead and (qty > 0 or committed > 0),
                suggested_qty=None,
                in_production=in_production,
                outstanding_qty=outstanding,
                can_make=can_make,
                cannot_make_reason=cannot_make_reason,
                detail=detail,
            )
        )

    # A product a customer has already ordered does not need a guess from
    # history: the order is better information than the forecast is.
    unplanned = {p.item_id for p in products if p.planned_qty <= 0 and p.committed_qty <= 0}
    if unplanned:
        suggestions = await compute_weekly_suggestions(
            db, org_id, horizon_days=GRID_HORIZON_DAYS, as_of=horizon_start, item_ids=unplanned
        )
        suggested_by_item = {
            suggestion.item_id: Decimal(str(round(sum(p.yhat for p in suggestion.points), 4)))
            for suggestion in suggestions
        }
        for product in products:
            suggested = suggested_by_item.get(product.item_id)
            if suggested is not None and suggested > 0:
                product.suggested_qty = suggested

    # A consumed-only item earns its row by having something to suggest;
    # otherwise it is noise on a screen meant to be read at a glance - an
    # empty green row with nothing planned, nothing missing, and nothing to
    # offer.
    products = [
        p
        for p in products
        if p.item_id not in consumed_only_ids or p.suggested_qty is not None
    ]

    supplier_names = {
        supplier.id: supplier.name
        for supplier in (
            await db.execute(select(Supplier).where(Supplier.org_id == org_id))
        )
        .scalars()
        .all()
    }
    to_buy: list[GridToBuy] = []
    for line in plan:
        if line.is_manufactured or line.suggested_order_quantity <= 0:
            continue
        line_item = items.get(uuid.UUID(line.item_id))
        supplier_name = None
        if line_item is not None and line_item.preferred_supplier_id is not None:
            supplier_name = supplier_names.get(line_item.preferred_supplier_id)
        order_by = line.suggested_order_date
        if order_by is None:
            # No lead time on file for this item (see Item.lead_time_days):
            # a real shortfall with an unknowable order date, not the least
            # urgent thing on the list.
            urgency = "unknown"
        elif order_by < horizon_start:
            urgency = "overdue"
        elif order_by <= horizon_start + timedelta(days=SOON_DAYS):
            urgency = "soon"
        else:
            urgency = "later"
        to_buy.append(
            GridToBuy(
                item_id=uuid.UUID(line.item_id),
                item_sku=line.item_sku,
                item_name=line.item_name,
                quantity=line.suggested_order_quantity,
                supplier_name=supplier_name,
                order_by=order_by,
                urgency=urgency,
            )
        )
    to_buy.sort(key=lambda b: (_URGENCY_RANK[b.urgency], b.order_by or date.max, b.item_sku))

    products.sort(
        key=lambda p: (_STATUS_RANK[p.materials_status], p.ready_by or date.max, p.item_sku)
    )
    return PlanningGrid(
        horizon_start=horizon_start, horizon_end=horizon_end, products=products, to_buy=to_buy
    )
