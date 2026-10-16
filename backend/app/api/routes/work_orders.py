from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.location import Location
from app.models.lot import LotTransaction
from app.models.operator import Operator
from app.models.work_order import WorkOrder, WorkOrderComponent
from app.services.inventory import apply_inventory_delta
from app.services.locations import resolve_location_id
from app.services.lots import find_or_create_lot
from app.services.work_orders import (
    active_bom_id,
    component_requirements,
)
from app.api.routes.work_orders_helpers import (
    MAX_LINE_QUANTITY,
    WorkOrderCreateRequest,
    WorkOrderFinishRequest,
    WorkOrderUpdateRequest,
    _validate_quantity,
    WorkOrderResponse,
    _wo_to_response,
    _fetch_components_for_orders,
    _detail_response,
    _plain,
    _start_job,
    _over_run_ceiling,
    _return_components,
)

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

# Guards the wo_number max+1 read-then-insert race, same as so_number's twin
# in sales_orders.py: two concurrent creates in one org can read the same max
# and collide on uq_wos_org_number.
MAX_WO_NUMBER_ATTEMPTS = 3
@router.post("", response_model=WorkOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    body: WorkOrderCreateRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    _validate_quantity(body.quantity_planned)

    try:
        item_id = uuid.UUID(body.item_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid item_id") from None
    item = await db.scalar(select(Item).where(Item.org_id == current.org_id, Item.id == item_id))
    if item is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "item not found")

    bom_id = await active_bom_id(db, current.org_id, item_id)
    if bom_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{item.sku} has no active recipe")

    location_id = await resolve_location_id(db, current.org_id, body.location_id)
    requirements = await component_requirements(db, current.org_id, item_id, body.quantity_planned)

    for attempt in range(MAX_WO_NUMBER_ATTEMPTS):
        max_number = await db.scalar(
            select(func.max(WorkOrder.wo_number)).where(WorkOrder.org_id == current.org_id)
        )
        wo = WorkOrder(
            org_id=current.org_id,
            item_id=item_id,
            bom_id=bom_id,
            wo_number=(max_number or 0) + 1,
            status="scheduled",
            quantity_planned=body.quantity_planned,
            location_id=location_id,
            scheduled_start=body.scheduled_start,
            notes=body.notes,
            created_by=current.id,
        )
        # A per-attempt SAVEPOINT, not a full db.rollback(): a whole-transaction
        # rollback would expire the already-loaded `item`, so the `item.sku`
        # read while building the response would trigger an async refresh
        # mid-response (MissingGreenlet).
        try:
            async with db.begin_nested():
                db.add(wo)
                await db.flush()
            break
        except IntegrityError:
            if attempt == MAX_WO_NUMBER_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "could not allocate a job number, please retry"
                ) from None

    for sequence, (component_item_id, quantity_required) in enumerate(requirements):
        db.add(
            WorkOrderComponent(
                org_id=current.org_id,
                wo_id=wo.id,
                item_id=component_item_id,
                quantity_required=quantity_required,
                sequence=sequence,
            )
        )
    await db.flush()

    await log_event(
        db,
        action="work_order.raised",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(wo.id),
        metadata={"wo_number": wo.wo_number, "item_id": str(item_id)},
    )
    if body.start_now:
        await _start_job(db, wo, current)
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, location_id, "Location not found")
    return await _detail_response(db, wo, item, location)


@router.get("", response_model=list[WorkOrderResponse])
async def list_work_orders(
    q: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(WorkOrder, Item, Location)
        .join(Item, Item.id == WorkOrder.item_id)
        .join(Location, Location.id == WorkOrder.location_id)
        .where(WorkOrder.org_id == current.org_id)
    )
    if q:
        query = query.where(
            or_(Item.sku.ilike(f"%{q}%"), cast(WorkOrder.wo_number, String).ilike(f"%{q}%"))
        )
    if status_filter:
        query = query.where(WorkOrder.status == status_filter)
    query = query.order_by(WorkOrder.wo_number).limit(limit).offset(offset)
    rows = (await db.execute(query)).all()

    # The list has to show what each job needs, so components ship with it.
    # One grouped query for the whole page rather than one per row: the panel
    # renders up to `limit` jobs, and a per-row fetch made that many round
    # trips (the defect caught in review on the sales-orders phase).
    components_by_wo = await _fetch_components_for_orders(
        db, current.org_id, [wo.id for wo, _, _ in rows]
    )
    return [
        _wo_to_response(wo, item, location, components_by_wo.get(wo.id, []))
        for wo, item, location in rows
    ]


@router.get("/{wo_id}", response_model=WorkOrderResponse)
async def get_work_order(
    wo_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wo = await get_or_404(db, WorkOrder, current.org_id, wo_id, "Work order not found")
    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)


