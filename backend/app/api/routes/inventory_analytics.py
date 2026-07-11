from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.bom import Item
from app.services.inventory_analytics import InventoryAnalyticsLine, compute_inventory_analytics
from app.services.morning_brief import (
    BriefLine,
    LotAlertLine,
    compute_lot_alerts,
    compute_morning_brief,
)
from app.services.rca import DIMENSIONS, RcaDimensionValue, RcaResult, compute_rca
from app.services.scenario import ScenarioDelta, compare_lead_time_scenario

router = APIRouter(prefix="/inventory", tags=["inventory-analytics"])


class InventoryAnalyticsLineResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    on_hand: Decimal
    avg_daily_demand: float
    demand_std: float
    demand_std_source: str
    lead_time_days: int | None
    safety_stock: float | None
    reorder_point: float | None
    days_of_supply: float | None
    abc_class: str | None
    xyz_class: str | None
    status: str


class InventoryAnalyticsResponse(BaseModel):
    lines: list[InventoryAnalyticsLineResponse]


def _to_response(line: InventoryAnalyticsLine) -> InventoryAnalyticsLineResponse:
    return InventoryAnalyticsLineResponse(
        item_id=str(line.item_id),
        item_sku=line.item_sku,
        item_name=line.item_name,
        on_hand=line.on_hand,
        avg_daily_demand=line.avg_daily_demand,
        demand_std=line.demand_std,
        demand_std_source=line.demand_std_source,
        lead_time_days=line.lead_time_days,
        safety_stock=line.safety_stock,
        reorder_point=line.reorder_point,
        days_of_supply=line.days_of_supply,
        abc_class=line.abc_class,
        xyz_class=line.xyz_class,
        status=line.status,
    )


