from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.models.bom import Item
from app.models.forecast import DemandForecast
from app.services.planning_grid import (
    DEFAULT_NEEDED_BY_DAYS,
    GridProduct,
    compute_planning_grid,
    resolve_grid_window,
)

router = APIRouter(prefix="/planning", tags=["planning"])


class GridProductResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    planned_qty: Decimal
    needed_by: date | None
    on_hand: Decimal
    materials_status: str
    missing_materials: list[str]


class GridSummaryResponse(BaseModel):
    missing: int
    arriving: int
    in_stock: int


class PlanningGridResponse(BaseModel):
    horizon_start: date
    horizon_end: date
    products: list[GridProductResponse]
    summary: GridSummaryResponse


def _product_to_response(product: GridProduct) -> GridProductResponse:
    return GridProductResponse(
        item_id=str(product.item_id),
        item_sku=product.item_sku,
        item_name=product.item_name,
        planned_qty=product.planned_qty,
        needed_by=product.needed_by,
        on_hand=product.on_hand,
        materials_status=product.materials_status,
        missing_materials=product.missing_materials,
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
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set how many of this product the user plans to make. Implemented in
    Task 4; this stub writes the forecast row the grid reads back."""
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    horizon_start, horizon_end = resolve_grid_window()
    needed_by = body.needed_by or horizon_start + timedelta(days=DEFAULT_NEEDED_BY_DAYS)
    await db.execute(
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
    await db.commit()
    grid = await compute_planning_grid(db, current.org_id)
    for product in grid.products:
        if product.item_id == item_id:
            return _product_to_response(product)
    return GridProductResponse(
        item_id=str(item_id),
        item_sku=item.sku,
        item_name=item.name,
        planned_qty=Decimal(0),
        needed_by=None,
        on_hand=Decimal(0),
        materials_status="in_stock",
        missing_materials=[],
    )