@router.post("/{wo_id}/start", response_model=WorkOrderResponse)
async def start_work_order(
    wo_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    """Begin the job: its components leave stock now, so what the shop is
    holding and what the system says match while the job is running."""
    # Locked, not just read: two concurrent starts would otherwise both see
    # `scheduled` and both consume.
    wo = await get_or_404(
        db, WorkOrder, current.org_id, wo_id, "Work order not found", for_update=True
    )
    await _start_job(db, wo, current)
    await db.commit()

    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)


@router.post("/{wo_id}/finish", response_model=WorkOrderResponse)
async def finish_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderFinishRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    """Close the job: the good units become stock, the scrap is recorded and
    the components of anything never attempted are accounted for."""
    wo = await get_or_404(
        db, WorkOrder, current.org_id, wo_id, "Work order not found", for_update=True
    )
    if wo.status != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, f"job {wo.wo_number} is {wo.status}")

    good = body.quantity_good
    scrapped = body.quantity_scrapped
    if good < 0 or scrapped < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantities cannot be negative")
    if good >= MAX_LINE_QUANTITY or scrapped >= MAX_LINE_QUANTITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that quantity is too large")
    # Match the Numeric(12, 4) columns so the response reflects what gets
    # stored, not the decimal places the caller happened to send. Same reason
    # the edit route quantizes its quantity.
    good = good.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    scrapped = scrapped.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    total = good + scrapped
    if total > _over_run_ceiling(wo.quantity_planned):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"job {wo.wo_number} was planned for {_plain(wo.quantity_planned)}, "
            "raise a second job for the rest",
        )

    if total < wo.quantity_planned:
        # The app cannot tell whether the unmade units' components are still on
        # the shelf or were used up, and both wrong answers are bad, so the
        # question is asked rather than defaulted.
        if body.remainder_disposition not in ("not_started", "consumed"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"job {wo.wo_number} finished under plan, say whether the unmade units' "
                "components are not_started or consumed",
            )
    elif body.remainder_disposition is not None:
        # Stored only when it means something: on a job that met its plan there
        # is no remainder for it to describe.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "remainder_disposition only applies to a job that finished under plan",
        )

    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    if good > 0:
        if item.is_lot_tracked:
            if not body.lot_number:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"{item.sku} is lot tracked, name the batch"
                )
            lot = await find_or_create_lot(
                db,
                org_id=current.org_id,
                item_id=wo.item_id,
                lot_number=body.lot_number,
                location_id=wo.location_id,
                quantity=good,
                expiry_date=None,
                created_by=current.id,
            )
            db.add(
                LotTransaction(
                    org_id=current.org_id,
                    lot_id=lot.id,
                    transaction_type="receipt",
                    quantity=good,
                    reason="work order",
                    created_by=current.id,
                )
            )
        else:
            await apply_inventory_delta(
                db,
                org_id=current.org_id,
                item_id=wo.item_id,
                location_id=wo.location_id,
                delta=good,
                transaction_type="production",
                created_by=current.id,
                reason="work order",
            )

    if body.remainder_disposition == "not_started":
        # Proportional: a job that made half its units returns half of each
        # remaining component. Right for material cut per unit, wrong for a
        # setup piece consumed whole at the start of the run. Getting that exact
        # needs per-component consumption points, which is operation tracking.
        await _return_components(
            db, wo, current, (wo.quantity_planned - total) / wo.quantity_planned
        )

    wo.quantity_good = good
    wo.quantity_scrapped = scrapped
    wo.scrap_reason = body.scrap_reason
    wo.remainder_disposition = body.remainder_disposition
    wo.status = "done"
    wo.finished_at = datetime.now(timezone.utc)
    await db.flush()

    await log_event(
        db,
        action="work_order.finished",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(wo.id),
        metadata={
            "wo_number": wo.wo_number,
            "quantity_good": str(good),
            "quantity_scrapped": str(scrapped),
            "remainder_disposition": body.remainder_disposition,
        },
    )
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)


@router.post("/{wo_id}/cancel", response_model=WorkOrderResponse)
async def cancel_work_order(
    wo_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    """Call the job off. A scheduled one simply stops being a claim on stock; a
    running one gives its components back."""
    wo = await get_or_404(
        db, WorkOrder, current.org_id, wo_id, "Work order not found", for_update=True
    )
    # Cancelling a running job means nothing was made and every component is
    # back on the shelf. It is defined in terms of the finish path rather than
    # being a second way to move stock, so the two cannot drift. A job where any
    # work actually happened must be finished instead, where the under-run
    # question gets asked properly.
    if wo.status == "in_progress":
        # good and scrap are both zero, so the fraction is 1 and every
        # component comes back in full.
        await _return_components(db, wo, current, Decimal(1))
        wo.finished_at = datetime.now(timezone.utc)
    elif wo.status != "scheduled":
        raise HTTPException(status.HTTP_409_CONFLICT, f"job {wo.wo_number} is {wo.status}")

    wo.status = "cancelled"
    await db.flush()

    await log_event(
        db,
        action="work_order.cancelled",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(wo.id),
        metadata={"wo_number": wo.wo_number},
    )
    await db.commit()
    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)


