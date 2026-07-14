from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.forecast import DemandForecast
from app.models.procurement import PurchaseOrder, Supplier, PurchaseOrderLine
from app.services.demand_forecasting import DEFAULT_HORIZON_DAYS, compute_demand_forecast
from app.services.mrp import MrpPlanLine, compute_build_plan, compute_mrp_plan, resolve_horizon
from app.services.pricing import latest_unit_prices

# A repeat import of the same period is an upsert, so the only real collision is
# two *first* inserts of the same (item, period) racing. One retry after
# re-selecting is enough - by then the winning row is committed and visible.
MAX_FORECAST_UPSERT_ATTEMPTS = 3

# Same po_number max+1 read-then-insert race guard as create_purchase_order -
# generate-po allocates a po_number the same way.
MAX_PO_NUMBER_ATTEMPTS = 3

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


class SuggestedForecastPoint(BaseModel):
    period_start: date
    quantity: Decimal
    lower: Decimal | None
    upper: Decimal | None


class SuggestedForecastResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    # moving_avg | exp_smoothing | no_data - see services.demand_forecasting.
    method_used: str
    # Non-zero consumption days found in the lookback window - a rough
    # confidence signal for how much this projection should be trusted.
    history_points: int
    points: list[SuggestedForecastPoint]


class MrpPlanResponse(BaseModel):
    horizon_start: date
    horizon_end: date
    lines: list[MrpPlanLine]


class GeneratePoRequest(BaseModel):
    supplier_id: str
    # Which shortfall items to put on this supplier's PO. Since items have no
    # stored source-of-supply yet, the caller chooses the supplier and the
    # subset of items - one call makes one draft PO for one supplier.
    item_ids: list[str]
    horizon_start: date | None = None
    horizon_end: date | None = None


