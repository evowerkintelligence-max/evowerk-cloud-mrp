from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.audit import AuditLog
from app.models.bom import BomLine, Item, ItemUnit
from app.models.procurement import Supplier
from app.models.user import User
from app.services import datasets as ds_service

router = APIRouter(prefix="/items", tags=["items"])

VALID_ITEM_TYPES = {"raw_material", "component", "finished_good"}


class ItemCreateRequest(BaseModel):
    sku: str
    name: str
    item_type: str = "component"
    unit_of_measure: str = "ea"
    is_lot_tracked: bool = False
    preferred_supplier_id: str | None = None
    lead_time_days: int | None = None
    min_order_qty: Decimal | None = None
    description: str | None = None
    barcode: str | None = None


class ItemUpdateRequest(BaseModel):
    # Always explicit: null clears the preference. This is the only
    # required-every-call field (sku/type/lot-tracking are load-bearing for
    # history and deliberately fixed after creation).
    preferred_supplier_id: str | None
    # Optional/omittable, unlike preferred_supplier_id above: existing callers
    # that only ever sent one of these fields shouldn't have the others
    # silently reset. Checked via model_fields_set in update_item.
    lead_time_days: int | None = None
    min_order_qty: Decimal | None = None
    is_active: bool | None = None
    name: str | None = None
    unit_of_measure: str | None = None
    description: str | None = None
    barcode: str | None = None


class ItemResponse(BaseModel):
    id: str
    sku: str
    name: str
    item_type: str
    unit_of_measure: str
    is_lot_tracked: bool
    preferred_supplier_id: str | None
    lead_time_days: int | None
    min_order_qty: Decimal | None
    is_active: bool
    description: str | None
    barcode: str | None


def _to_response(item: Item) -> ItemResponse:
    return ItemResponse(
        id=str(item.id),
        sku=item.sku,
        name=item.name,
        item_type=item.item_type,
        unit_of_measure=item.unit_of_measure,
        is_lot_tracked=item.is_lot_tracked,
        preferred_supplier_id=(
            str(item.preferred_supplier_id) if item.preferred_supplier_id else None
        ),
        lead_time_days=item.lead_time_days,
        min_order_qty=item.min_order_qty,
        is_active=item.is_active,
        description=item.description,
        barcode=item.barcode,
    )


class ItemUnitCreateRequest(BaseModel):
    code: str
    factor_to_base: Decimal


class ItemUnitResponse(BaseModel):
    id: str
    item_id: str
    code: str
    factor_to_base: Decimal


def _unit_to_response(unit: ItemUnit) -> ItemUnitResponse:
    # Format factor_to_base with 6 decimal places to match Numeric(18, 6) precision
    factor_str = f"{unit.factor_to_base:.6f}"
    return ItemUnitResponse(
        id=str(unit.id), item_id=str(unit.item_id), code=unit.code, factor_to_base=Decimal(factor_str)
    )


async def _resolve_supplier_id(
    db: AsyncSession, org_id: uuid.UUID, raw: str | None
) -> uuid.UUID | None:
    """Parse and validate a preferred_supplier_id belongs to this org, or None
    to clear it."""
    if raw is None:
        return None
    try:
        supplier_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid preferred_supplier_id") from None
    exists = (
        await db.execute(select(Supplier.id).where(Supplier.id == supplier_id, Supplier.org_id == org_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preferred supplier not found")
    return supplier_id


async def _create_item_row(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    *,
    sku: str,
    name: str,
    item_type: str = "component",
    unit_of_measure: str = "ea",
    is_lot_tracked: bool = False,
    preferred_supplier_id: str | None = None,
    lead_time_days: int | None = None,
    min_order_qty: Decimal | None = None,
    description: str | None = None,
    barcode: str | None = None,
) -> Item:
    """Validate and insert one Item row (flush + audit log, no commit -
    caller commits). Shared by create_item and the CSV import loop, which
    commits after each row rather than once at the end - see import_items."""
    if item_type not in VALID_ITEM_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"item_type must be one of {sorted(VALID_ITEM_TYPES)}")
    if lead_time_days is not None and lead_time_days < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "lead_time_days must be non-negative")
    if min_order_qty is not None and min_order_qty <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "min_order_qty must be positive")
    resolved_supplier_id = await _resolve_supplier_id(db, org_id, preferred_supplier_id)

    item = Item(
        org_id=org_id,
        sku=sku,
        name=name,
        item_type=item_type,
        unit_of_measure=unit_of_measure,
        is_lot_tracked=is_lot_tracked,
        preferred_supplier_id=resolved_supplier_id,
        lead_time_days=lead_time_days,
        min_order_qty=min_order_qty,
        description=description,
        barcode=barcode,
    )
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "sku or barcode already exists in this org") from None

    await log_event(
        db,
        action="item.created",
        org_id=org_id,
        actor_user_id=actor_user_id,
        target=str(item.id),
        metadata={"sku": item.sku},
    )
    return item


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    body: ItemCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    item = await _create_item_row(
        db,
        current.org_id,
        current.id,
        sku=body.sku,
        name=body.name,
        item_type=body.item_type,
        unit_of_measure=body.unit_of_measure,
        is_lot_tracked=body.is_lot_tracked,
        preferred_supplier_id=body.preferred_supplier_id,
        lead_time_days=body.lead_time_days,
        min_order_qty=body.min_order_qty,
        description=body.description,
        barcode=body.barcode,
    )
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.get("", response_model=list[ItemResponse])
async def list_items(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Item).where(Item.org_id == current.org_id).order_by(Item.sku))
    return [_to_response(item) for item in result.scalars().all()]


