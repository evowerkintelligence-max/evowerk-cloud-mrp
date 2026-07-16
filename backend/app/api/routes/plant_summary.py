"""Plant Summary — a single-dataset, single-screen rollup combining the OEE
and Scrap engines' headline numbers (OEE%, scrap rate, FPQ%, defect rate) plus
a trend chart and a per-machine breakdown, so a plant manager gets one screen
instead of switching between the OEE and Scrap & Quality pages. Ported from
the desktop app's backend/routes/plant.py; no new metrics engine — reuses
app.analytics.oee_calc, the same math OEE itself runs on.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import (
    compute_components_ideal,
    date_bucket_expr,
    oee_grouped,
    oee_overall,
    rate_sql_expr,
    val_expr,
)
from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


class PlantSummaryRequest(BaseModel):
    dataset_id: uuid.UUID
    planned_time_col: str = Field(..., min_length=1)
    operating_time_col: str = Field(..., min_length=1)
    total_units_col: str = Field(..., min_length=1)
    good_units_col: str = Field(..., min_length=1)
    date_col: Optional[str] = None
    machine_col: Optional[str] = None
    scrap_units_col: Optional[str] = None
    defects_col: Optional[str] = None
    target_rate: Optional[float] = None
    machine_rates: Optional[dict[str, float]] = None


class PlantSummaryTrendPoint(BaseModel):
    date: str
    oee: float
    total_units: float
    scrap_rate_pct: Optional[float] = None


class PlantSummaryMachineRow(BaseModel):
    machine: str
    oee: float
    availability: float
    performance: float
    quality: float
    total_units: float
    good_units: float


class PlantSummaryResponse(BaseModel):
    oee: float
    availability: float
    performance: float
    quality: float
    performance_estimated: bool
    total_units: float
    good_units: float
    row_count: int
    invalid_cells: int = 0
    blank_cells: int = 0
    scrap_rate_pct: Optional[float] = None
    fpq_pct: Optional[float] = None
    defect_rate_per_1000: Optional[float] = None
    trend: list[PlantSummaryTrendPoint] = []
    machines: list[PlantSummaryMachineRow] = []


@router.post("/plant-summary", response_model=PlantSummaryResponse)
async def plant_summary(
    req: PlantSummaryRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PlantSummaryResponse:
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, req.dataset_id)
    ds_service.validate_columns(dataset, [
        req.planned_time_col, req.operating_time_col, req.total_units_col, req.good_units_col,
        req.date_col, req.machine_col, req.scrap_units_col, req.defects_col,
    ])

    headers = {c["name"] for c in dataset.columns}
    pc, oc, tc, gc = req.planned_time_col, req.operating_time_col, req.total_units_col, req.good_units_col
    machine_col = req.machine_col if (req.machine_col and req.machine_col in headers) else None
    scrap_col = req.scrap_units_col if (req.scrap_units_col and req.scrap_units_col in headers) else None
    defects_col = req.defects_col if (req.defects_col and req.defects_col in headers) else None

    rate_sql = rate_sql_expr(machine_col, req.machine_rates, req.target_rate)
    fully_estimated = rate_sql is None

    extra_cols = {
        key: col for key, col in [("scrap", scrap_col), ("defects", defects_col)] if col
    }

    rel = ds_service.dataset_relation(dataset)
    con = ds_service.open_con()
    try:
        ov = oee_overall(con, rel, pc, oc, tc, gc, ideal_sql=rate_sql, extra_sum_cols=extra_cols)
        trend_rows = (
            oee_grouped(con, rel, pc, oc, tc, gc, req.date_col, ideal_sql=rate_sql)
            if (req.date_col and req.date_col in headers) else []
        )
        machine_rows = (
            oee_grouped(con, rel, pc, oc, tc, gc, machine_col, ideal_sql=rate_sql)
            if machine_col else []
        )
        scrap_trend: dict[str, float] = {}
        if scrap_col and req.date_col and req.date_col in headers:
            dk = date_bucket_expr(req.date_col)
            # nosec B608 - dk/scrap_col are qi()/val_expr-quoted, validate_columns-checked above
            sql = f"SELECT {dk}, SUM({val_expr(scrap_col)}) FROM {rel} WHERE {dk} IS NOT NULL GROUP BY 1"  # nosec B608
            for k, s in con.execute(sql).fetchall():
                scrap_trend[str(k)] = float(s or 0.0)
    finally:
        con.close()

    totals = {"planned": ov["planned"], "operating": ov["operating"], "total": ov["total"], "good": ov["good"]}
    avail, perf, qual, oee, estimated = compute_components_ideal(
        totals["planned"], totals["operating"], totals["total"], totals["good"], ov["ideal"], fully_estimated,
    )

    scrap_rate_pct = fpq_pct = defect_rate_per_1000 = None
    if scrap_col and totals["total"] > 0:
        scrap_units = ov["scrap"]
        scrap_rate_pct = round(scrap_units / totals["total"] * 100, 2)
        fpq_pct = round(100.0 - scrap_rate_pct, 2)
    if defects_col and totals["total"] > 0:
        defect_rate_per_1000 = round(ov["defects"] / totals["total"] * 1000, 2)

    trend: list[PlantSummaryTrendPoint] = []
    for date_key, g in sorted(trend_rows, key=lambda kv: kv[0]):
        _, _, _, o, _ = compute_components_ideal(g["planned"], g["operating"], g["total"], g["good"], g["ideal"], fully_estimated)
        point_scrap = None
        if date_key in scrap_trend and g["total"] > 0:
            point_scrap = round(scrap_trend[date_key] / g["total"] * 100, 2)
        trend.append(PlantSummaryTrendPoint(date=date_key, oee=o, total_units=g["total"], scrap_rate_pct=point_scrap))

    machines: list[PlantSummaryMachineRow] = []
    for mach, g in sorted(machine_rows, key=lambda kv: kv[0]):
        a, p, q, o, _ = compute_components_ideal(g["planned"], g["operating"], g["total"], g["good"], g["ideal"], fully_estimated)
        machines.append(PlantSummaryMachineRow(
            machine=mach, oee=o, availability=a, performance=p, quality=q,
            total_units=g["total"], good_units=g["good"],
        ))
    machines.sort(key=lambda m: m.oee, reverse=True)

    return PlantSummaryResponse(
        oee=oee, availability=avail, performance=perf, quality=qual, performance_estimated=estimated,
        total_units=totals["total"], good_units=totals["good"], row_count=ov["row_count"],
        invalid_cells=ov["invalid_cells"], blank_cells=ov["blank_cells"],
        scrap_rate_pct=scrap_rate_pct, fpq_pct=fpq_pct, defect_rate_per_1000=defect_rate_per_1000,
        trend=trend, machines=machines,
    )
