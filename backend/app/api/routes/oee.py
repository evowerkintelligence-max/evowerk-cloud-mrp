"""OEE (Overall Equipment Effectiveness) compute + RCA endpoints.

Runs over an existing uploaded Dataset with user-supplied column mappings —
no separate shop-floor "production run" domain needed. Ported from the
desktop app's backend/routes/oee.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.analytics.oee_calc import (
    compute_components_ideal,
    date_bucket_expr,
    duplicate_row_count,
    oee_grouped,
    oee_overall,
    rate_sql_expr,
    revenue_loss_ideal,
)
from app.api.routes.oee_helpers import (
    OeeComputeRequest,
    OeeTrendPoint,
    OeeMachineResult,
    RevenueImpactMachine,
    RevenueImpact,
    TeepResult,
    OeeComputeResponse,
    OeeRcaRequest,
    OeeRcaDimRow,
    _RCA_MAX_ROWS_PER_DIM,
    _RCA_LOW_SAMPLE_THRESHOLD,
    _RCA_MAX_DROP_PERIODS,
    OeeRcaDimension,
    OeeRcaDropPeriod,
    OeeRcaResponse,
    _day_key,
    _date_span_days,
    _six_big_losses,
    _get_dataset,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ─── Routes ─────────────────────────────────────────────────────────────────

@router.post("/oee", response_model=OeeComputeResponse)
async def oee_compute(
    req: OeeComputeRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OeeComputeResponse:
    dataset = await _get_dataset(db, current, req.dataset_id)
    ds_service.validate_columns(dataset, [
        req.planned_time_col, req.operating_time_col, req.total_units_col, req.good_units_col,
        req.date_col, req.machine_col, req.unplanned_downtime_col, req.planned_downtime_col,
        req.minor_stops_col, req.startup_rejects_col,
    ])

    headers = {c["name"] for c in dataset.columns}
    pc, oc, tc, gc = req.planned_time_col, req.operating_time_col, req.total_units_col, req.good_units_col
    machine_col = req.machine_col if (req.machine_col and req.machine_col in headers) else None

    rate_sql = rate_sql_expr(machine_col, req.machine_rates, req.target_rate)
    fully_estimated = rate_sql is None

    split_cols = {
        key: col for key, col in [
            ("unplanned_dt", req.unplanned_downtime_col),
            ("planned_dt", req.planned_downtime_col),
            ("minor_stops", req.minor_stops_col),
            ("startup_rejects", req.startup_rejects_col),
        ] if col and col in headers
    }

    rel = ds_service.dataset_relation(dataset)
    con = ds_service.open_con()
    try:
        ov = oee_overall(con, rel, pc, oc, tc, gc, ideal_sql=rate_sql, extra_sum_cols=split_cols)
        trend_rows = (
            oee_grouped(con, rel, pc, oc, tc, gc, req.date_col, ideal_sql=rate_sql,
                        key_expr=date_bucket_expr(req.date_col))
            if (req.date_col and req.date_col in headers) else []
        )
        machine_rows = (
            oee_grouped(con, rel, pc, oc, tc, gc, machine_col, ideal_sql=rate_sql)
            if machine_col else []
        )
        date_span = (
            _date_span_days(con, rel, req.date_col)
            if (req.date_col and req.date_col in headers) else None
        )
        # Duplicate rows (same date+machine+measurements) are almost always an
        # accidental double-upload/double-paste, not a real second reading —
        # left uncaught, they double-count toward the totals silently.
        duplicate_rows = (
            duplicate_row_count(con, rel, [req.date_col, machine_col, pc, oc, tc, gc])
            if (req.date_col and req.date_col in headers and machine_col) else 0
        )
    finally:
        con.close()

    # A date column with fewer distinct days than its own calendar span means
    # some days have zero rows — trend/TEEP silently treat that as "no
    # production" rather than "no data", which looks the same on a chart but
    # means something very different.
    date_gap_days = max(0, date_span - len(trend_rows)) if date_span else 0

    totals = {"planned": ov["planned"], "operating": ov["operating"], "total": ov["total"], "good": ov["good"]}
    ideal_total = ov["ideal"]
    unrated_rows = 0 if fully_estimated else ov["unrated_rows"]

    # Physically impossible (operating > planned, good > total) means Availability/
    # Quality got silently clamped to 100% in compute_components_ideal — almost
    # always a sign the column mapping is swapped, not a real 100% result. Flag it
    # instead of showing a confident-looking number.
    availability_clamped = totals["planned"] > 0 and totals["operating"] > totals["planned"]
    quality_clamped = totals["total"] > 0 and totals["good"] > totals["total"]

    avail, perf, qual, oee, estimated = compute_components_ideal(
        totals["planned"], totals["operating"], totals["total"], totals["good"],
        ideal_total, fully_estimated,
    )

    trend: list[OeeTrendPoint] = []
    for date_key, g in sorted(trend_rows, key=lambda kv: kv[0]):
        a, p, q, o, _ = compute_components_ideal(
            g["planned"], g["operating"], g["total"], g["good"], g["ideal"], fully_estimated
        )
        trend.append(OeeTrendPoint(
            date=date_key, availability=a, performance=p, quality=q, oee=o, total_units=g["total"],
            invalid_cells=int(g.get("invalid_cells", 0)), blank_cells=int(g.get("blank_cells", 0)),
        ))

    machine_calendar_min = (date_span * 1440.0) if date_span else None

    machines: list[OeeMachineResult] = []
    for mach, g in sorted(machine_rows, key=lambda kv: kv[0]):
        a, p, q, o, _ = compute_components_ideal(
            g["planned"], g["operating"], g["total"], g["good"], g["ideal"], fully_estimated
        )
        m_rev_lost = None
        if req.unit_price and req.unit_price > 0:
            al, pl, ql = revenue_loss_ideal(g["planned"], g["operating"], g["total"], g["good"], g["ideal"], req.unit_price)
            m_rev_lost = round(al + pl + ql, 2)
        m_rate = (req.machine_rates or {}).get(mach)
        rate_used = m_rate if (m_rate and m_rate > 0) else (req.target_rate if (req.target_rate and req.target_rate > 0) else None)
        m_util = m_teep = None
        if machine_calendar_min and machine_calendar_min > 0:
            m_util = round(max(0.0, min(1.0, g["planned"] / machine_calendar_min)) * 100, 2)
            m_teep = round(o * m_util / 100.0, 2)
        machines.append(OeeMachineResult(
            machine=mach, availability=a, performance=p, quality=q, oee=o,
            total_units=g["total"], good_units=g["good"],
            total_planned=g["planned"], total_operating=g["operating"],
            revenue_lost=m_rev_lost,
            invalid_cells=int(g.get("invalid_cells", 0)), blank_cells=int(g.get("blank_cells", 0)),
            rate_used=rate_used, utilization=m_util, teep=m_teep,
        ))

    teep_result: Optional[TeepResult] = None
    if date_span and date_span > 0 and totals["planned"] > 0:
        machine_count = max(1, len(machine_rows)) if machine_col else 1
        calendar_min = date_span * 1440.0 * machine_count
        utilization_frac = max(0.0, min(1.0, totals["planned"] / calendar_min))
        teep_result = TeepResult(
            teep=round((oee / 100.0) * utilization_frac * 100, 2),
            utilization=round(utilization_frac * 100, 2),
            calendar_days=date_span,
            calendar_minutes=round(calendar_min, 1),
            machine_count=machine_count,
            machine_count_assumed=not machine_col,
        )

    six_big_losses = _six_big_losses(totals, ideal_total, fully_estimated, ov, split_cols, req.unit_price)

    revenue_impact: Optional[RevenueImpact] = None
    if req.unit_price and req.unit_price > 0:
        al, pl, ql = revenue_loss_ideal(
            totals["planned"], totals["operating"], totals["total"], totals["good"],
            ideal_total, req.unit_price,
        )
        total_lost = al + pl + ql

        distinct_days = {_day_key(k) for k, _ in trend_rows if _day_key(k)}
        if distinct_days:
            period_days = float(len(distinct_days))
            period_days_estimated = False
        else:
            period_days = max(1.0, ov["row_count"] / 3.0)
            period_days_estimated = True

        daily_rate = round(total_lost / period_days, 2)
        monthly_rate = round(daily_rate * 30, 2)

        current_oee_frac = oee / 100.0
        target_oee_frac = 0.85
        if current_oee_frac < target_oee_frac and totals["planned"] > 0:
            rate_used = (ideal_total / totals["operating"]) if totals["operating"] > 0 else 0
            potential_good_at_target = totals["planned"] * rate_used * target_oee_frac
            benchmark_saving = max(0.0, (potential_good_at_target - totals["good"]) * req.unit_price)
        else:
            benchmark_saving = 0.0

        per_machine_impact = []
        for mach, g in sorted(machine_rows, key=lambda kv: kv[0]):
            mal, mpl, mql = revenue_loss_ideal(g["planned"], g["operating"], g["total"], g["good"], g["ideal"], req.unit_price)
            per_machine_impact.append(RevenueImpactMachine(
                machine=str(mach),
                total_lost=round(mal + mpl + mql, 2),
                availability_lost=round(mal, 2),
                performance_lost=round(mpl, 2),
                quality_lost=round(mql, 2),
            ))
        per_machine_impact.sort(key=lambda x: x.total_lost, reverse=True)

        revenue_impact = RevenueImpact(
            unit_price=req.unit_price,
            total_lost=round(total_lost, 2),
            availability_lost=round(al, 2),
            performance_lost=round(pl, 2),
            quality_lost=round(ql, 2),
            daily_rate=daily_rate,
            monthly_rate=monthly_rate,
            benchmark_saving=round(benchmark_saving, 2),
            period_days=round(period_days, 1),
            period_days_estimated=period_days_estimated,
            performance_estimated=estimated,
            per_machine=per_machine_impact,
        )

    return OeeComputeResponse(
        availability=avail, performance=perf, quality=qual, oee=oee,
        performance_estimated=estimated, unrated_rows=unrated_rows,
        total_planned=totals["planned"], total_operating=totals["operating"],
        total_units=totals["total"], good_units=totals["good"],
        row_count=ov["row_count"], invalid_cells=ov["invalid_cells"],
        blank_cells=ov["blank_cells"], rows_with_issues=ov["rows_with_issues"],
        availability_clamped=availability_clamped, quality_clamped=quality_clamped,
        date_gap_days=date_gap_days, duplicate_rows=duplicate_rows,
        trend=trend, machines=machines, revenue_impact=revenue_impact,
        teep=teep_result, six_big_losses=six_big_losses,
    )


@router.post("/oee/rca", response_model=OeeRcaResponse)
async def oee_rca(
    req: OeeRcaRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OeeRcaResponse:
    dataset = await _get_dataset(db, current, req.dataset_id)
    ds_service.validate_columns(dataset, [
        req.planned_time_col, req.operating_time_col, req.total_units_col, req.good_units_col,
        req.date_col, req.machine_col, req.shift_col, req.operator_col, req.material_batch_col,
    ])

    headers = {c["name"] for c in dataset.columns}
    dim_col_map: dict[str, Optional[str]] = {
        "Machine": req.machine_col,
        "Shift": req.shift_col,
        "Operator": req.operator_col,
        "Material Batch": req.material_batch_col,
    }
    active_dims: dict[str, str] = {
        name: col for name, col in dim_col_map.items() if col and col in headers
    }

    pc, oc, tc, gc = req.planned_time_col, req.operating_time_col, req.total_units_col, req.good_units_col
    machine_col = req.machine_col if (req.machine_col and req.machine_col in headers) else None
    rate_sql = rate_sql_expr(machine_col, req.machine_rates, req.target_rate)
    fully_estimated = rate_sql is None

    rel = ds_service.dataset_relation(dataset)
    con = ds_service.open_con()
    try:
        ov = oee_overall(con, rel, pc, oc, tc, gc, ideal_sql=rate_sql)
        dim_results = {
            name: oee_grouped(con, rel, pc, oc, tc, gc, col, with_count=True, ideal_sql=rate_sql)
            for name, col in active_dims.items()
        }
        trend_rows = (
            oee_grouped(con, rel, pc, oc, tc, gc, req.date_col, ideal_sql=rate_sql,
                        key_expr=date_bucket_expr(req.date_col))
            if (req.date_col and req.date_col in headers) else []
        )
    finally:
        con.close()

    totals = {"planned": ov["planned"], "operating": ov["operating"], "total": ov["total"], "good": ov["good"]}

    overall_avail, overall_perf, overall_qual, overall_oee, overall_perf_estimated = compute_components_ideal(
        totals["planned"], totals["operating"], totals["total"], totals["good"],
        ov["ideal"], fully_estimated,
    )

    dimensions: list[OeeRcaDimension] = []
    for dim_name, dim_col in active_dims.items():
        dim_rows: list[OeeRcaDimRow] = []
        for label, g in dim_results[dim_name]:
            a, p, q, o, est = compute_components_ideal(
                g["planned"], g["operating"], g["total"], g["good"], g["ideal"], fully_estimated
            )
            dim_rows.append(OeeRcaDimRow(
                label=label, oee=o, availability=a, performance=p, quality=q,
                total_units=g["total"], good_units=g["good"], row_count=int(g["count"]),
                delta_oee=round(o - overall_oee, 2),
                invalid_cells=int(g.get("invalid_cells", 0)), blank_cells=int(g.get("blank_cells", 0)),
                performance_estimated=est,
                low_sample=int(g["count"]) < _RCA_LOW_SAMPLE_THRESHOLD,
            ))
        dim_rows.sort(key=lambda r: r.oee)
        total_rows = len(dim_rows)
        dim_rows = dim_rows[:_RCA_MAX_ROWS_PER_DIM]
        dimensions.append(OeeRcaDimension(name=dim_name, col=dim_col, rows=dim_rows, total_rows=total_rows))

    drop_periods: list[OeeRcaDropPeriod] = []
    for date_key, g in sorted(trend_rows, key=lambda kv: kv[0]):
        _, _, _, o, _ = compute_components_ideal(
            g["planned"], g["operating"], g["total"], g["good"], g["ideal"], fully_estimated
        )
        if o < overall_oee:
            drop_periods.append(OeeRcaDropPeriod(date=date_key, oee=o, delta_oee=round(o - overall_oee, 2)))
    drop_periods.sort(key=lambda p: p.oee)
    drop_periods_total = len(drop_periods)
    drop_periods = drop_periods[:_RCA_MAX_DROP_PERIODS]

    return OeeRcaResponse(
        overall_oee=overall_oee, overall_availability=overall_avail,
        overall_performance=overall_perf, overall_quality=overall_qual,
        invalid_cells=ov["invalid_cells"], blank_cells=ov["blank_cells"],
        rows_with_issues=ov["rows_with_issues"], dimensions=dimensions,
        drop_periods=drop_periods, drop_periods_total=drop_periods_total,
        overall_performance_estimated=overall_perf_estimated,
    )
