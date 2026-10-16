"""Schemas and helpers for the /analytics/oee routes (split out of oee.py)."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.services import datasets as ds_service


# ─── Request / response models ─────────────────────────────────────────────

class OeeComputeRequest(BaseModel):
    dataset_id: uuid.UUID
    planned_time_col: str = Field(..., min_length=1)
    operating_time_col: str = Field(..., min_length=1)
    total_units_col: str = Field(..., min_length=1)
    good_units_col: str = Field(..., min_length=1)
    target_rate: Optional[float] = Field(default=None, gt=0)
    date_col: Optional[str] = None
    machine_col: Optional[str] = None
    unit_price: Optional[float] = Field(default=None, gt=0)
    machine_rates: Optional[dict[str, float]] = None
    unplanned_downtime_col: Optional[str] = None
    planned_downtime_col: Optional[str] = None
    minor_stops_col: Optional[str] = None
    startup_rejects_col: Optional[str] = None


class OeeTrendPoint(BaseModel):
    date: str
    availability: float
    performance: float
    quality: float
    oee: float
    total_units: float
    invalid_cells: int = 0
    blank_cells: int = 0


class OeeMachineResult(BaseModel):
    machine: str
    availability: float
    performance: float
    quality: float
    oee: float
    total_units: float
    good_units: float
    total_planned: float
    total_operating: float
    revenue_lost: Optional[float] = None
    invalid_cells: int = 0
    blank_cells: int = 0
    rate_used: Optional[float] = None
    utilization: Optional[float] = None
    teep: Optional[float] = None


class RevenueImpactMachine(BaseModel):
    machine: str
    total_lost: float
    availability_lost: float
    performance_lost: float
    quality_lost: float


class RevenueImpact(BaseModel):
    unit_price: float
    total_lost: float
    availability_lost: float
    performance_lost: float
    quality_lost: float
    daily_rate: float
    monthly_rate: float
    benchmark_saving: float
    period_days: float
    period_days_estimated: bool = False
    performance_estimated: bool = False
    per_machine: list[RevenueImpactMachine] = []


class TeepResult(BaseModel):
    """TEEP = OEE × Utilization, where Utilization = planned production time /
    all calendar time (24/7 over the dataset's date span)."""
    teep: float
    utilization: float
    calendar_days: int
    calendar_minutes: float
    machine_count: int
    machine_count_assumed: bool


class SixLossBucket(BaseModel):
    key: str
    label: str
    family: str
    minutes: float
    units: Optional[float] = None
    cost: Optional[float] = None
    pct_of_loss: float = 0.0


class SixBigLosses(BaseModel):
    total_loss_minutes: float
    buckets: list[SixLossBucket]
    availability_split: bool
    performance_split: bool
    quality_split: bool
    performance_estimated: bool


class OeeComputeResponse(BaseModel):
    availability: float
    performance: float
    quality: float
    oee: float
    performance_estimated: bool
    unrated_rows: int = 0
    total_planned: float
    total_operating: float
    total_units: float
    good_units: float
    row_count: int
    invalid_cells: int = 0
    blank_cells: int = 0
    rows_with_issues: int = 0
    availability_clamped: bool = False
    quality_clamped: bool = False
    date_gap_days: int = 0
    duplicate_rows: int = 0
    trend: list[OeeTrendPoint] = []
    machines: list[OeeMachineResult] = []
    revenue_impact: Optional[RevenueImpact] = None
    teep: Optional[TeepResult] = None
    six_big_losses: Optional[SixBigLosses] = None


class OeeRcaRequest(BaseModel):
    dataset_id: uuid.UUID
    planned_time_col: str = Field(..., min_length=1)
    operating_time_col: str = Field(..., min_length=1)
    total_units_col: str = Field(..., min_length=1)
    good_units_col: str = Field(..., min_length=1)
    target_rate: Optional[float] = Field(default=None, gt=0)
    machine_rates: Optional[dict[str, float]] = None
    date_col: Optional[str] = None
    machine_col: Optional[str] = None
    shift_col: Optional[str] = None
    operator_col: Optional[str] = None
    material_batch_col: Optional[str] = None


class OeeRcaDimRow(BaseModel):
    label: str
    oee: float
    availability: float
    performance: float
    quality: float
    total_units: float
    good_units: float
    row_count: int
    delta_oee: float
    invalid_cells: int = 0
    blank_cells: int = 0
    performance_estimated: bool = False
    low_sample: bool = False


# A dimension like batch/serial ID can have thousands of distinct values;
# RCA cares about the worst performers, so keep the lowest-OEE N.
_RCA_MAX_ROWS_PER_DIM = 50
# Below this row count, a dimension value's OEE is too noisy to rank as a
# genuine "worst performer" next to values backed by hundreds of rows.
_RCA_LOW_SAMPLE_THRESHOLD = 5
_RCA_MAX_DROP_PERIODS = 60


class OeeRcaDimension(BaseModel):
    name: str
    col: str
    rows: list[OeeRcaDimRow]
    total_rows: int = 0


class OeeRcaDropPeriod(BaseModel):
    date: str
    oee: float
    delta_oee: float


class OeeRcaResponse(BaseModel):
    overall_oee: float
    overall_availability: float
    overall_performance: float
    overall_quality: float
    invalid_cells: int = 0
    blank_cells: int = 0
    rows_with_issues: int = 0
    dimensions: list[OeeRcaDimension]
    drop_periods: list[OeeRcaDropPeriod]
    drop_periods_total: int = 0
    overall_performance_estimated: bool = False


# ─── Internal helpers ───────────────────────────────────────────────────────

def _day_key(v: Any) -> str:
    """Truncate a date/timestamp cell to its calendar-day portion."""
    s = str(v or "").strip()
    if not s:
        return s
    for sep in (" ", "T"):
        if sep in s:
            return s.split(sep, 1)[0]
    return s


def _date_span_days(con, rel: str, date_col: str) -> Optional[int]:
    """Inclusive calendar-day span (min → max) of the date column, for TEEP."""
    c = ds_service.qi(date_col)
    sql = (
        f"SELECT MIN(ts), MAX(ts) FROM (SELECT "  # nosec B608 - date_col is qi()-quoted and validate_columns-checked above
        f"COALESCE(TRY_CAST(CAST({c} AS VARCHAR) AS TIMESTAMP), "
        f"CAST(TRY_CAST(CAST({c} AS VARCHAR) AS DATE) AS TIMESTAMP)) AS ts "
        f"FROM {rel})"
    )
    try:
        row = con.execute(sql).fetchone() or (None, None)
    except Exception:
        return None
    lo, hi = row[0], row[1]
    if lo is None or hi is None:
        return None
    lo_d = lo.date() if hasattr(lo, "date") and callable(getattr(lo, "date")) else lo
    hi_d = hi.date() if hasattr(hi, "date") and callable(getattr(hi, "date")) else hi
    try:
        return max(1, int((hi_d - lo_d).days) + 1)
    except Exception:
        return None


def _six_big_losses(
    totals: dict[str, float],
    ideal: float,
    fully_estimated: bool,
    sums: dict[str, Any],
    split_cols: dict[str, str],
    unit_price: Optional[float],
) -> Optional[SixBigLosses]:
    """Decompose OEE losses into the TPM Six Big Losses, in minutes (and $).
    Splits within a family are only made when the dataset actually contains a
    split column; otherwise the pair is reported as one combined bucket."""
    planned, operating = totals["planned"], totals["operating"]
    total, good = totals["total"], totals["good"]
    if planned <= 0:
        return None

    rate_eff = (ideal / operating) if operating > 0 else 0.0
    price = unit_price if (unit_price and unit_price > 0) else None

    avail_min = max(0.0, planned - operating)
    if fully_estimated or ideal <= 0:
        perf_min = 0.0
    else:
        perf_frac = max(0.0, min(1.0, total / ideal))
        perf_min = operating * (1.0 - perf_frac)
    bad_units = max(0.0, total - good)
    qual_min = (bad_units / rate_eff) if rate_eff > 0 else 0.0

    buckets: list[SixLossBucket] = []

    def add(key: str, label: str, family: str, minutes: float, units: Optional[float] = None):
        cost = None
        if price is not None:
            cost = round((units * price) if family == "quality" and units is not None
                         else minutes * rate_eff * price, 2)
        buckets.append(SixLossBucket(
            key=key, label=label, family=family, minutes=round(minutes, 1),
            units=(round(units, 1) if units is not None else None), cost=cost,
        ))

    has_up, has_pl = "unplanned_dt" in split_cols, "planned_dt" in split_cols
    availability_split = has_up or has_pl
    if availability_split:
        ef = min(float(sums.get("unplanned_dt", 0.0)), avail_min) if has_up else 0.0
        su = min(float(sums.get("planned_dt", 0.0)), avail_min - ef) if has_pl else 0.0
        rest = max(0.0, avail_min - ef - su)
        if has_up:
            add("equipment_failure", "Equipment Failure (unplanned stops)", "availability", ef)
        if has_pl:
            add("setup_adjustments", "Setup & Adjustments (changeovers)", "availability", su)
        if rest > 0.05:
            add("unclassified_downtime", "Unclassified Downtime", "availability", rest)
    else:
        add("availability_loss", "All Stops (breakdowns + changeovers)", "availability", avail_min)

    performance_split = ("minor_stops" in split_cols) and not fully_estimated
    if fully_estimated:
        add("performance_loss", "Speed & Minor-Stop Losses", "performance", 0.0)
    elif performance_split:
        ms = min(float(sums.get("minor_stops", 0.0)), perf_min)
        add("minor_stops", "Idling & Minor Stops", "performance", ms)
        add("reduced_speed", "Reduced Speed (slow cycles)", "performance", max(0.0, perf_min - ms))
    else:
        add("performance_loss", "Speed & Minor-Stop Losses (combined)", "performance", perf_min)

    quality_split = "startup_rejects" in split_cols
    if quality_split:
        sr = min(float(sums.get("startup_rejects", 0.0)), bad_units)
        sr_min = (sr / rate_eff) if rate_eff > 0 else 0.0
        add("startup_rejects", "Startup Rejects (warm-up scrap)", "quality", sr_min, units=sr)
        add("process_defects", "Process Defects (production rejects)", "quality",
            max(0.0, qual_min - sr_min), units=max(0.0, bad_units - sr))
    else:
        add("quality_loss", "All Defects (startup + production)", "quality", qual_min, units=bad_units)

    total_loss = sum(b.minutes for b in buckets)
    for b in buckets:
        b.pct_of_loss = round(b.minutes / total_loss * 100, 1) if total_loss > 0 else 0.0

    return SixBigLosses(
        total_loss_minutes=round(total_loss, 1),
        buckets=buckets,
        availability_split=availability_split,
        performance_split=performance_split,
        quality_split=quality_split,
        performance_estimated=fully_estimated,
    )


async def _get_dataset(db: AsyncSession, current: CurrentUser, dataset_id: uuid.UUID):
    return await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