@router.patch("/{wo_id}", response_model=WorkOrderResponse)
async def update_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderUpdateRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    # Locked like start, finish and cancel are, and for a sharper reason than
    # any of them: this path DELETEs and re-inserts the component rows, so an
    # edit racing a start would reset quantity_consumed to 0 on a job whose
    # stock has already left the shelf, and the later return would put nothing
    # back.
    wo = await get_or_404(
        db, WorkOrder, current.org_id, wo_id, "Work order not found", for_update=True
    )
    supplied = body.model_fields_set
    # True only when assigned_operator_id is the ONE field the caller sent -
    # not just present, since a mixed body (operator + quantity, say) still
    # has to go through the scheduled-only guard for the other fields.
    operator_only_update = "assigned_operator_id" in supplied and not (supplied - {"assigned_operator_id"})
    # Built up as fields are applied and logged exactly once, on whichever path
    # returns: logging inside the operator block AND again at the end wrote two
    # work_order.updated rows for a single mixed PATCH.
    audit_metadata: dict[str, object] = {"wo_number": wo.wo_number}

    # Who's actually running the job is a plan-level fact, not a
    # component/quantity edit - it can change on a job that's already
    # started (that's exactly when you'd correct or set it). But it still
    # doesn't apply once the job is done or cancelled - there's no one
    # "running" it anymore to reassign.
    if "assigned_operator_id" in supplied:
        if wo.status not in ("scheduled", "in_progress"):
            raise HTTPException(status.HTTP_409_CONFLICT, f"job {wo.wo_number} is {wo.status}")
        if body.assigned_operator_id is not None:
            try:
                operator_id = uuid.UUID(body.assigned_operator_id)
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid assigned_operator_id") from None
            await get_or_404(db, Operator, current.org_id, operator_id, "Operator not found")
            wo.assigned_operator_id = operator_id
        else:
            wo.assigned_operator_id = None
        audit_metadata["assigned_operator_id"] = body.assigned_operator_id

    # An operator-only change never touches components/quantity, so it skips
    # the BOM/recipe recompute below entirely - that pipeline has its own
    # failure modes (e.g. no active recipe) that have nothing to do with
    # who's assigned.
    if operator_only_update:
        await log_event(
            db,
            action="work_order.updated",
            org_id=current.org_id,
            actor_user_id=current.id,
            target=str(wo.id),
            metadata=audit_metadata,
        )
        await db.commit()
        await db.refresh(wo)
        item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
        location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
        return await _detail_response(db, wo, item, location)

    if wo.status != "scheduled":
        # A started job's components are already being consumed on the floor;
        # editing the recipe or quantity under it would rewrite what already
        # happened.
        raise HTTPException(status.HTTP_409_CONFLICT, f"job {wo.wo_number} is {wo.status}")

    quantity_planned = wo.quantity_planned
    if body.quantity_planned is not None:
        _validate_quantity(body.quantity_planned)
        # Match the Numeric(12, 4) column so the response reflects what gets
        # stored, not the raw decimal places the caller happened to send.
        quantity_planned = body.quantity_planned.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    # A job reflects the recipe as of its LAST edit, not the one it was
    # raised on: if the BOM was wrong and has since been fixed, editing
    # picks the fix up.
    bom_id = await active_bom_id(db, current.org_id, wo.item_id)
    if bom_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{item.sku} has no active recipe")

    # Which keys the caller SENT, not which came out non-null: null is how the
    # UI clears a date or a note, and reading it as "field omitted" meant
    # clearing a start date silently did nothing, on the very field that drives
    # reservation priority, the horizon bound and the Late badge.
    if body.location_id is not None:
        wo.location_id = await resolve_location_id(db, current.org_id, body.location_id)
    if "scheduled_start" in supplied:
        wo.scheduled_start = body.scheduled_start
    if "notes" in supplied:
        wo.notes = body.notes
    wo.quantity_planned = quantity_planned
    wo.bom_id = bom_id

    # Delete and re-insert every time, even when only the date changed: it is
    # one query either way, and a conditional here is exactly how the stored
    # components and the recipe drift apart.
    requirements = await component_requirements(db, current.org_id, wo.item_id, quantity_planned)
    await db.execute(
        WorkOrderComponent.__table__.delete().where(WorkOrderComponent.wo_id == wo.id)
    )
    for sequence, (component_item_id, quantity_required) in enumerate(requirements):
        db.add(
            WorkOrderComponent(
                org_id=current.org_id,
                wo_id=wo.id,
                item_id=component_item_id,
                quantity_required=quantity_required,
                sequence=sequence,
            )
        )

    # Child rows changing alone never fires onupdate on the parent; touch it
    # so updated_at still means "last changed".
    wo.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await log_event(
        db,
        action="work_order.updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(wo.id),
        metadata=audit_metadata,
    )
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)
