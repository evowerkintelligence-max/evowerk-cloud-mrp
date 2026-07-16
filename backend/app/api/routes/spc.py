from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import spc as spc_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


class SPCRequest(BaseModel):
    dataset_id: str
    value_col: str = Field(..., min_length=1)
    subgroup_col: Optional[str] = None  # set -> Xbar-R; else Individuals/MR
    order_col: Optional[str] = None
    usl: Optional[float] = None
    lsl: Optional[float] = None
    max_points: int = Field(default=2000, ge=50, le=20000)


class SPCPoint(BaseModel):
    label: str
    value: float
    range_value: Optional[float] = None
    in_control: bool
    violations: List[int] = Field(default_factory=list)


class SPCNormality(BaseModel):
    tested: bool
    reason: Optional[str] = None
    statistic: Optional[float] = None
    p_value: Optional[float] = None
    is_normal: Optional[bool] = None


class SPCCapability(BaseModel):
    mean: float
    sigma_within: float
    sigma_overall: float
    usl: Optional[float] = None
    lsl: Optional[float] = None
    cp: Optional[float] = None
    cpk: Optional[float] = None
    pp: Optional[float] = None
    ppk: Optional[float] = None
    normality: Optional[SPCNormality] = None


class SPCResponse(BaseModel):
    chart_type: str
    subgroup_size: int
    centerline: float
    ucl: float
    lcl: float
    range_centerline: float
    range_ucl: float
    range_lcl: float
    points: List[SPCPoint]
    out_of_control_count: int
    row_count: int
    truncated: bool
    low_sample_warning: bool = False
    min_baseline: int = 20
    capability: Optional[SPCCapability] = None


@router.post("/spc", response_model=SPCResponse)
async def analyze_spc(
    req: SPCRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    ds_service.validate_columns(dataset, [req.value_col, req.subgroup_col, req.order_col])
    return SPCResponse(**spc_service.analyze_spc(dataset, req))