@router.get("/analytics", response_model=InventoryAnalyticsResponse)
async def get_inventory_analytics(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read-only per-item inventory analytics: reorder point, safety stock,
    days-of-supply, ABC/XYZ classification and a status flag. See
    app.services.inventory_analytics for the underlying model."""
    lines = await compute_inventory_analytics(db, current.org_id)
    return InventoryAnalyticsResponse(lines=[_to_response(line) for line in lines])


class BriefLineResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    status: str
    on_hand: Decimal
    days_of_supply: float | None
    status_last_week: str | None
    days_of_supply_last_week: float | None
    days_of_supply_delta: float | None
    is_new_this_week: bool


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


class MorningBriefResponse(BaseModel):
    lines: list[BriefLineResponse]
    lot_alerts: list[LotAlertResponse]


def _brief_to_response(line: BriefLine) -> BriefLineResponse:
    return BriefLineResponse(
        item_id=str(line.item_id),
        item_sku=line.item_sku,
        item_name=line.item_name,
        status=line.status,
        on_hand=line.on_hand,
        days_of_supply=line.days_of_supply,
        status_last_week=line.status_last_week,
        days_of_supply_last_week=line.days_of_supply_last_week,
        days_of_supply_delta=line.days_of_supply_delta,
        is_new_this_week=line.is_new_this_week,
    )


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


@router.get("/morning-brief", response_model=MorningBriefResponse)
async def get_morning_brief(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Daily digest of items needing attention (stockout/low/excess/slow-moving),
    ranked by severity, compared against the same figures 7 days ago where
    available, plus lots on hold or expiring soon. See
    app.services.morning_brief."""
    lines = await compute_morning_brief(db, current.org_id)
    lot_alerts = await compute_lot_alerts(db, current.org_id)
    return MorningBriefResponse(
        lines=[_brief_to_response(line) for line in lines],
        lot_alerts=[_lot_alert_to_response(alert) for alert in lot_alerts],
    )


class RcaDimensionValueResponse(BaseModel):
    value: str
    item_count: int
    at_risk_count: int
    expected_at_risk_count: float
    standardized_residual: float
    is_significant: bool


class RcaResponse(BaseModel):
    dimension: str
    # chi_square_independence | insufficient_data
    method: str
    chi2_statistic: float | None
    p_value: float | None
    degrees_of_freedom: int | None
    low_expected_frequency_warning: bool
    values: list[RcaDimensionValueResponse]


def _rca_value_to_response(value: RcaDimensionValue) -> RcaDimensionValueResponse:
    return RcaDimensionValueResponse(
        value=value.value,
        item_count=value.item_count,
        at_risk_count=value.at_risk_count,
        expected_at_risk_count=value.expected_at_risk_count,
        standardized_residual=value.standardized_residual,
        is_significant=value.is_significant,
    )


def _rca_to_response(result: RcaResult) -> RcaResponse:
    return RcaResponse(
        dimension=result.dimension,
        method=result.method,
        chi2_statistic=result.chi2_statistic,
        p_value=result.p_value,
        degrees_of_freedom=result.degrees_of_freedom,
        low_expected_frequency_warning=result.low_expected_frequency_warning,
        values=[_rca_value_to_response(v) for v in result.values],
    )


@router.get("/rca", response_model=RcaResponse)
async def get_rca(
    dimension: str = "supplier",
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Is an item's stockout/low status associated with `dimension`
    (supplier | item_type | abc_class)? A chi-square test of independence,
    ranked by standardized residual per value (biggest anomaly first) - see
    app.services.rca."""
    if dimension not in DIMENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"dimension must be one of {sorted(DIMENSIONS)}")
    result = await compute_rca(db, current.org_id, dimension)
    return _rca_to_response(result)


class ScenarioCompareRequest(BaseModel):
    # item_id (string) -> hypothetical lead_time_days. Not persisted anywhere.
    lead_time_overrides: dict[str, int]


class ScenarioDeltaResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    baseline_lead_time_days: int | None
    scenario_lead_time_days: int | None
    baseline_reorder_point: float | None
    scenario_reorder_point: float | None
    reorder_point_delta: float | None
    baseline_safety_stock: float | None
    scenario_safety_stock: float | None
    safety_stock_delta: float | None
    baseline_days_of_supply: float | None
    scenario_days_of_supply: float | None
    days_of_supply_delta: float | None
    baseline_status: str
    scenario_status: str
    classification: str


class ScenarioCompareResponse(BaseModel):
    deltas: list[ScenarioDeltaResponse]


def _scenario_delta_to_response(delta: ScenarioDelta) -> ScenarioDeltaResponse:
    return ScenarioDeltaResponse(
        item_id=str(delta.item_id),
        item_sku=delta.item_sku,
        item_name=delta.item_name,
        baseline_lead_time_days=delta.baseline_lead_time_days,
        scenario_lead_time_days=delta.scenario_lead_time_days,
        baseline_reorder_point=delta.baseline_reorder_point,
        scenario_reorder_point=delta.scenario_reorder_point,
        reorder_point_delta=delta.reorder_point_delta,
        baseline_safety_stock=delta.baseline_safety_stock,
        scenario_safety_stock=delta.scenario_safety_stock,
        safety_stock_delta=delta.safety_stock_delta,
        baseline_days_of_supply=delta.baseline_days_of_supply,
        scenario_days_of_supply=delta.scenario_days_of_supply,
        days_of_supply_delta=delta.days_of_supply_delta,
        baseline_status=delta.baseline_status,
        scenario_status=delta.scenario_status,
        classification=delta.classification,
    )


@router.post("/scenario-compare", response_model=ScenarioCompareResponse)
async def post_scenario_compare(
    body: ScenarioCompareRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """What-if: how would reorder point / safety stock / days-of-supply /
    status change for these items with a different lead time? Nothing is
    persisted - see app.services.scenario."""
    if not body.lead_time_overrides:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide at least one lead_time override")

    overrides: dict[uuid.UUID, int] = {}
    for raw_item_id, lead_time_days in body.lead_time_overrides.items():
        try:
            item_id = uuid.UUID(raw_item_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid item_id: {raw_item_id}") from None
        if lead_time_days < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"lead_time_days must be non-negative (item {raw_item_id})"
            )
        overrides[item_id] = lead_time_days

    found = (
        await db.execute(
            select(Item.id).where(Item.org_id == current.org_id, Item.id.in_(overrides.keys()))
        )
    ).scalars().all()
    missing = overrides.keys() - set(found)
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown item(s) for this org: {', '.join(str(m) for m in missing)}"
        )

    deltas = await compare_lead_time_scenario(db, current.org_id, overrides)
    return ScenarioCompareResponse(deltas=[_scenario_delta_to_response(d) for d in deltas])
