from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.models.alert import AlertRule
from app.services import alerts as service

router = APIRouter(prefix="/alerts", tags=["alerts"])

_ALERT_TYPES = ("machine_downtime", "quality", "scrap", "inventory", "production_target", "stream_metric", "automl_risk")


# ─── Schemas ────────────────────────────────────────────────────────────────

class AlertRuleCreate(BaseModel):
    name: str = Field(..., min_length=1)
    type: str
    severity: str = "warning"
    enabled: bool = True
    dataset_id: uuid.UUID | None = None
    config: dict = Field(default_factory=dict)


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    severity: str | None = None
    enabled: bool | None = None
    dataset_id: uuid.UUID | None = None
    config: dict | None = None


class AlertRuleResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID | None
    name: str
    type: str
    severity: str
    enabled: bool
    config: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AlertTriggerResponse(BaseModel):
    rule_id: str
    rule_name: str
    type: str
    severity: str
    triggered: bool
    value: float
    threshold: float
    message: str
    offenders: list[str] = []
    evaluated_at: str


class ScanResponse(BaseModel):
    results: list[AlertTriggerResponse]
    triggered_count: int
    evaluated_count: int


# ─── Rule CRUD ──────────────────────────────────────────────────────────────

@router.get("/rules", response_model=list[AlertRuleResponse])
async def list_rules(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.list_rules(db, current.org_id)


@router.post("/rules", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: AlertRuleCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if payload.type not in _ALERT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown alert type: {payload.type}")
    if payload.type != "stream_metric" and payload.dataset_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "dataset_id is required for this alert type")
    return await service.create_rule(
        db, org_id=current.org_id, created_by=current.id, name=payload.name, type=payload.type,
        severity=payload.severity, dataset_id=payload.dataset_id, config=payload.config, enabled=payload.enabled,
    )


@router.put("/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_rule(
    rule_id: uuid.UUID, payload: AlertRuleUpdate, current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    patch = payload.model_dump(exclude_unset=True)
    rule = await get_or_404(db, AlertRule, current.org_id, rule_id, "Alert rule not found")
    return await service.update_rule(db, rule, patch)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    rule = await get_or_404(db, AlertRule, current.org_id, rule_id, "Alert rule not found")
    await service.delete_rule(db, rule)


# ─── Scan ───────────────────────────────────────────────────────────────────

@router.post("/scan", response_model=ScanResponse)
async def scan_all(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    results = await service.scan_all(db, current.org_id)
    triggered = [r for r in results if r.triggered]
    return ScanResponse(
        results=[AlertTriggerResponse(**vars(r)) for r in results],
        triggered_count=len(triggered), evaluated_count=len(results),
    )


@router.post("/scan/{rule_id}", response_model=AlertTriggerResponse)
async def scan_one(
    rule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    rule = await get_or_404(db, AlertRule, current.org_id, rule_id, "Alert rule not found")
    if not rule.enabled:
        return AlertTriggerResponse(
            rule_id=str(rule.id), rule_name=rule.name, type=rule.type, severity=rule.severity,
            triggered=False, value=0.0, threshold=0.0, message="Rule is disabled — enable it to evaluate.",
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )
    result = await service.evaluate_rule_safe(db, current.org_id, rule)
    return AlertTriggerResponse(**vars(result))
