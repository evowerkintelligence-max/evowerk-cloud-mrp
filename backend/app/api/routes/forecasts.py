from __future__ import annotations

import uuid
from collections import defaultdict, deque
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
from app.models.bom import Bom, BomLine, Item
from app.models.forecast import DemandForecast
from app.models.inventory import InventoryBalance
from app.models.lot import Lot
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier

# A repeat import of the same period is an upsert, so the only real collision is
# two *first* inserts of the same (item, period) racing. One retry after
# re-selecting is enough - by then the winning row is committed and visible.
MAX_FORECAST_UPSERT_ATTEMPTS = 3

# Open-PO supply comes only from POs a supplier has actually been told to ship.
# A draft PO is still being edited (lines can still change or it can be
# cancelled outright), so counting it as on-order would let a never-submitted
# draft silently suppress a real replenishment suggestion.
OPEN_PO_STATUSES = ("submitted", "partially_received")

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


class MrpPlanLine(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    # True if this item is produced by an active BOM: its net requirement is a
    # *build* suggestion (make it), not a *buy* one (order it). The frontend
    # labels the two differently.
    is_manufactured: bool
    # Demand this item was forecast for directly (independent demand).
    independent_demand: Decimal
    # Demand exploded onto this item from parent assemblies' net requirements
    # (dependent demand). gross_demand = independent_demand + dependent_demand.
    dependent_demand: Decimal
    gross_demand: Decimal
    on_hand: Decimal
    on_order: Decimal
    net_requirement: Decimal
    suggested_order_quantity: Decimal


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


async def _load_bom_children(
    db: AsyncSession, org_id: uuid.UUID
) -> dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]]:
    """Manufacturing graph over ACTIVE BOMs only: produced item_id -> list of
    (component_item_id, quantity_per_unit). Draft/archived BOMs are ignored -
    only the currently-active BOM defines how an item is really built, and
    activation already enforces one-active-per-item and acyclicity.
    """
    rows = await db.execute(
        select(Bom.item_id, BomLine.component_item_id, BomLine.quantity)
        .join(BomLine, BomLine.bom_id == Bom.id)
        .where(Bom.org_id == org_id, Bom.status == "active")
    )
    children: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]] = defaultdict(list)
    for parent_id, child_id, qty in rows.all():
        children[parent_id].append((child_id, qty))
    return children


def _resolve_horizon(horizon_start: date | None, horizon_end: date | None) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    hs = horizon_start if horizon_start is not None else today
    he = horizon_end if horizon_end is not None else today + timedelta(days=90)
    if he < hs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_end must not be before horizon_start")
    return hs, he


async def _compute_mrp_plan(
    db: AsyncSession, org_id: uuid.UUID, horizon_start: date, horizon_end: date
) -> list[MrpPlanLine]:
    """Material Requirements Planning: net demand against supply, exploding
    forecasts through active BOMs so a forecast of a finished good becomes
    concrete requirements for the components it's built from.

        net_requirement = max(0, gross_demand - on_hand - on_order)
        gross_demand    = independent_demand + dependent_demand

    - independent_demand: the item's own forecast buckets whose period_start is
      in [horizon_start, horizon_end].
    - dependent_demand: quantity_per x parent net_requirement, summed over every
      parent assembly that lists this item in its active BOM.
    - on_hand: for lot-tracked items, sum of *active* lot quantity_remaining
      (on_hold lots - QA hold/recall - are excluded; they can't be consumed, so
      counting them would understate a real shortage). For non-lot-tracked
      items, the general inventory_balances on-hand. The two are disjoint (an
      item is one or the other), so summing them is just "whichever applies".
    - on_order: open PO supply, quantity_ordered - quantity_received across PO
      lines on submitted/partially_received POs.

    This is *netted* explosion, not gross: each assembly is netted against its
    own on-hand/on-order first, and only the resulting net requirement explodes
    to its components. Finished-good and sub-assembly stock therefore correctly
    reduces how much of each raw component you actually need to buy. Items are
    processed parents-before-components (topological order over the active-BOM
    DAG) so an assembly's net is final before it explodes downward.

    Demand-driven: items reached by neither a forecast nor an explosion aren't
    planning actions, so they're omitted. Lot sizing / MOQ / lead-time offset /
    time-phasing per bucket are intentionally not modelled yet - suggested order
    quantity is the raw net requirement.
    """
    independent = await _sum_by_item(
        db,
        select(DemandForecast.item_id, func.sum(DemandForecast.quantity))
        .where(
            DemandForecast.org_id == org_id,
            DemandForecast.period_start >= horizon_start,
            DemandForecast.period_start <= horizon_end,
        )
        .group_by(DemandForecast.item_id),
    )
    if not independent:
        return []

    # On-hand comes from two disjoint sources: active lots (lot-tracked items)
    # and inventory_balances (everything else). Merge into one item->qty map.
    on_hand = await _sum_by_item(
        db,
        select(Lot.item_id, func.sum(Lot.quantity_remaining))
        .where(Lot.org_id == org_id, Lot.status == "active")
        .group_by(Lot.item_id),
    )
    inventory_on_hand = await _sum_by_item(
        db,
        select(InventoryBalance.item_id, InventoryBalance.quantity_on_hand).where(
            InventoryBalance.org_id == org_id
        ),
    )
    for item_id, qty in inventory_on_hand.items():
        on_hand[item_id] = on_hand.get(item_id, Decimal(0)) + qty
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
    children = await _load_bom_children(db, org_id)

    # Every item touched by the plan: the forecast items plus everything
    # reachable from them through the manufacturing graph.
    relevant: set[uuid.UUID] = set()
    stack = list(independent.keys())
    while stack:
        node = stack.pop()
        if node in relevant:
            continue
        relevant.add(node)
        for child_id, _ in children.get(node, []):
            stack.append(child_id)

    # Topological order, parents before components (Kahn). Active BOMs are
    # acyclic (enforced at activation), so this consumes every relevant node.
    indegree: dict[uuid.UUID, int] = dict.fromkeys(relevant, 0)
    for parent_id in relevant:
        for child_id, _ in children.get(parent_id, []):
            if child_id in relevant:
                indegree[child_id] += 1
    queue = deque(node for node in relevant if indegree[node] == 0)
    order: list[uuid.UUID] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for child_id, _ in children.get(node, []):
            if child_id in relevant:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    queue.append(child_id)
    if len(order) != len(relevant):
        # A cycle slipped past activation's guard (shouldn't happen). Surface
        # the leftovers un-exploded rather than dropping them or looping.
        order.extend(node for node in relevant if node not in set(order))

    items = {
        item.id: item
        for item in (await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(relevant))))
        .scalars()
        .all()
    }

    gross: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for item_id, qty in independent.items():
        gross[item_id] += qty

    lines: list[MrpPlanLine] = []
    for item_id in order:
        item = items.get(item_id)
        if item is None:
            # BOM/forecast referencing a deleted item - skip rather than 500.
            continue
        total_gross = gross[item_id]
        hand = on_hand.get(item_id, Decimal(0))
        # Clamp: a fully-received line contributes 0; the aggregate can't
        # legitimately go negative, but guard against a data quirk manufacturing
        # phantom supply.
        order_qty = max(Decimal(0), on_order.get(item_id, Decimal(0)))
        net = max(Decimal(0), total_gross - hand - order_qty)

        # Explode the NET (not gross) requirement onto components. Processing in
        # topological order guarantees total_gross above is already final.
        for child_id, qty_per in children.get(item_id, []):
            gross[child_id] += net * qty_per

        if total_gross <= 0:
            # Reached but not actually required (e.g. a component of an assembly
            # fully covered by stock) - nothing to plan.
            continue
        indep = independent.get(item_id, Decimal(0))
        lines.append(
            MrpPlanLine(
                item_id=str(item_id),
                item_sku=item.sku,
                item_name=item.name,
                is_manufactured=item_id in children,
                independent_demand=indep,
                dependent_demand=total_gross - indep,
                gross_demand=total_gross,
                on_hand=hand,
                on_order=order_qty,
                net_requirement=net,
                suggested_order_quantity=net,
            )
        )

    # Most urgent first (biggest shortfall), then a stable sku tiebreak.
    lines.sort(key=lambda ln: (-ln.net_requirement, ln.item_sku))
    return lines


