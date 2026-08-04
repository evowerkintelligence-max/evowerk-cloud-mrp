"""Data Integration hub — CRUD for connection configs + test/preview/sync.
Sync materializes source data as a CSV Dataset (see app.services.integrations).
Ported from the desktop app's routes/integrations.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.integration import Integration
from app.services import integrations as integrations_service
from app.services.connectors import VALID_TYPES

router = APIRouter(prefix="/integrations", tags=["integrations"])

_FILE_TYPES = {"csv", "excel"}


# -----------------------------
# Schemas
# -----------------------------

class IntegrationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: str
    config: dict = Field(default_factory=dict)


class IntegrationUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None


class IntegrationResponse(BaseModel):
    id: str
    name: str
    type: str
    config: dict
    status: str
    last_tested_at: str | None
    last_synced_at: str | None
    last_sync_rows: int | None
    last_sync_dataset_id: str | None
    created_at: str
    updated_at: str


class SyncRequest(BaseModel):
    dataset_name: str | None = None
    max_rows: int = Field(500_000, ge=1, le=5_000_000)


def _to_response(item: Integration) -> IntegrationResponse:
    return IntegrationResponse(
        id=str(item.id),
        name=item.name,
        type=item.type,
        config=integrations_service.mask_config(item.config),
        status=item.status,
        last_tested_at=item.last_tested_at.isoformat() if item.last_tested_at else None,
        last_synced_at=item.last_synced_at.isoformat() if item.last_synced_at else None,
        last_sync_rows=item.last_sync_rows,
        last_sync_dataset_id=str(item.last_sync_dataset_id) if item.last_sync_dataset_id else None,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


async def _get_or_404(db: AsyncSession, org_id: uuid.UUID, integration_id: uuid.UUID) -> Integration:
    item = (
        await db.execute(select(Integration).where(Integration.id == integration_id, Integration.org_id == org_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    return item


# -----------------------------
# Connector type metadata
# Registered before "/{integration_id}" - both are GET on a single path
# segment, and Starlette commits to whichever matches first regardless of the
# {integration_id}: uuid.UUID type hint (same ordering issue noted on
# inventory.router in app/main.py).
# -----------------------------

@router.get("/types")
async def list_integration_types(current: CurrentUser = Depends(get_current_user)) -> dict:
    return {
        "types": [
            {"id": "csv", "label": "CSV", "category": "File", "icon": "file-text"},
            {"id": "excel", "label": "Excel", "category": "File", "icon": "table"},
            {"id": "sqlserver", "label": "SQL Server", "category": "Database", "icon": "database"},
            {"id": "oracle", "label": "Oracle", "category": "Database", "icon": "database"},
            {"id": "postgres", "label": "Postgres", "category": "Database", "icon": "database"},
            {"id": "mysql", "label": "MySQL", "category": "Database", "icon": "database"},
            {"id": "sap_odata", "label": "SAP (OData)", "category": "Enterprise", "icon": "building"},
            {"id": "erp_rest", "label": "ERP (REST API)", "category": "Enterprise", "icon": "building"},
            {"id": "mes_rest", "label": "MES (REST API)", "category": "Enterprise", "icon": "cpu"},
            {"id": "opcua", "label": "OPC-UA (PLC)", "category": "Industrial", "icon": "activity"},
            {"id": "mqtt", "label": "MQTT (SCADA)", "category": "Industrial", "icon": "radio"},
            {"id": "opcda", "label": "OPC-DA (legacy PLC)", "category": "Industrial", "icon": "activity"},
            {"id": "pi_webapi", "label": "PI (historian)", "category": "Industrial", "icon": "activity"},
        ]
    }


# -----------------------------
# CRUD
# -----------------------------

@router.get("", response_model=list[IntegrationResponse])
async def list_integrations(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Integration).where(Integration.org_id == current.org_id).order_by(Integration.name))
    ).scalars().all()
    return [_to_response(i) for i in rows]


@router.post("", response_model=IntegrationResponse, status_code=status.HTTP_201_CREATED)
async def create_integration(
    req: IntegrationCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if req.type not in VALID_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid type '{req.type}'. Valid: {sorted(VALID_TYPES)}")

    item = Integration(
        org_id=current.org_id,
        name=req.name,
        type=req.type,
        config=integrations_service.merge_secrets(req.config, {}),
        created_by=current.id,
    )
    db.add(item)
    await log_event(
        db, action="integration.created", org_id=current.org_id, actor_user_id=current.id,
        metadata={"name": req.name, "type": req.type},
    )
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.get("/{integration_id}", response_model=IntegrationResponse)
async def get_integration(
    integration_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    item = await _get_or_404(db, current.org_id, integration_id)
    return _to_response(item)


@router.patch("/{integration_id}", response_model=IntegrationResponse)
async def update_integration(
    integration_id: uuid.UUID,
    patch: IntegrationUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_or_404(db, current.org_id, integration_id)
    if patch.name is not None:
        item.name = patch.name
    if patch.config is not None:
        item.config = integrations_service.merge_secrets(patch.config, item.config)

    await log_event(db, action="integration.updated", org_id=current.org_id, actor_user_id=current.id, target=str(item.id))
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    item = await _get_or_404(db, current.org_id, integration_id)
    if item.type in _FILE_TYPES:
        ext = Path(str(item.config.get("file_path", ""))).suffix.lstrip(".") or "dat"
        integrations_service.delete_integration_source_file(current.org_id, item.id, ext)
    await db.delete(item)
    await log_event(db, action="integration.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(integration_id))
    await db.commit()


# -----------------------------
# CSV/Excel source file upload
# -----------------------------

@router.post("/{integration_id}/upload", response_model=IntegrationResponse)
async def upload_integration_file(
    integration_id: uuid.UUID,
    file: UploadFile,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_or_404(db, current.org_id, integration_id)
    if item.type not in _FILE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only csv/excel integrations accept a file upload")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")

    default_ext = "csv" if item.type == "csv" else "xlsx"
    ext = (Path(file.filename or "").suffix.lstrip(".").lower()) or default_ext
    path = integrations_service.save_integration_source_file(current.org_id, item.id, ext, data)

    item.config = {**item.config, "file_path": str(path)}
    item.status = "untested"
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


# -----------------------------
# Test / preview / sync
# -----------------------------

@router.post("/{integration_id}/test")
async def test_integration(
    integration_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    item = await _get_or_404(db, current.org_id, integration_id)
    result = await integrations_service.test_integration(item)
    item.status = "connected" if result["ok"] else "error"
    item.last_tested_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": result["ok"], "message": result["message"], "status": item.status}


@router.post("/{integration_id}/preview")
async def preview_integration(
    integration_id: uuid.UUID,
    n: int = 10,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_or_404(db, current.org_id, integration_id)
    return await integrations_service.preview_integration(item, n)


@router.post("/{integration_id}/sync")
async def sync_integration(
    integration_id: uuid.UUID,
    req: SyncRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_or_404(db, current.org_id, integration_id)

    try:
        result = await integrations_service.sync_integration(
            db, item, org_id=current.org_id, created_by=current.id,
            dataset_name=req.dataset_name, max_rows=req.max_rows,
        )
    except HTTPException:
        item.status = "error"
        await db.commit()
        raise

    item.status = "connected"
    item.last_synced_at = datetime.now(timezone.utc)
    item.last_sync_rows = result["rows_synced"]
    item.last_sync_dataset_id = uuid.UUID(result["dataset"]["id"])
    await log_event(
        db, action="integration.synced", org_id=current.org_id, actor_user_id=current.id, target=str(item.id),
        metadata={"rows_synced": result["rows_synced"]},
    )
    await db.commit()

    return {"ok": True, "rows_synced": result["rows_synced"], "dataset": result["dataset"]}
