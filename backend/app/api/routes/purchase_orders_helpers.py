"""Schemas and helpers for the /purchase-orders routes (split out of purchase_orders.py)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_or_404
from app.models.bom import Item
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier


# Guards the po_number max+1 read-then-insert race: two concurrent creates in
# the same org can both read the same max and collide on uq_pos_org_number.
# Retry a few times rather than surfacing an unhandled 500.
MAX_PO_NUMBER_ATTEMPTS = 3


class PurchaseOrderLineInput(BaseModel):
    item_id: str
    quantity_ordered: Decimal
    unit_price: Decimal
    sequence: int = 0


class PurchaseOrderLineResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
    item_is_lot_tracked: bool
    quantity_ordered: Decimal
    quantity_received: Decimal
    unit_price: Decimal
    sequence: int


class ReceiveLineInput(BaseModel):
    po_line_id: str
    quantity: Decimal
    # Portion of `quantity` that failed inspection - not additional to it.
    # See PurchaseOrderReceiptLine.quantity_rejected for the full contract.
    quantity_rejected: Decimal = Decimal(0)
    reject_reason: str | None = None
    # Required if the line's item is lot-tracked, rejected otherwise - see
    # receive_purchase_order's validation loop.
    lot_number: str | None = None
    expiry_date: date | None = None
    # Which location the stock lands in. Defaults to the org's default location.
    location_id: str | None = None


class PurchaseOrderCreateRequest(BaseModel):
    supplier_id: str
    expected_delivery_date: date | None = None


class PurchaseOrderResponse(BaseModel):
    id: str
    supplier_id: str
    supplier_name: str
    po_number: int
    status: str
    expected_delivery_date: date | None


class PurchaseOrderDetailResponse(PurchaseOrderResponse):
    lines: list[PurchaseOrderLineResponse]


async def _fetch_po_lines_with_items(
    db: AsyncSession, po_id: uuid.UUID, *, for_update: bool = False
) -> list[tuple[PurchaseOrderLine, Item]]:
    query = (
        select(PurchaseOrderLine, Item)
        .join(Item, Item.id == PurchaseOrderLine.item_id)
        .where(PurchaseOrderLine.po_id == po_id)
        .order_by(PurchaseOrderLine.sequence)
    )
    if for_update:
        query = query.with_for_update(of=PurchaseOrderLine)
    result = await db.execute(query)
    return result.all()


async def _po_to_response(
    db: AsyncSession, po: PurchaseOrder, supplier: Supplier | None = None
) -> PurchaseOrderResponse:
    if supplier is None:
        supplier = await get_or_404(db, Supplier, po.org_id, po.supplier_id, "Supplier not found")
    return PurchaseOrderResponse(
        id=str(po.id),
        supplier_id=str(po.supplier_id),
        supplier_name=supplier.name,
        po_number=po.po_number,
        status=po.status,
        expected_delivery_date=po.expected_delivery_date,
    )
