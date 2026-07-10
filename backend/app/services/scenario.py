from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.inventory_analytics import InventoryAnalyticsLine, compute_inventory_analytics

# Ported from a desktop app's scenario_engine.py (diff two KPI snapshots,
# direction-aware improvement/regression classification) - rewired onto
# MRP's own inventory-position metrics instead of a generic KPI store.
#
# Scoped to lead-time what-if for now (the one lever inventory_analytics
# already supports overriding without touching the DB - see its
# lead_time_overrides parameter). Unlike Phase 3's chi-square test, there's no
# library that fits "diff two numbers and classify the direction" better than
# a plain subtraction - reaching for one here would be the overengineering
# this phase was explicitly told to avoid.
#
# Distance from 0 = how far a status is from "healthy" (an ordinal risk
# score, not a real magnitude) - the sign marks which side (under- vs
# over-stocked) but classification only compares magnitude, since "less risk,
# whichever side" is what "improved" means here. no_data has no signal either
# way, so it is treated as neutral (0), same as healthy.
_STATUS_RISK_SCORE = {
    "stockout": -2,
    "low": -1,
    "no_data": 0,
    "healthy": 0,
    "slow_moving": 1,
    "excess": 2,
}


@dataclass
class ScenarioDelta:
    item_id: uuid.UUID
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
    classification: str  # "improved" | "regressed" | "unchanged"


def _classify(baseline_status: str, scenario_status: str) -> str:
    baseline_risk = abs(_STATUS_RISK_SCORE[baseline_status])
    scenario_risk = abs(_STATUS_RISK_SCORE[scenario_status])
    if scenario_risk < baseline_risk:
        return "improved"
    if scenario_risk > baseline_risk:
        return "regressed"
    return "unchanged"


def _delta(scenario: float | None, baseline: float | None) -> float | None:
    if scenario is None or baseline is None:
        return None
    return scenario - baseline


async def compare_lead_time_scenario(
    db: AsyncSession, org_id: uuid.UUID, lead_time_overrides: dict[uuid.UUID, int]
) -> list[ScenarioDelta]:
    """What-if: how would reorder point / safety stock / days-of-supply /
    status change for these items if their lead time were different? Nothing
    is persisted - both sides are computed fresh from the same live data,
    differing only in which lead_time_days compute_inventory_analytics uses."""
    baseline_lines = await compute_inventory_analytics(db, org_id)
    scenario_lines = await compute_inventory_analytics(db, org_id, lead_time_overrides=lead_time_overrides)

    baseline_by_item: dict[uuid.UUID, InventoryAnalyticsLine] = {line.item_id: line for line in baseline_lines}

    deltas: list[ScenarioDelta] = []
    for scenario_line in scenario_lines:
        if scenario_line.item_id not in lead_time_overrides:
            continue  # only overridden items can possibly differ - skip the noise
        baseline_line = baseline_by_item.get(scenario_line.item_id)
        if baseline_line is None:
            continue  # defensive: item vanished between the two calls
        deltas.append(
            ScenarioDelta(
                item_id=scenario_line.item_id,
                item_sku=scenario_line.item_sku,
                item_name=scenario_line.item_name,
                baseline_lead_time_days=baseline_line.lead_time_days,
                scenario_lead_time_days=scenario_line.lead_time_days,
                baseline_reorder_point=baseline_line.reorder_point,
                scenario_reorder_point=scenario_line.reorder_point,
                reorder_point_delta=_delta(scenario_line.reorder_point, baseline_line.reorder_point),
                baseline_safety_stock=baseline_line.safety_stock,
                scenario_safety_stock=scenario_line.safety_stock,
                safety_stock_delta=_delta(scenario_line.safety_stock, baseline_line.safety_stock),
                baseline_days_of_supply=baseline_line.days_of_supply,
                scenario_days_of_supply=scenario_line.days_of_supply,
                days_of_supply_delta=_delta(scenario_line.days_of_supply, baseline_line.days_of_supply),
                baseline_status=baseline_line.status,
                scenario_status=scenario_line.status,
                classification=_classify(baseline_line.status, scenario_line.status),
            )
        )

    # Most impactful first: regressions before improvements before unchanged,
    # then by |reorder_point_delta| within each group.
    priority = {"regressed": 0, "improved": 1, "unchanged": 2}
    deltas.sort(
        key=lambda d: (priority[d.classification], -abs(d.reorder_point_delta or 0.0), d.item_sku)
    )
    return deltas
