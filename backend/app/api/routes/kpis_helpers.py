"""Schemas and helpers for the /kpis routes (split out of kpis.py)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from app.models.dataset import Dataset
from app.models.kpi import Kpi, KpiValue
from app.services import datasets as ds_service
from app.services import kpis as kpi_service


# -----------------------------
# Request/response schemas
# -----------------------------

class KpiDeltaPolicy(BaseModel):
    warn_abs: float | None = None
    breach_abs: float | None = None
    warn_pct: float | None = None
    breach_pct: float | None = None


class KpiStaticPolicy(BaseModel):
    warn_low: float | None = None
    breach_low: float | None = None
    warn_high: float | None = None
    breach_high: float | None = None


class KpiBaselinePolicy(BaseModel):
    window: int = Field(14, ge=2, le=3650)
    warn_z: float | None = 2.0
    breach_z: float | None = 3.0


class KpiTrendPolicy(BaseModel):
    window: int = Field(14, ge=2, le=3650)
    flat_epsilon_pct: float = Field(0.01, ge=0.0, le=1.0)


class KpiEvalPolicy(BaseModel):
    delta: KpiDeltaPolicy | None = None
    static: KpiStaticPolicy | None = None
    baseline: KpiBaselinePolicy | None = None
    trend: KpiTrendPolicy | None = None


class KpiWriteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    dataset_id: uuid.UUID
    sql: str = Field(..., min_length=1, max_length=4000)
    unit: str | None = Field(default=None, max_length=50)
    direction: str = "neutral"  # higher_is_better | lower_is_better | neutral
    enabled: bool = True
    eval_policy: KpiEvalPolicy | None = None
    tags: list[str] = Field(default_factory=list)
    breakdown_group_col: str | None = None
    breakdown_agg: Literal["count", "avg", "sum"] | None = None
    breakdown_value_col: str | None = None


class KpiPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    dataset_id: uuid.UUID | None = None
    sql: str | None = Field(default=None, min_length=1, max_length=4000)
    unit: str | None = Field(default=None, max_length=50)
    direction: str | None = None
    enabled: bool | None = None
    eval_policy: KpiEvalPolicy | None = None
    tags: list[str] | None = None
    breakdown_group_col: str | None = None
    breakdown_agg: Literal["count", "avg", "sum"] | None = None
    breakdown_value_col: str | None = None


class KpiResponse(BaseModel):
    id: str
    dataset_id: str
    name: str
    description: str | None
    sql: str
    unit: str | None
    direction: str
    enabled: bool
    eval_policy: dict | None
    tags: list[str]
    breakdown_group_col: str | None
    breakdown_agg: str | None
    breakdown_value_col: str | None
    created_at: str
    updated_at: str


class KpiValueResponse(BaseModel):
    kpi_id: str
    dataset_id: str
    value: float | None
    error: str | None
    meta: dict
    computed_at: str


class KpiComputeRequest(BaseModel):
    dataset_id: uuid.UUID
    kpi_ids: list[uuid.UUID] = Field(default_factory=list)
    force: bool = False
    include_eval: bool = False
    lookback: int = Field(14, ge=1, le=3650)


class KpiComputeItemResponse(BaseModel):
    kpi_id: str
    value: float | None
    meta: dict
    computed_at: str
    cache_hit: bool
    error: str | None
    eval: dict | None
    breakdown: list[dict] | None


class KpiCompareRequest(BaseModel):
    dataset_id_a: uuid.UUID
    dataset_id_b: uuid.UUID
    kpi_ids: list[uuid.UUID] = Field(default_factory=list)
    force: bool = False
    include_eval: bool = False
    lookback: int = Field(14, ge=1, le=3650)


class KpiCompareItemResponse(BaseModel):
    kpi_id: str
    a_value: float | None
    b_value: float | None
    delta: float | None
    pct_delta: float | None
    a_error: str | None
    b_error: str | None
    a_eval: dict | None
    b_eval: dict | None


def _kpi_to_response(k: Kpi) -> KpiResponse:
    return KpiResponse(
        id=str(k.id),
        dataset_id=str(k.dataset_id),
        name=k.name,
        description=k.description,
        sql=k.sql,
        unit=k.unit,
        direction=k.direction,
        enabled=k.enabled,
        eval_policy=k.eval_policy,
        tags=k.tags,
        breakdown_group_col=k.breakdown_group_col,
        breakdown_agg=k.breakdown_agg,
        breakdown_value_col=k.breakdown_value_col,
        created_at=k.created_at.isoformat(),
        updated_at=k.updated_at.isoformat(),
    )


def _value_to_response(v: KpiValue) -> KpiValueResponse:
    return KpiValueResponse(
        kpi_id=str(v.kpi_id),
        dataset_id=str(v.dataset_id),
        value=v.value,
        error=v.error,
        meta=v.meta,
        computed_at=v.computed_at.isoformat(),
    )


def _item_to_response(i: kpi_service.KpiComputeItem) -> KpiComputeItemResponse:
    return KpiComputeItemResponse(
        kpi_id=str(i.kpi_id), value=i.value, meta=i.meta, computed_at=i.computed_at.isoformat(),
        cache_hit=i.cache_hit, error=i.error, eval=i.eval,
        breakdown=[{"label": label, "value": v} for label, v in i.breakdown] if i.breakdown else None,
    )


def _compare_item_to_response(i: kpi_service.KpiCompareItem) -> KpiCompareItemResponse:
    return KpiCompareItemResponse(
        kpi_id=str(i.kpi_id), a_value=i.a_value, b_value=i.b_value, delta=i.delta, pct_delta=i.pct_delta,
        a_error=i.a_error, b_error=i.b_error, a_eval=i.a_eval, b_eval=i.b_eval,
    )


def _validate_breakdown(dataset: Dataset, group_col: str | None, agg: str | None, value_col: str | None) -> None:
    if not group_col:
        return
    cols = [group_col]
    if agg and agg != "count":
        if not value_col:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "breakdown_value_col is required when breakdown_agg is not 'count'")
        cols.append(value_col)
    ds_service.validate_columns(dataset, cols)
