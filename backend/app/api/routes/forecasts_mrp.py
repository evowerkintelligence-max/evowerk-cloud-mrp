"""The /mrp router: MRP netting, build plans and PO generation (split out of forecasts.py)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.procurement import PurchaseOrder, Supplier, PurchaseOrderLine
from app.services.mrp import MrpPlanLine, compute_build_plan, compute_mrp_plan, resolve_horizon
from app.services.pricing import latest_unit_prices
from app.api.routes.forecasts_helpers import (
    MrpPlanResponse,
    GeneratePoRequest,
    GeneratePoLine,
    GeneratePoResponse,
    GeneratePosRequest,
    GeneratedPoSummary,
    UnassignedItem,
    GeneratePosResponse,
    BuildPlanRequest,
    BuildPlanResponse,
    _parse_item_id,
)

# Same po_number max+1 read-then-insert race guard as create_purchase_order -
# generate-po allocates a po_number the same way.
MAX_PO_NUMBER_ATTEMPTS = 3
mrp_router = APIRouter(prefix="/mrp", tags=["mrp"])
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


async def _lock_org_for_po_generation(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Serializes concurrent MRP-driven PO generation within one org so two
    overlapping generate-po/generate-pos/build-plan calls can't each compute
    the plan (same on_hand/on_order/on_draft snapshot, same shortfall) before
    either commits its draft PO - on_draft only prevents double-ordering
    *after* a request commits, not against one racing it. Must be acquired
    before compute_mrp_plan/compute_build_plan, not just around PO creation,
    since the snapshot read is exactly what needs to serialize.

    Same org-scoped advisory lock pattern as boms._lock_org_for_activation:
    O(1) on Postgres, auto-released at transaction end, so unrelated orgs
    never serialize behind each other. Namespaced ("mrp-po:") so this lock
    doesn't collide with the BOM-activation lock, which uses the bare org_id
    as its key. SQLite (the test backend) has no advisory-lock primitive, so
    it falls back to a row lock on the org's POs - a no-op on an org with no
    POs yet, which is fine at test-sized scale (same caveat as the BOM lock).
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"mrp-po:{org_id}"})
    else:
        await db.execute(select(PurchaseOrder.id).where(PurchaseOrder.org_id == org_id).with_for_update())


async def _new_draft_po(
    db: AsyncSession, org_id: uuid.UUID, supplier: Supplier, created_by: uuid.UUID
) -> PurchaseOrder:
    """Allocate a po_number and create a draft PO, guarded against the max+1
    read-then-insert race. Uses a per-attempt SAVEPOINT (not a plain rollback)
    so it's safe to call repeatedly within one request - generate_pos creates a
    PO per supplier, and a plain rollback on the 2nd would discard the 1st.
    """
    expected_delivery_date = (
        datetime.now(timezone.utc).date() + timedelta(days=supplier.lead_time_days)
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

    await _lock_org_for_po_generation(db, org_id)
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

    await _lock_org_for_po_generation(db, org_id)
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
    await _lock_org_for_po_generation(db, current.org_id)
    lines = await compute_build_plan(
        db, current.org_id, item_uuid, body.build_quantity, body.yield_loss_pct
    )
    orderable = [line for line in lines if not line.is_manufactured and line.net_requirement > 0]
    return await _create_pos_grouped_by_supplier(
        db, current.org_id, current, orderable, "po.generated_from_build_plan"
    )
