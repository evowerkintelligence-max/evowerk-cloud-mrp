from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services.morning_brief import LotAlertLine, compute_lot_alerts
from app.services.work_orders import JobAlertLine, compute_job_alerts

router = APIRouter(prefix="/inventory", tags=["morning-brief"])


class LotAlertResponse(BaseModel):
    lot_id: str
    item_id: str
    item_sku: str
    item_name: str
    location_id: str
    location_code: str
    lot_number: str
    quantity_remaining: Decimal
    status: str
    expiry_date: date | None
    days_until_expiry: int | None
    reason: str


class JobAlertResponse(BaseModel):
    wo_id: str
    wo_number: int
    item_sku: str
    item_name: str
    reason: str
    days_over: float


class MorningBriefResponse(BaseModel):
    lot_alerts: list[LotAlertResponse]
    job_alerts: list[JobAlertResponse]


def _lot_alert_to_response(alert: LotAlertLine) -> LotAlertResponse:
    return LotAlertResponse(
        lot_id=str(alert.lot_id),
        item_id=str(alert.item_id),
        item_sku=alert.item_sku,
        item_name=alert.item_name,
        location_id=str(alert.location_id),
        location_code=alert.location_code,
        lot_number=alert.lot_number,
        quantity_remaining=alert.quantity_remaining,
        status=alert.status,
        expiry_date=alert.expiry_date,
        days_until_expiry=alert.days_until_expiry,
        reason=alert.reason,
    )


def _job_alert_to_response(alert: JobAlertLine) -> JobAlertResponse:
    return JobAlertResponse(
        wo_id=str(alert.wo_id),
        wo_number=alert.wo_number,
        item_sku=alert.item_sku,
        item_name=alert.item_name,
        reason=alert.reason,
        days_over=alert.days_over,
    )


@router.get("/morning-brief", response_model=MorningBriefResponse)
async def get_morning_brief(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Daily digest of lots on hold or expiring soon, plus production jobs
    that have gone quiet. See app.services.morning_brief and
    app.services.work_orders."""
    lot_alerts = await compute_lot_alerts(db, current.org_id)
    job_alerts = await compute_job_alerts(db, current.org_id)
    return MorningBriefResponse(
        lot_alerts=[_lot_alert_to_response(alert) for alert in lot_alerts],
        job_alerts=[_job_alert_to_response(alert) for alert in job_alerts],
    )
