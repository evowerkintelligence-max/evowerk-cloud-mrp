from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.forecast import DemandForecast
from app.services.planning_grid import (
    GridProduct,
    compute_planning_grid,
    resolve_grid_window,
)

router = APIRouter(prefix="/planning", tags=["planning"])


class GridDetailLineResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    is_manufactured: bool
    gross_demand: Decimal
    on_hand: Decimal
    on_order: Decimal
    on_draft: Decimal
    net_requirement: Decimal
    suggested_order_quantity: Decimal
    need_by_date: date | None
    suggested_order_date: date | None


class GridProductResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    planned_qty: Decimal
    needed_by: date | None
    planned_bucket_count: int
    committed_qty: Decimal
    on_hand: Decimal
    materials_status: str
    missing_materials: list[str]
    ready_by: date | None
    suggested_qty: Decimal | None
    detail: list[GridDetailLineResponse]


class GridToBuyResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    quantity: Decimal
    supplier_name: str | None
    order_by: date | None
    urgency: str


class GridSummaryResponse(BaseModel):
    missing: int
    arriving: int
    in_stock: int


class PlanningGridResponse(BaseModel):
    horizon_start: date
    horizon_end: date
    products: list[GridProductResponse]
    to_buy: list[GridToBuyResponse]
    summary: GridSummaryResponse


def _product_to_response(product: GridProduct) -> GridProductResponse:
    return GridProductResponse(
        item_id=str(product.item_id),
        item_sku=product.item_sku,
        item_name=product.item_name,
        planned_qty=product.planned_qty,
        needed_by=product.needed_by,
        planned_bucket_count=product.planned_bucket_count,
        committed_qty=product.committed_qty,
        on_hand=product.on_hand,
        materials_status=product.materials_status,
        missing_materials=product.missing_materials,
        ready_by=product.ready_by,
        suggested_qty=product.suggested_qty,
        detail=[
            GridDetailLineResponse(
                item_id=line.item_id,
                item_sku=line.item_sku,
                item_name=line.item_name,
                is_manufactured=line.is_manufactured,
                gross_demand=line.gross_demand,
                on_hand=line.on_hand,
                on_order=line.on_order,
                on_draft=line.on_draft,
                net_requirement=line.net_requirement,
                suggested_order_quantity=line.suggested_order_quantity,
                need_by_date=line.need_by_date,
                suggested_order_date=line.suggested_order_date,
            )
            for line in product.detail
        ],
    )


@router.get("/grid", response_model=PlanningGridResponse)
async def get_planning_grid(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The whole planning screen in one read: a row per product with a
    traffic-light material status. See app.services.planning_grid."""
    grid = await compute_planning_grid(db, current.org_id)
    return PlanningGridResponse(
        horizon_start=grid.horizon_start,
        horizon_end=grid.horizon_end,
        products=[_product_to_response(p) for p in grid.products],
        to_buy=[
            GridToBuyResponse(
                item_id=str(row.item_id),
                item_sku=row.item_sku,
                item_name=row.item_name,
                quantity=row.quantity,
                supplier_name=row.supplier_name,
                order_by=row.order_by,
                urgency=row.urgency,
            )
            for row in grid.to_buy
        ],
        summary=GridSummaryResponse(
            missing=sum(1 for p in grid.products if p.materials_status == "missing"),
            arriving=sum(1 for p in grid.products if p.materials_status == "arriving"),
            in_stock=sum(1 for p in grid.products if p.materials_status == "in_stock"),
        ),
    )


class GridPlanRequest(BaseModel):
    quantity: Decimal
    needed_by: date | None = None


@router.put("/grid/{item_id}", response_model=GridProductResponse)
async def set_planned_quantity(
    item_id: uuid.UUID,
    body: GridPlanRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    """Set how many of this product the user plans to make, and when they need
    it. The only write the planning screen makes.

    Owner or manager: planning is a shop-floor supervisor action, deliberately
    broader than the owner-only forecast endpoints in forecasts.py. This
    replaces a whole window of forecasts, so a read-only member typing 0 must
    not be able to wipe an imported forecast. Reading the grid stays open to
    everyone.

    The window holds exactly one number per item: this replaces whatever was
    there rather than adding to it, because the input is a single box the user
    overwrites, not a ledger of buckets. The delete and the insert share one
    transaction so a failed write can never leave the item with no plan at all.
    Quantity 0 means "not making these right now" and clears the item.

    The clear only reaches forecasts inside the current window: a forecast
    imported for a date beyond it survives, and as the window rolls forward
    it can slide back into view, making the item reappear with a plan the
    user believed they had already deleted.
    """
    if body.quantity < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Quantity cannot be negative")
    if body.quantity >= 100_000_000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That quantity is too large. Please enter a smaller number.")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    horizon_start, horizon_end = resolve_grid_window()

    needed_by = body.needed_by
    if body.quantity > 0:
        if needed_by is None:
            # No date typed: reuse whatever date the item is already planned
            # against inside the window, rather than inventing one. Editing
            # the quantity on an already-planned row must not demand a date
            # the user already gave.
            needed_by = (
                await db.execute(
                    select(func.min(DemandForecast.period_start)).where(
                        DemandForecast.org_id == current.org_id,
                        DemandForecast.item_id == item_id,
                        DemandForecast.period_start >= horizon_start,
                        DemandForecast.period_start <= horizon_end,
                    )
                )
            ).scalar_one_or_none()
            if needed_by is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Pick the date you need these by.",
                )
        if not horizon_start <= needed_by <= horizon_end:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Pick a date between {horizon_start} and {horizon_end}",
            )

    deleted = await db.execute(
        delete(DemandForecast).where(
            DemandForecast.org_id == current.org_id,
            DemandForecast.item_id == item_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
    )
    if body.quantity > 0:
        db.add(
            DemandForecast(
                org_id=current.org_id,
                item_id=item_id,
                period_start=needed_by,
                quantity=body.quantity,
                source="planning_grid",
                created_by=current.id,
            )
        )
    await log_event(
        db,
        action="forecast.planned_via_grid",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={
            "quantity": str(body.quantity),
            "needed_by": needed_by.isoformat() if needed_by else None,
            "rows_replaced": deleted.rowcount,
        },
    )
    await db.commit()

    grid = await compute_planning_grid(db, current.org_id)
    for product in grid.products:
        if product.item_id == item_id:
            return _product_to_response(product)
    # Cleared to zero and the item has no BOM, so it is no longer a product row.
    return GridProductResponse(
        item_id=str(item_id),
        item_sku=item.sku,
        item_name=item.name,
        planned_qty=Decimal("0.0000"),
        needed_by=None,
        planned_bucket_count=0,
        committed_qty=Decimal("0.0000"),
        on_hand=Decimal("0.0000"),
        materials_status="in_stock",
        missing_materials=[],
        ready_by=None,
        suggested_qty=None,
        detail=[],
    )