class GeneratePoLine(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    quantity_ordered: Decimal
    unit_price: Decimal


class GeneratePoResponse(BaseModel):
    po_id: str
    po_number: int
    supplier_id: str
    supplier_name: str
    lines: list[GeneratePoLine]


class GeneratePosRequest(BaseModel):
    horizon_start: date | None = None
    horizon_end: date | None = None


class GeneratedPoSummary(BaseModel):
    po_id: str
    po_number: int
    supplier_id: str
    supplier_name: str
    line_count: int


class UnassignedItem(BaseModel):
    # A purchased shortfall with no preferred supplier - nothing to auto-order it
    # against, so it's reported for the user to assign a supplier (or use the
    # manual generate-po).
    item_id: str
    item_sku: str
    item_name: str
    net_requirement: Decimal


class GeneratePosResponse(BaseModel):
    created: list[GeneratedPoSummary]
    unassigned: list[UnassignedItem]


class BuildPlanRequest(BaseModel):
    item_id: str
    build_quantity: Decimal
    yield_loss_pct: Decimal = Decimal(0)


class BuildPlanResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    build_quantity: Decimal
    yield_loss_pct: Decimal
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
    item = await get_or_404(db, Item, current.org_id, _parse_item_id(body.item_id), "Item not found")

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


@router.get("/suggested/{item_id}", response_model=SuggestedForecastResponse)
async def get_suggested_forecast(
    item_id: uuid.UUID,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read-only, data-driven demand projection for one item (moving average
    or exponential smoothing over its consumption history - see
    services.demand_forecasting), for review before turning it into an actual
    forecast via POST /forecasts. Never written anywhere itself."""
    if horizon_days <= 0 or horizon_days > 365:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_days must be between 1 and 365")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    result = await compute_demand_forecast(db, current.org_id, item_id, horizon_days=horizon_days)
    return SuggestedForecastResponse(
        item_id=str(item.id),
        item_sku=item.sku,
        item_name=item.name,
        method_used=result.method_used,
        history_points=result.history_points,
        points=[
            SuggestedForecastPoint(
                period_start=p.period_start,
                quantity=Decimal(str(round(p.yhat, 4))),
                lower=Decimal(str(round(p.lower, 4))) if p.lower is not None else None,
                upper=Decimal(str(round(p.upper, 4))) if p.upper is not None else None,
            )
            for p in result.points
        ],
    )


# --------------------------------------------------------------------------- #
# MRP netting (see app.services.mrp)
# --------------------------------------------------------------------------- #
@mrp_router.get("/plan", response_model=MrpPlanResponse)
async def run_mrp_plan(
    horizon_start: date | None = None,
    horizon_end: date | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read-only MRP plan over a demand horizon. See services.mrp.compute_mrp_plan
    for the netting/explosion model."""
    hs, he = resolve_horizon(horizon_start, horizon_end)
    lines = await compute_mrp_plan(db, current.org_id, hs, he)
    return MrpPlanResponse(horizon_start=hs, horizon_end=he, lines=lines)


async def _new_draft_po(
    db: AsyncSession, org_id: uuid.UUID, supplier: Supplier, created_by: uuid.UUID
) -> PurchaseOrder:
    """Allocate a po_number and create a draft PO, guarded against the max+1
    read-then-insert race. Uses a per-attempt SAVEPOINT (not a plain rollback)
    so it's safe to call repeatedly within one request - generate_pos creates a
    PO per supplier, and a plain rollback on the 2nd would discard the 1st.
    """
    expected_delivery_date = (
        date.today() + timedelta(days=supplier.lead_time_days)
        if supplier.lead_time_days is not None
        else None
    )
    for attempt in range(MAX_PO_NUMBER_ATTEMPTS):
        max_number = await db.scalar(
            select(func.max(PurchaseOrder.po_number)).where(PurchaseOrder.org_id == org_id)
        )
        po = PurchaseOrder(
            org_id=org_id,
            supplier_id=supplier.id,
            po_number=(max_number or 0) + 1,
            status="draft",
            expected_delivery_date=expected_delivery_date,
            created_by=created_by,
        )
        try:
            async with db.begin_nested():
                db.add(po)
                await db.flush()
            return po
        except IntegrityError:
            if attempt == MAX_PO_NUMBER_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Could not allocate a PO number, please retry"
                ) from None
    raise AssertionError("unreachable")


async def _add_po_lines(
    db: AsyncSession,
    org_id: uuid.UUID,
    po: PurchaseOrder,
    chosen: list[MrpPlanLine],
    prices: dict[uuid.UUID, Decimal],
) -> list[GeneratePoLine]:
    """Add one draft PO line per chosen plan line, at the suggested order
    quantity (net requirement, rounded up to the item's MOQ if it has one) and
    last-known price, and return the response view of them."""
    response_lines: list[GeneratePoLine] = []
    for seq, line in enumerate(chosen):
        item_uuid = uuid.UUID(line.item_id)
        unit_price = prices.get(item_uuid, Decimal(0))
        db.add(
            PurchaseOrderLine(
                org_id=org_id,
                po_id=po.id,
                item_id=item_uuid,
                quantity_ordered=line.suggested_order_quantity,
                unit_price=unit_price,
                sequence=seq,
            )
        )
        response_lines.append(
            GeneratePoLine(
                item_id=line.item_id,
                item_sku=line.item_sku,
                item_name=line.item_name,
                quantity_ordered=line.suggested_order_quantity,
                unit_price=unit_price,
            )
        )
    return response_lines


@mrp_router.post("/generate-po", response_model=GeneratePoResponse)
async def generate_po(
    body: GeneratePoRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Turn selected plan shortfalls into a draft purchase order for one
    supplier. Creates a *draft* (not submitted) PO so the buyer reviews
    quantities and prices in the normal PO editor before committing. The draft's
    outstanding quantity counts as on_draft supply in the plan, so it offsets
    the net requirement immediately - re-running the plan or generation won't
    re-order what's already sitting in a draft. Only purchased items with a real
    net requirement are orderable; manufactured items are a build, not a buy.
    """
    hs, he = resolve_horizon(body.horizon_start, body.horizon_end)
    org_id = current.org_id

    try:
        supplier_id = uuid.UUID(body.supplier_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid supplier_id") from None
    supplier = (
        await db.execute(select(Supplier).where(Supplier.id == supplier_id, Supplier.org_id == org_id))
    ).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")

    if not body.item_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select at least one item to order")

    plan_by_item = {line.item_id: line for line in await compute_mrp_plan(db, org_id, hs, he)}

    # Validate every requested item up front so a bad one rejects the whole
    # request rather than leaving a half-built PO.
    chosen: list[MrpPlanLine] = []
    seen: set[str] = set()
    for raw in body.item_ids:
        try:
            item_uuid = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid item_id: {raw}") from None
        key = str(item_uuid)
        if key in seen:
            continue
        seen.add(key)
        line = plan_by_item.get(key)
        if line is None or line.net_requirement <= 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Item {raw} has no net requirement in this horizon - nothing to order",
            )
        if line.is_manufactured:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Item {line.item_sku} is manufactured (build it, don't order it)",
            )
        chosen.append(line)

    prices = await latest_unit_prices(db, org_id, {uuid.UUID(line.item_id) for line in chosen})
    po = await _new_draft_po(db, org_id, supplier, current.id)
    response_lines = await _add_po_lines(db, org_id, po, chosen, prices)
    await db.flush()

    await log_event(
        db,
        action="po.generated_from_plan",
        org_id=org_id,
        actor_user_id=current.id,
        target=str(po.id),
        metadata={"supplier_id": str(supplier_id), "po_number": po.po_number, "line_count": len(chosen)},
    )
    await db.commit()
    return GeneratePoResponse(
        po_id=str(po.id),
        po_number=po.po_number,
        supplier_id=str(supplier_id),
        supplier_name=supplier.name,
        lines=response_lines,
    )


async def _create_pos_grouped_by_supplier(
    db: AsyncSession,
    org_id: uuid.UUID,
    current: CurrentUser,
    orderable: list[MrpPlanLine],
    audit_action: str,
) -> GeneratePosResponse:
    """Shared by generate_pos (forecast-driven MRP plan) and
    generate_pos_from_build_plan (build-quantity-driven): group purchased
    shortfalls by preferred supplier and create one draft PO per supplier.
    Items with no preferred supplier can't be routed automatically and come
    back as `unassigned`. audit_action distinguishes the two call sites in
    the audit log.
    """
    if not orderable:
        return GeneratePosResponse(created=[], unassigned=[])

    item_uuids = {uuid.UUID(line.item_id) for line in orderable}
    preferred = {
        item_id: supplier_id
        for item_id, supplier_id in (
            await db.execute(
                select(Item.id, Item.preferred_supplier_id).where(
                    Item.org_id == org_id, Item.id.in_(item_uuids)
                )
            )
        ).all()
    }

    groups: dict[uuid.UUID, list[MrpPlanLine]] = defaultdict(list)
    unassigned: list[UnassignedItem] = []
    for line in orderable:
        supplier_id = preferred.get(uuid.UUID(line.item_id))
        if supplier_id is None:
            unassigned.append(
                UnassignedItem(
                    item_id=line.item_id,
                    item_sku=line.item_sku,
                    item_name=line.item_name,
                    net_requirement=line.net_requirement,
                )
            )
        else:
            groups[supplier_id].append(line)

    prices = await latest_unit_prices(db, org_id, item_uuids)
    suppliers = {
        s.id: s
        for s in (
            await db.execute(select(Supplier).where(Supplier.org_id == org_id, Supplier.id.in_(groups)))
        )
        .scalars()
        .all()
    }

    # Defensive: a preferred_supplier_id always references a live org supplier
    # (validated when set, FK with ondelete SET NULL on supplier deletion), so
    # every group key should resolve. But if a supplier row is somehow missing,
    # route its items to `unassigned` rather than KeyError into a 500.
    for supplier_id in [sid for sid in groups if sid not in suppliers]:
        for line in groups.pop(supplier_id):
            unassigned.append(
                UnassignedItem(
                    item_id=line.item_id,
                    item_sku=line.item_sku,
                    item_name=line.item_name,
                    net_requirement=line.net_requirement,
                )
            )

    created: list[GeneratedPoSummary] = []
    # Deterministic order (by supplier name) so po_number allocation is stable.
    for supplier_id in sorted(groups, key=lambda sid: suppliers[sid].name):
        group_lines = groups[supplier_id]
        po = await _new_draft_po(db, org_id, suppliers[supplier_id], current.id)
        await _add_po_lines(db, org_id, po, group_lines, prices)
        created.append(
            GeneratedPoSummary(
                po_id=str(po.id),
                po_number=po.po_number,
                supplier_id=str(supplier_id),
                supplier_name=suppliers[supplier_id].name,
                line_count=len(group_lines),
            )
        )

    if created:
        await log_event(
            db,
            action=audit_action,
            org_id=org_id,
            actor_user_id=current.id,
            metadata={"po_count": len(created), "unassigned_count": len(unassigned)},
        )
    await db.commit()
    return GeneratePosResponse(created=created, unassigned=unassigned)


@mrp_router.post("/generate-pos", response_model=GeneratePosResponse)
async def generate_pos(
    body: GeneratePosRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Automatic PO generation: take every purchased shortfall in the plan,
    group it by the item's preferred supplier, and create one draft PO per
    supplier. Purchased shortfalls with no preferred supplier can't be routed,
    so they're returned as `unassigned` for the user to assign a supplier (or
    order manually via generate-po). Manufactured items are a build action and
    are never ordered. Like generate-po, the POs are drafts - review and submit
    them in the PO editor.
    """
    hs, he = resolve_horizon(body.horizon_start, body.horizon_end)
    org_id = current.org_id

    orderable = [
        line
        for line in await compute_mrp_plan(db, org_id, hs, he)
        if not line.is_manufactured and line.net_requirement > 0
    ]
    return await _create_pos_grouped_by_supplier(
        db, org_id, current, orderable, "po.auto_generated_from_plan"
    )


@mrp_router.post("/build-plan", response_model=BuildPlanResponse)
async def run_build_plan(
    body: BuildPlanRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read-only build calculator: given a quantity to build and an optional
    expected yield loss, explode the item's active BOM (netted against real
    stock) into what needs to be bought. See services.mrp.compute_build_plan.
    """
    item_uuid = _parse_item_id(body.item_id)
    item = await get_or_404(db, Item, current.org_id, item_uuid, "Item not found")
    lines = await compute_build_plan(
        db, current.org_id, item_uuid, body.build_quantity, body.yield_loss_pct
    )
    return BuildPlanResponse(
        item_id=str(item.id),
        item_sku=item.sku,
        item_name=item.name,
        build_quantity=body.build_quantity,
        yield_loss_pct=body.yield_loss_pct,
        lines=lines,
    )


@mrp_router.post("/build-plan/generate-pos", response_model=GeneratePosResponse)
async def generate_pos_from_build_plan(
    body: BuildPlanRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Commit a build calculation into draft POs, grouped by preferred
    supplier - the same grouping generate_pos uses. Recomputes the plan
    server-side from body rather than trusting any client-submitted
    quantities, matching generate_po/generate_pos.
    """
    item_uuid = _parse_item_id(body.item_id)
    await get_or_404(db, Item, current.org_id, item_uuid, "Item not found")
    lines = await compute_build_plan(
        db, current.org_id, item_uuid, body.build_quantity, body.yield_loss_pct
    )
    orderable = [line for line in lines if not line.is_manufactured and line.net_requirement > 0]
    return await _create_pos_grouped_by_supplier(
        db, current.org_id, current, orderable, "po.generated_from_build_plan"
    )
