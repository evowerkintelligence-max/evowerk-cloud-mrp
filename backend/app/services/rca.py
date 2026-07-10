from __future__ import annotations

import uuid
from dataclasses import dataclass

from scipy.stats import chi2_contingency
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Item
from app.models.procurement import Supplier
from app.services.inventory_analytics import InventoryAnalyticsLine, compute_inventory_analytics

# Ported from a desktop app's rca_engine.py (rank machine/shift/operator by
# OEE delta vs overall, weighted by a hand-rolled severity+support confidence
# score) - rewired onto MRP's stockout/low status and upgraded to a real
# statistical test: a chi-square test of independence (industry-standard for
# "is this categorical outcome associated with this categorical dimension")
# plus standardized residuals per dimension value (the standard post-hoc way
# to see WHICH values drive a significant result), instead of a bespoke score.
AT_RISK_STATUSES = ("stockout", "low")
DIMENSIONS = ("supplier", "item_type", "abc_class")
# Standard rule of thumb: chi-square is unreliable when an expected cell count
# falls below this - flagged, not silently hidden.
MIN_EXPECTED_FREQUENCY = 5.0
# |standardized residual| > 1.96 <=> two-tailed 95% significance under a
# standard normal approximation (same convention as inventory_analytics'
# safety-stock z-scores).
SIGNIFICANCE_THRESHOLD = 1.96


@dataclass
class RcaDimensionValue:
    value: str
    item_count: int
    at_risk_count: int
    expected_at_risk_count: float
    standardized_residual: float
    is_significant: bool


@dataclass
class RcaResult:
    dimension: str
    method: str  # "chi_square_independence" | "insufficient_data"
    chi2_statistic: float | None
    p_value: float | None
    degrees_of_freedom: int | None
    low_expected_frequency_warning: bool
    values: list[RcaDimensionValue]


def _empty_result(dimension: str) -> RcaResult:
    return RcaResult(
        dimension=dimension,
        method="insufficient_data",
        chi2_statistic=None,
        p_value=None,
        degrees_of_freedom=None,
        low_expected_frequency_warning=False,
        values=[],
    )


async def _dimension_value_by_item(
    db: AsyncSession, org_id: uuid.UUID, dimension: str, lines: list[InventoryAnalyticsLine]
) -> dict[uuid.UUID, str]:
    if dimension == "abc_class":
        return {line.item_id: (line.abc_class or "unclassified") for line in lines}

    item_ids = {line.item_id for line in lines}
    if dimension == "item_type":
        rows = await db.execute(
            select(Item.id, Item.item_type).where(Item.org_id == org_id, Item.id.in_(item_ids))
        )
        return dict(rows.all())

    # dimension == "supplier"
    rows = await db.execute(
        select(Item.id, Supplier.name)
        .outerjoin(Supplier, Supplier.id == Item.preferred_supplier_id)
        .where(Item.org_id == org_id, Item.id.in_(item_ids))
    )
    return {item_id: (name or "unassigned") for item_id, name in rows.all()}


async def compute_rca(db: AsyncSession, org_id: uuid.UUID, dimension: str) -> RcaResult:
    """Is an item's stockout/low status associated with `dimension` (which
    supplier, item type, or ABC class)? A chi-square test of independence
    over a [dimension value] x [at-risk, not-at-risk] contingency table,
    followed by the standardized residual per value - the standard way to
    read off which specific values are driving a significant association,
    ranked by |residual| (biggest anomaly first)."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"Unknown RCA dimension: {dimension}")

    lines = await compute_inventory_analytics(db, org_id)
    if not lines:
        return _empty_result(dimension)

    value_by_item = await _dimension_value_by_item(db, org_id, dimension, lines)

    counts: dict[str, list[int]] = {}
    for line in lines:
        value = value_by_item.get(line.item_id, "unassigned")
        bucket = counts.setdefault(value, [0, 0])
        bucket[0 if line.status in AT_RISK_STATUSES else 1] += 1

    if len(counts) < 2:
        # Chi-square tests association between >= 2 categories; with only one
        # dimension value present there's nothing to compare it against.
        return _empty_result(dimension)

    table = [bucket for bucket in counts.values()]
    try:
        chi2, p_value, dof, expected = chi2_contingency(table)
    except ValueError:
        # e.g. every item is (or isn't) at-risk, so a whole column is zero -
        # scipy can't compute an expected-frequency table from that.
        return _empty_result(dimension)

    low_expected = bool((expected < MIN_EXPECTED_FREQUENCY).any())

    values: list[RcaDimensionValue] = []
    for (value_name, (at_risk, not_at_risk)), expected_row in zip(counts.items(), expected, strict=True):
        expected_at_risk = float(expected_row[0])
        residual = (at_risk - expected_at_risk) / (expected_at_risk**0.5) if expected_at_risk > 0 else 0.0
        values.append(
            RcaDimensionValue(
                value=value_name,
                item_count=at_risk + not_at_risk,
                at_risk_count=at_risk,
                expected_at_risk_count=expected_at_risk,
                standardized_residual=residual,
                is_significant=abs(residual) > SIGNIFICANCE_THRESHOLD,
            )
        )
    values.sort(key=lambda v: abs(v.standardized_residual), reverse=True)

    return RcaResult(
        dimension=dimension,
        method="chi_square_independence",
        chi2_statistic=float(chi2),
        p_value=float(p_value),
        degrees_of_freedom=int(dof),
        low_expected_frequency_warning=low_expected,
        values=values,
    )
