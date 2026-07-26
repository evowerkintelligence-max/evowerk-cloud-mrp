from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Item
from app.models.forecast import DemandForecast
from app.services.mrp import compute_mrp_plan, load_bom_children, load_on_hand

# The grid never shows a horizon control - a non-technical user should not have
# to pick one. 90 days matches the engine default (services.mrp.resolve_horizon),
# so the grid and a raw /mrp/plan call agree.
GRID_HORIZON_DAYS = 90

# Where a typed quantity lands when the user does not pick a date. Two weeks is
# far enough out that most purchased parts can still arrive in time.
DEFAULT_NEEDED_BY_DAYS = 14

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
    on_hand: Decimal
    # in_stock | arriving | missing, judged over the components reachable from
    # this product through active BOMs (or the product itself when it has none).
    materials_status: str
    missing_materials: list[str]


@dataclass
class PlanningGrid:
    horizon_start: date
    horizon_end: date
    products: list[GridProduct]


def resolve_grid_window(as_of: date | None = None) -> tuple[date, date]:
    start = as_of if as_of is not None else datetime.now(timezone.utc).date()
    return start, start + timedelta(days=GRID_HORIZON_DAYS)


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


async def compute_planning_grid(
    db: AsyncSession, org_id: uuid.UUID, as_of: date | None = None
) -> PlanningGrid:
    """One row per product a user plans, with a traffic-light material status.

    A "product" is an item with an active BOM that no other active BOM consumes
    (the top of a build tree), plus any item carrying a forecast in the window -
    that second half covers a purchased item sold as-is, which has no BOM but is
    still something the user plans. Sub-assemblies never get their own row: the
    engine handles them, and showing them would be exactly the disjointed detail
    this screen exists to hide.

    Status is judged over the components reachable from the product (or the
    product itself when it has no BOM, where "the material" is the thing you buy):

        missing   - some component still has a net requirement, so it must be ordered
        arriving  - nothing is short, but some component is only covered thanks to
                    an open or drafted PO rather than stock on the shelf
        in_stock  - every component is covered by stock on hand
    """
    horizon_start, horizon_end = resolve_grid_window(as_of)

    children = await load_bom_children(db, org_id)
    plan = await compute_mrp_plan(db, org_id, horizon_start, horizon_end)
    by_item = {uuid.UUID(line.item_id): line for line in plan}
    on_hand = await load_on_hand(db, org_id)

    forecast_rows = (
        await db.execute(
            select(
                DemandForecast.item_id,
                func.sum(DemandForecast.quantity),
                func.min(DemandForecast.period_start),
            )
            .where(
                DemandForecast.org_id == org_id,
                DemandForecast.period_start >= horizon_start,
                DemandForecast.period_start <= horizon_end,
            )
            .group_by(DemandForecast.item_id)
        )
    ).all()
    planned = {item_id: (qty, first) for item_id, qty, first in forecast_rows}

    produced = set(children)
    components = {child for kids in children.values() for child, _ in kids}
    product_ids = (produced - components) | set(planned)
    if not product_ids:
        return PlanningGrid(horizon_start=horizon_start, horizon_end=horizon_end, products=[])

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
        qty, first_period = planned.get(product_id, (Decimal(0), None))
        evaluation = _reachable_components(product_id, children) or {product_id}

        missing_materials: list[str] = []
        arriving = False
        for component_id in evaluation:
            line = by_item.get(component_id)
            if line is None:
                continue
            if line.net_requirement > 0:
                missing_materials.append(line.item_sku)
            elif line.on_hand < line.gross_demand:
                arriving = True
        missing_materials.sort()

        if missing_materials:
            status = "missing"
        elif arriving:
            status = "arriving"
        else:
            status = "in_stock"

        products.append(
            GridProduct(
                item_id=product_id,
                item_sku=item.sku,
                item_name=item.name,
                planned_qty=qty,
                needed_by=first_period,
                on_hand=on_hand.get(product_id, Decimal(0)),
                materials_status=status,
                missing_materials=missing_materials,
            )
        )

    products.sort(key=lambda p: (_STATUS_RANK[p.materials_status], p.item_sku))
    return PlanningGrid(horizon_start=horizon_start, horizon_end=horizon_end, products=products)
