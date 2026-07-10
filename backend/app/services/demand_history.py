from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryTransaction
from app.models.lot import Lot, LotTransaction


async def daily_consumption_by_item(
    db: AsyncSession,
    org_id: uuid.UUID,
    since: datetime,
    *,
    item_ids: set[uuid.UUID] | None = None,
) -> dict[uuid.UUID, dict[date, Decimal]]:
    """Actual usage history, merged across both on-hand sources (non-lot-tracked
    InventoryTransaction + lot-tracked LotTransaction/Lot). Bucketed by day in
    Python - not SQL date_trunc/date() - since tests run on SQLite and prod on
    Postgres, and no shared cross-dialect date-bucketing helper exists.

    Shared by inventory_analytics (safety stock / ABC-XYZ) and
    demand_forecasting (moving-average / exp-smoothing projection) - one
    source of truth for "what did this item actually consume, per day."
    `item_ids` narrows to specific items (e.g. a single-item forecast call);
    omit to pull every item in the org (e.g. the analytics list endpoint).
    """
    daily: dict[uuid.UUID, dict[date, Decimal]] = {}

    non_lot_query = select(
        InventoryTransaction.item_id, InventoryTransaction.created_at, InventoryTransaction.quantity_delta
    ).where(
        InventoryTransaction.org_id == org_id,
        InventoryTransaction.transaction_type == "consumption",
        InventoryTransaction.created_at >= since,
    )
    if item_ids is not None:
        non_lot_query = non_lot_query.where(InventoryTransaction.item_id.in_(item_ids))
    non_lot_rows = await db.execute(non_lot_query)
    for item_id, created_at, delta in non_lot_rows.all():
        by_date = daily.setdefault(item_id, {})
        d = created_at.date()
        by_date[d] = by_date.get(d, Decimal(0)) + abs(delta)

    lot_query = (
        select(Lot.item_id, LotTransaction.created_at, LotTransaction.quantity)
        .join(Lot, Lot.id == LotTransaction.lot_id)
        .where(
            LotTransaction.org_id == org_id,
            LotTransaction.transaction_type == "consumption",
            LotTransaction.created_at >= since,
        )
    )
    if item_ids is not None:
        lot_query = lot_query.where(Lot.item_id.in_(item_ids))
    lot_rows = await db.execute(lot_query)
    for item_id, created_at, qty in lot_rows.all():
        by_date = daily.setdefault(item_id, {})
        d = created_at.date()
        by_date[d] = by_date.get(d, Decimal(0)) + qty

    return daily
