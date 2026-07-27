from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forecast import DemandForecast
from app.models.sales import SalesOrder, SalesOrderLine
from app.models.tenant import Org

# Only an open order is a commitment. A cancelled one is off the books and a
# completed one has already shipped, so neither is demand any more.
OPEN_ORDER_STATUS = "open"


def combine_demand(
    forecast: dict[uuid.UUID, Decimal],
    committed: dict[uuid.UUID, Decimal],
    policy: str,
) -> dict[uuid.UUID, Decimal]:
    """One demand figure per item from the two sources.

    `consume` (the default) treats committed demand as part of the estimate
    rather than additional to it, so the answer is whichever is larger. An
    estimate of 100 with orders for 60 plans 100, because those 60 units were
    always expected to sell; orders for 130 plan 130, because reality has
    overtaken the estimate. Adding them instead would buy materials twice for
    the same sale.

    `add` sums them, which is right only when a shop's estimate deliberately
    excludes work already booked.

    Combination is per item across the whole window, not per date bucket,
    because compute_mrp_plan already flattens every forecast bucket in the
    horizon into one figure per item before netting. Two different summing
    rules in one engine would be worse than the precision is worth over 90
    days.
    """
    combined: dict[uuid.UUID, Decimal] = {}
    for item_id in set(forecast) | set(committed):
        estimated = forecast.get(item_id, Decimal(0))
        booked = committed.get(item_id, Decimal(0))
        combined[item_id] = estimated + booked if policy == "add" else max(estimated, booked)
    return combined


async def load_demand_policy(db: AsyncSession, org_id: uuid.UUID) -> str:
    return await db.scalar(select(Org.demand_policy).where(Org.id == org_id)) or "consume"


async def load_forecast_demand(
    db: AsyncSession, org_id: uuid.UUID, horizon_start: date, horizon_end: date
) -> tuple[dict[uuid.UUID, Decimal], dict[uuid.UUID, date]]:
    """Estimated demand: forecast quantity per item, and the earliest dated
    bucket per item, over the window."""
    rows = (
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
    quantities = {item_id: (total or Decimal(0)) for item_id, total, _ in rows}
    dates = {item_id: earliest for item_id, _, earliest in rows if earliest is not None}
    return quantities, dates


async def load_committed_demand(
    db: AsyncSession, org_id: uuid.UUID, horizon_start: date, horizon_end: date
) -> tuple[dict[uuid.UUID, Decimal], dict[uuid.UUID, date]]:
    """Committed demand: outstanding quantity per item across open sales
    orders, and the earliest needed-by date per item, up to the end of the
    window.

    Deliberately NOT bounded below by horizon_start, unlike the forecast query
    above. A forecast bucket in the past is spent, but an open order in the
    past is not spent, it is late: the goods are still owed. Dropping it would
    turn the grid green for the single most at-risk commitment in the shop.
    Callers can spot these by comparing the returned date against today; see
    GridProduct.committed_overdue.
    """
    rows = (
        await db.execute(
            select(
                SalesOrderLine.item_id,
                func.sum(SalesOrderLine.quantity_ordered - SalesOrderLine.quantity_delivered),
                func.min(SalesOrder.needed_by),
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.so_id)
            .where(
                SalesOrder.org_id == org_id,
                SalesOrder.status == OPEN_ORDER_STATUS,
                SalesOrder.needed_by <= horizon_end,
            )
            .group_by(SalesOrderLine.item_id)
        )
    ).all()
    quantities = {
        item_id: total for item_id, total, _ in rows if total is not None and total > 0
    }
    dates = {
        item_id: earliest
        for item_id, total, earliest in rows
        if earliest is not None and total is not None and total > 0
    }
    return quantities, dates


def earliest_dates(
    forecast_dates: dict[uuid.UUID, date], committed_dates: dict[uuid.UUID, date]
) -> dict[uuid.UUID, date]:
    """Per item, the earlier of the two sources' dates. Whichever source is
    more urgent sets the date the plan works back from."""
    merged = dict(forecast_dates)
    for item_id, when in committed_dates.items():
        existing = merged.get(item_id)
        merged[item_id] = when if existing is None else min(existing, when)
    return merged
