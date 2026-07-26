from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import create_staff_access_token, verify_password_timing_safe
from app.db.session import get_db
from app.middleware.rate_limit import rate_limit
from app.core.config import get_settings
from app.models.staff import StaffAccount

settings = get_settings()
router = APIRouter(prefix="/staff/auth", tags=["staff-auth"])
_staff_login_rate_limit = rate_limit("staff_login", settings.rate_limit_login_per_5min, 300)


class StaffLoginRequest(BaseModel):
    employee_id: str
    password: str


class StaffTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=StaffTokenResponse, dependencies=[Depends(_staff_login_rate_limit)])
async def staff_login(body: StaffLoginRequest, db: AsyncSession = Depends(get_db)):
    staff = (
        await db.execute(select(StaffAccount).where(StaffAccount.employee_id == body.employee_id))
    ).scalar_one_or_none()
    # Always pay the argon2 cost, even for an unknown employee_id, so this
    # path can't be timed to enumerate valid IDs (same protection customer
    # login already has via the same helper).
    ok = await run_in_threadpool(
        verify_password_timing_safe, body.password, staff.hashed_password if staff else None
    )
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    return StaffTokenResponse(access_token=create_staff_access_token(staff_id=str(staff.id)))