@mrp_router.get("/plan", response_model=MrpPlanResponse)
async def run_mrp_plan(
    horizon_start: date | None = None,
    horizon_end: date | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read-only MRP plan over a demand horizon. See _compute_mrp_plan for the
    netting/explosion model."""
    hs, he = _resolve_horizon(horizon_start, horizon_end)
    lines = await _compute_mrp_plan(db, current.org_id, hs, he)
    return MrpPlanResponse(horizon_start=hs, horizon_end=he, lines=lines)


async def _latest_unit_prices(
    db: AsyncSession, org_id: uuid.UUID, item_ids: set[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Most recent negotiated unit_price per item across all its PO lines, to
    prefill a suggested draft (the buyer edits before submitting). PO lines have
    no timestamp of their own, so 'most recent' is by the parent PO's
    created_at."""
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


@mrp_router.post("/generate-po", response_model=GeneratePoResponse)
async def generate_po(
    body: GeneratePoRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Turn selected plan shortfalls into a draft purchase order for one
    supplier. Creates a *draft* (not submitted) PO so the buyer reviews
    quantities and prices in the normal PO editor before committing - and so it
    doesn't count as on-order supply until they do (a fresh generate-po run
    would otherwise net itself away). Only purchased items with a real net
    requirement are orderable; manufactured items are a build action, not a buy.
    """
    hs, he = _resolve_horizon(body.horizon_start, body.horizon_end)
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

    plan_by_item = {line.item_id: line for line in await _compute_mrp_plan(db, org_id, hs, he)}

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

    prices = await _latest_unit_prices(db, org_id, {uuid.UUID(line.item_id) for line in chosen})

    # Allocate a po_number the same guarded way as create_purchase_order: flush
    # the PO alone in the retry loop so a collision rollback doesn't discard the
    # lines we add afterwards.
    for attempt in range(MAX_PO_NUMBER_ATTEMPTS):
        max_number = await db.scalar(
            select(func.max(PurchaseOrder.po_number)).where(PurchaseOrder.org_id == org_id)
        )
        po = PurchaseOrder(
            org_id=org_id,
            supplier_id=supplier_id,
            po_number=(max_number or 0) + 1,
            status="draft",
            created_by=current.id,
        )
        db.add(po)
        try:
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
            if attempt == MAX_PO_NUMBER_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Could not allocate a PO number, please retry"
                ) from None

    response_lines: list[GeneratePoLine] = []
    for seq, line in enumerate(chosen):
        item_uuid = uuid.UUID(line.item_id)
        unit_price = prices.get(item_uuid, Decimal(0))
        db.add(
            PurchaseOrderLine(
                org_id=org_id,
                po_id=po.id,
                item_id=item_uuid,
                quantity_ordered=line.net_requirement,
                unit_price=unit_price,
                sequence=seq,
            )
        )
        response_lines.append(
            GeneratePoLine(
                item_id=line.item_id,
                item_sku=line.item_sku,
                item_name=line.item_name,
                quantity_ordered=line.net_requirement,
                unit_price=unit_price,
            )
        )
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
