from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.procurement import PurchaseOrder, PurchaseOrderLine


async def latest_unit_prices(
    db: AsyncSession, org_id: uuid.UUID, item_ids: set[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Most recent negotiated unit_price per item across all its PO lines. PO
    lines have no timestamp of their own, so 'most recent' is by the parent
    PO's created_at. Shared by MRP's generate-po price prefill and inventory
    analytics' ABC $-value ranking - one source of truth for "what does this
    item cost" from the data we actually have (no separate Item.unit_cost
    field exists)."""
    if not item_ids:
        return {}
    rows = await db.execute(
        select(PurchaseOrderLine.item_id, PurchaseOrderLine.unit_price, PurchaseOrder.created_at)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrder.org_id == org_id, PurchaseOrderLine.item_id.in_(item_ids))
    )
    latest: dict[uuid.UUID, tuple[Decimal, datetime]] = {}
    for item_id, price, created_at in rows.all():
        if item_id not in latest or created_at > latest[item_id][1]:
            latest[item_id] = (price, created_at)
    return {item_id: price for item_id, (price, _) in latest.items()}