class ImportRowResult(BaseModel):
    row: int
    sku: str


class ImportRowError(BaseModel):
    row: int
    sku: str | None
    message: str


class ImportResult(BaseModel):
    created: list[ImportRowResult]
    errors: list[ImportRowError]


class ImportFromDatasetRequest(BaseModel):
    dataset_id: str


def _row_cell(row: dict[str, object], field_map: dict[str, str], key: str) -> str | None:
    column = field_map.get(key)
    if column is None:
        return None
    raw = row.get(column)
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


@router.post("/import", response_model=ImportResult)
async def import_items(
    body: ImportFromDatasetRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Row-by-row import from an already-uploaded Dataset (see /datasets) -
    the single ingestion gate for this app; Items doesn't take its own file
    upload. Unlike dataset uploads themselves (all-or-nothing), populating
    Items from one is row-by-row with partial success: each row commits
    independently so one typo'd SKU can't roll back every row imported
    before it."""
    try:
        dataset_id = uuid.UUID(body.dataset_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid dataset_id") from None
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)

    field_map = {c["name"].strip().lower(): c["name"] for c in dataset.columns}
    if "sku" not in field_map or "name" not in field_map:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dataset must include sku and name columns")

    rows = ds_service.preview_rows(dataset, limit=dataset.row_count or 0)

    created: list[ImportRowResult] = []
    errors: list[ImportRowError] = []
    for row_number, row in enumerate(rows, start=2):  # row 1 is the header
        sku = _row_cell(row, field_map, "sku")
        name = _row_cell(row, field_map, "name")
        if not sku or not name:
            errors.append(ImportRowError(row=row_number, sku=sku, message="sku and name are required"))
            continue

        lead_time_raw = _row_cell(row, field_map, "lead_time_days")
        try:
            lead_time_days = int(lead_time_raw) if lead_time_raw is not None else None
        except ValueError:
            errors.append(
                ImportRowError(row=row_number, sku=sku, message="lead_time_days must be an integer")
            )
            continue

        moq_raw = _row_cell(row, field_map, "min_order_qty")
        try:
            min_order_qty = Decimal(moq_raw) if moq_raw is not None else None
        except InvalidOperation:
            errors.append(
                ImportRowError(row=row_number, sku=sku, message="min_order_qty must be a number")
            )
            continue

        try:
            item = await _create_item_row(
                db,
                current.org_id,
                current.id,
                sku=sku,
                name=name,
                item_type=_row_cell(row, field_map, "item_type") or "component",
                unit_of_measure=_row_cell(row, field_map, "unit_of_measure") or "ea",
                is_lot_tracked=(_row_cell(row, field_map, "is_lot_tracked") or "").lower()
                in {"true", "1", "yes"},
                preferred_supplier_id=_row_cell(row, field_map, "preferred_supplier_id"),
                lead_time_days=lead_time_days,
                min_order_qty=min_order_qty,
                description=_row_cell(row, field_map, "description"),
                barcode=_row_cell(row, field_map, "barcode"),
            )
        except HTTPException as e:
            errors.append(ImportRowError(row=row_number, sku=sku, message=str(e.detail)))
            continue

        await db.commit()
        created.append(ImportRowResult(row=row_number, sku=item.sku))

    return ImportResult(created=created, errors=errors)


@router.get("/export")
async def export_items(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Item).where(Item.org_id == current.org_id).order_by(Item.sku))
    items = result.scalars().all()

    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(
        [
            "sku", "name", "item_type", "unit_of_measure", "is_lot_tracked",
            "lead_time_days", "min_order_qty", "is_active", "description", "barcode",
            "preferred_supplier_id",
        ]
    )
    for item in items:
        writer.writerow(
            [
                item.sku,
                item.name,
                item.item_type,
                item.unit_of_measure,
                item.is_lot_tracked,
                item.lead_time_days if item.lead_time_days is not None else "",
                item.min_order_qty if item.min_order_qty is not None else "",
                item.is_active,
                item.description or "",
                item.barcode or "",
                str(item.preferred_supplier_id) if item.preferred_supplier_id else "",
            ]
        )

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="items.csv"'},
    )


@router.patch("/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: uuid.UUID,
    body: ItemUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    item.preferred_supplier_id = await _resolve_supplier_id(
        db, current.org_id, body.preferred_supplier_id
    )
    metadata: dict[str, object] = {"preferred_supplier_id": body.preferred_supplier_id}
    if "lead_time_days" in body.model_fields_set:
        if body.lead_time_days is not None and body.lead_time_days < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "lead_time_days must be non-negative")
        item.lead_time_days = body.lead_time_days
        metadata["lead_time_days"] = body.lead_time_days
    if "min_order_qty" in body.model_fields_set:
        if body.min_order_qty is not None and body.min_order_qty <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "min_order_qty must be positive")
        item.min_order_qty = body.min_order_qty
        metadata["min_order_qty"] = str(body.min_order_qty) if body.min_order_qty is not None else None
    if "is_active" in body.model_fields_set and body.is_active is not None:
        item.is_active = body.is_active
        metadata["is_active"] = body.is_active
    if "name" in body.model_fields_set:
        if not body.name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must not be empty")
        item.name = body.name
        metadata["name"] = body.name
    if "unit_of_measure" in body.model_fields_set:
        if not body.unit_of_measure:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unit_of_measure must not be empty")
        item.unit_of_measure = body.unit_of_measure
        metadata["unit_of_measure"] = body.unit_of_measure
    if "description" in body.model_fields_set:
        item.description = body.description
        metadata["description"] = body.description
    if "barcode" in body.model_fields_set:
        item.barcode = body.barcode
        metadata["barcode"] = body.barcode
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "barcode already exists in this org") from None

    await log_event(
        db,
        action="item.updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item.id),
        metadata=metadata,
    )
    await db.commit()
    return _to_response(item)


class AuditLogEntry(BaseModel):
    action: str
    actor_email: str | None
    metadata: dict
    created_at: str


@router.get("/{item_id}/audit-log", response_model=list[AuditLogEntry])
async def get_item_audit_log(
    item_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    result = await db.execute(
        select(AuditLog.action, AuditLog.event_metadata, AuditLog.created_at, User.email)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(AuditLog.org_id == current.org_id, AuditLog.target == str(item_id))
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    )
    return [
        AuditLogEntry(
            action=action,
            actor_email=actor_email,
            metadata=event_metadata,
            created_at=created_at.isoformat(),
        )
        for action, event_metadata, created_at, actor_email in result.all()
    ]


@router.post("/{item_id}/units", response_model=ItemUnitResponse, status_code=status.HTTP_201_CREATED)
async def create_item_unit(
    item_id: uuid.UUID,
    body: ItemUnitCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    code = body.code.strip()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "code is required")
    if body.factor_to_base <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "factor_to_base must be positive")

    unit = ItemUnit(org_id=current.org_id, item_id=item_id, code=code, factor_to_base=body.factor_to_base)
    db.add(unit)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f'Unit "{code}" already exists for this item'
        ) from None
    await db.commit()
    return _unit_to_response(unit)


@router.get("/{item_id}/units", response_model=list[ItemUnitResponse])
async def list_item_units(
    item_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    result = await db.execute(
        select(ItemUnit)
        .where(ItemUnit.org_id == current.org_id, ItemUnit.item_id == item_id)
        .order_by(ItemUnit.code)
    )
    return [_unit_to_response(u) for u in result.scalars().all()]


@router.delete("/{item_id}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item_unit(
    item_id: uuid.UUID,
    unit_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    unit = await get_or_404(db, ItemUnit, current.org_id, unit_id, "Unit not found")
    if unit.item_id != item_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found")
    in_use = await db.scalar(select(BomLine.id).where(BomLine.unit_id == unit_id).limit(1))
    if in_use is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This unit is used by a BOM line and can't be deleted"
        )
    await db.delete(unit)
    await db.commit()
