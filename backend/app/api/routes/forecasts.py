from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.forecast import DemandForecast
from app.models.lot import Lot
from app.models.procurement import PurchaseOrder, PurchaseOrderLine

# A repeat import of the same period is an upsert, so the only real collision is
# two *first* inserts of the same (item, period) racing. One retry after
# re-selecting is enough - by then the winning row is committed and visible.
MAX_FORECAST_UPSERT_ATTEMPTS = 3

# Open-PO supply comes only from POs a supplier has actually been told to ship.
# A draft PO is still being edited (lines can still change or it can be
# cancelled outright), so counting it as on-order would let a never-submitted
# draft silently suppress a real replenishment suggestion.
OPEN_PO_STATUSES = ("submitted", "partially_received")

router = APIRouter(prefix="/forecasts", tags=["forecasts"])
mrp_router = APIRouter(prefix="/mrp", tags=["mrp"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ForecastResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
    period_start: date
    quantity: Decimal
    source: str
    notes: str | None
    updated_at: datetime


class ForecastUpsertRequest(BaseModel):
    item_id: str
    period_start: date
    quantity: Decimal
    notes: str | None = None


class ForecastImportRow(BaseModel):
    item_id: str
    period_start: date
    quantity: Decimal
    notes: str | None = None


class ForecastImportResponse(BaseModel):
    created: int
    updated: int
    total: int


class MrpPlanLine(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    # False for non-lot-tracked items: on_hand is reported as 0 but that's
    # "not tracked", not "confirmed empty" - the frontend must not read a 0
    # here as real stock-out. See run_mrp_plan for why.
    on_hand_tracked: bool
    gross_demand: Decimal
    on_hand: Decimal
    on_order: Decimal
    net_requirement: Decimal
    suggested_order_quantity: Decimal


class MrpPlanResponse(BaseModel):
    horizon_start: date
    horizon_end: date
    lines: list[MrpPlanLine]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _to_response(fc: DemandForecast, item: Item) -> ForecastResponse:
    return ForecastResponse(
        id=str(fc.id),
        item_id=str(fc.item_id),
        item_sku=item.sku,
        item_name=item.name,
        period_start=fc.period_start,
        quantity=fc.quantity,
        source=fc.source,
        notes=fc.notes,
        updated_at=fc.updated_at,
    )


async def _get_item_or_404(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> Item:
    result = await db.execute(select(Item).where(Item.id == item_id, Item.org_id == org_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


def _parse_item_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid item_id: {raw}") from None


async def _upsert_forecast(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    period_start: date,
    quantity: Decimal,
    notes: str | None,
    source: str,
    created_by: uuid.UUID,
) -> tuple[DemandForecast, bool]:
    """Set-not-increment upsert on (org, item, period). Returns (row, created).

    A forecast import *overwrites* the number for a period (re-running the
    desktop forecaster should replace the stale value, never add to it), so
    unlike lot top-up there's no read-modify-write increment to lose - a
    concurrent double-import of the same period is a last-writer-wins overwrite,
    which is the intended semantics. The only race we guard is two *first*
    inserts of the same period colliding on the unique constraint, handled the
    same way _find_or_create_lot handles a concurrent lot creation: a per-row
    SAVEPOINT so a rollback here can't discard the other rows of a bulk import.
    """
    existing = (
        await db.execute(
            select(DemandForecast).where(
                DemandForecast.org_id == org_id,
                DemandForecast.item_id == item_id,
                DemandForecast.period_start == period_start,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.quantity = quantity
        existing.notes = notes
        existing.source = source
        await db.flush()
        return existing, False

    for attempt in range(MAX_FORECAST_UPSERT_ATTEMPTS):
        try:
            async with db.begin_nested():
                fc = DemandForecast(
                    org_id=org_id,
                    item_id=item_id,
                    period_start=period_start,
                    quantity=quantity,
                    notes=notes,
                    source=source,
                    created_by=created_by,
                )
                db.add(fc)
                await db.flush()
            return fc, True
        except IntegrityError:
            # Savepoint rolled back automatically; the rest of a bulk import's
            # pending rows are untouched. Someone inserted this period
            # concurrently - overwrite it instead of erroring.
            existing = (
                await db.execute(
                    select(DemandForecast).where(
                        DemandForecast.org_id == org_id,
                        DemandForecast.item_id == item_id,
                        DemandForecast.period_start == period_start,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.quantity = quantity
                existing.notes = notes
                existing.source = source
                await db.flush()
                return existing, False
            if attempt == MAX_FORECAST_UPSERT_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Could not save forecast, please retry",
                ) from None
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- #
# Forecast CRUD
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[ForecastResponse])
async def list_forecasts(
    item_id: str | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(DemandForecast, Item)
        .join(Item, Item.id == DemandForecast.item_id)
        .where(DemandForecast.org_id == current.org_id)
    )
    if item_id:
        query = query.where(DemandForecast.item_id == _parse_item_id(item_id))
    result = await db.execute(query.order_by(Item.sku, DemandForecast.period_start))
    return [_to_response(fc, item) for fc, item in result.all()]


@router.post("", response_model=ForecastResponse)
async def upsert_forecast(
    body: ForecastUpsertRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be non-negative")
    item = await _get_item_or_404(db, current.org_id, _parse_item_id(body.item_id))

    fc, created = await _upsert_forecast(
        db,
        org_id=current.org_id,
        item_id=item.id,
        period_start=body.period_start,
        quantity=body.quantity,
        notes=body.notes,
        source="manual",
        created_by=current.id,
    )
    await log_event(
        db,
        action="forecast.upserted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(fc.id),
        metadata={
            "item_id": str(item.id),
            "period_start": body.period_start.isoformat(),
            "quantity": str(body.quantity),
            "created": created,
        },
    )
    await db.commit()
    return _to_response(fc, item)


@router.post("/import", response_model=ForecastImportResponse)
async def import_forecasts(
    rows: list[ForecastImportRow],
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk upsert, source stamped "imported" - the integration point for the
    desktop forecaster's output. Whole request is atomic: any bad row (unknown
    item, negative quantity) rejects the entire batch so a partial import can't
    leave a half-refreshed plan.
    """
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No forecast rows provided")

    # Validate every row up front, before any write, so validation failure
    # can't leave some rows already upserted.
    parsed: list[tuple[uuid.UUID, date, Decimal, str | None]] = []
    item_ids: set[uuid.UUID] = set()
    for row in rows:
        if row.quantity < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"quantity must be non-negative (item {row.item_id})"
            )
        iid = _parse_item_id(row.item_id)
        item_ids.add(iid)
        parsed.append((iid, row.period_start, row.quantity, row.notes))

    # One query to confirm every referenced item belongs to this org.
    found = (
        await db.execute(select(Item.id).where(Item.org_id == current.org_id, Item.id.in_(item_ids)))
    ).scalars().all()
    missing = item_ids - set(found)
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown item(s) for this org: {', '.join(str(m) for m in missing)}"
        )

    created = updated = 0
    for iid, period_start, quantity, notes in parsed:
        _, was_created = await _upsert_forecast(
            db,
            org_id=current.org_id,
            item_id=iid,
            period_start=period_start,
            quantity=quantity,
            notes=notes,
            source="imported",
            created_by=current.id,
        )
        if was_created:
            created += 1
        else:
            updated += 1

    await log_event(
        db,
        action="forecast.imported",
        org_id=current.org_id,
        actor_user_id=current.id,
        metadata={"created": created, "updated": updated, "total": len(parsed)},
    )
    await db.commit()
    return ForecastImportResponse(created=created, updated=updated, total=len(parsed))


@router.delete("/{forecast_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_forecast(
    forecast_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    fc = (
        await db.execute(
            select(DemandForecast).where(
                DemandForecast.id == forecast_id, DemandForecast.org_id == current.org_id
            )
        )
    ).scalar_one_or_none()
    if fc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Forecast not found")
    await db.delete(fc)
    await log_event(
        db,
        action="forecast.deleted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(forecast_id),
    )
    await db.commit()


# --------------------------------------------------------------------------- #
# MRP netting
# --------------------------------------------------------------------------- #
async def _sum_by_item(db: AsyncSession, query) -> dict[uuid.UUID, Decimal]:
    result = await db.execute(query)
    return {item_id: (total or Decimal(0)) for item_id, total in result.all()}


@mrp_router.get("/plan", response_model=MrpPlanResponse)
async def run_mrp_plan(
    horizon_start: date | None = None,
    horizon_end: date | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Requirements netting over a demand horizon. For every item with forecast
    demand in the window:

        net_requirement = max(0, gross_demand - on_hand - on_order)

    - gross_demand: sum of demand_forecasts buckets whose period_start is in
      [horizon_start, horizon_end].
    - on_hand: sum of *active* lot quantity_remaining. on_hold lots (QA
      hold/recall) are deliberately excluded - they can't be consumed, so
      counting them as available would understate a real shortage. Non-lot-
      tracked items have no on-hand ledger in the system yet, so their on_hand
      is 0 with on_hand_tracked=False (a known gap, surfaced honestly rather
      than faked).
    - on_order: open PO supply, quantity_ordered - quantity_received across PO
      lines on submitted/partially_received POs.

    Read-only and demand-driven: items with supply but no forecast demand in the
    window aren't planning actions, so they're omitted. Lot sizing / MOQ /
    supplier lead-time offset are intentionally not modelled yet - suggested
    order quantity is the raw net requirement.
    """
    today = datetime.now(timezone.utc).date()
    if horizon_start is None:
        horizon_start = today
    if horizon_end is None:
        horizon_end = today + timedelta(days=90)
    if horizon_end < horizon_start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_end must not be before horizon_start")

    org_id = current.org_id

    demand = await _sum_by_item(
        db,
        select(DemandForecast.item_id, func.sum(DemandForecast.quantity))
        .where(
            DemandForecast.org_id == org_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
        .group_by(DemandForecast.item_id),
    )
    if not demand:
        return MrpPlanResponse(horizon_start=horizon_start, horizon_end=horizon_end, lines=[])

    on_hand = await _sum_by_item(
        db,
        select(Lot.item_id, func.sum(Lot.quantity_remaining))
        .where(Lot.org_id == org_id, Lot.status == "active")
        .group_by(Lot.item_id),
    )
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

    items = {
        item.id: item
        for item in (
            await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(demand.keys())))
        )
        .scalars()
        .all()
    }

    lines: list[MrpPlanLine] = []
    for item_id, gross in demand.items():
        item = items.get(item_id)
        if item is None:
            # Forecast referencing a deleted item - skip rather than 500.
            continue
        hand = on_hand.get(item_id, Decimal(0))
        # Clamp: a fully-received line contributes 0; the aggregate can't
        # legitimately go negative, but guard against it rather than letting a
        # data quirk manufacture phantom supply.
        order = max(Decimal(0), on_order.get(item_id, Decimal(0)))
        net = max(Decimal(0), gross - hand - order)
        lines.append(
            MrpPlanLine(
                item_id=str(item_id),
                item_sku=item.sku,
                item_name=item.name,
                on_hand_tracked=item.is_lot_tracked,
                gross_demand=gross,
                on_hand=hand,
                on_order=order,
                net_requirement=net,
                suggested_order_quantity=net,
            )
        )

    # Most urgent first (biggest shortfall), then a stable sku tiebreak.
    lines.sort(key=lambda ln: (-ln.net_requirement, ln.item_sku))
    return MrpPlanResponse(horizon_start=horizon_start, horizon_end=horizon_end, lines=lines)
