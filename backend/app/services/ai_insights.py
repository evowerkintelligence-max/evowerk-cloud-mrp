from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.demand_forecasting import DemandForecastResult, compute_demand_forecast
from app.services.inventory_analytics import InventoryAnalyticsLine, compute_inventory_analytics
from app.services.rca import DIMENSIONS, RcaResult, compute_rca
from app.services.scenario import ScenarioDelta, compare_lead_time_scenario

# Rules-first NLQ over Phases 1-6's own outputs: keyword intent detection (no
# ML/LLM call - same "rules-first planner" shape as a sibling app's
# ai_insights_engine.plan_from_question), then a narrative composed from the
# same structured facts those phases already compute (compute_inventory_analytics
# / compute_demand_forecast / compute_rca / compare_lead_time_scenario). No new
# numbers are invented here - this is a text layer over existing outputs.
_STATUS_WORDS = ("stockout", "low", "excess", "slow_moving", "healthy", "no_data")
_ABC_PATTERN = re.compile(r"\bclass\s*([abc])\b", re.IGNORECASE)
_LEAD_TIME_NUMBER = re.compile(r"lead\s*time\D{0,15}(\d+)", re.IGNORECASE)


@dataclass
class AiInsightResult:
    question: str
    intents: list[str]
    narrative: str
    warnings: list[str] = field(default_factory=list)


def detect_intents(question: str) -> list[str]:
    """Keyword-membership intent detection, same pattern as a rules-first
    planner: a question can trigger more than one intent."""
    q = question.lower()
    intents: list[str] = []
    if any(k in q for k in ("compare", "changed", "difference", "vs", "versus", "why", "cause")):
        intents.append("compare")
    if any(k in q for k in ("forecast", "predict", "next", "expected", "projection")):
        intents.append("forecast")
    if "what if" in q or "scenario" in q:
        intents.append("scenario")
    if any(k in q for k in ("health", "status", "summary", "overview", "how are", "how is", "how's")):
        intents.append("health")
    # A question that matched no keyword still deserves a useful answer -
    # default to the same "how's inventory doing" view morning_brief uses.
    if not intents:
        intents.append("health")
    return intents


def _matched_items(lines: list[InventoryAnalyticsLine], q_lower: str) -> list[InventoryAnalyticsLine]:
    """Items the question names by sku or name.
    ponytail: substring match against data already in hand, not NER - a very
    short sku could false-match inside another word; add word-boundary/fuzzy
    matching if that becomes a real complaint."""
    return [ln for ln in lines if ln.item_sku.lower() in q_lower or ln.item_name.lower() in q_lower]


def _filter_lines(lines: list[InventoryAnalyticsLine], q_lower: str) -> list[InventoryAnalyticsLine]:
    """NLQ-to-filter: narrow analytics rows to whatever the question named -
    a status, an ABC class, or specific item(s) - using the same rows already
    computed rather than a separate filter/query engine."""
    filtered = lines

    statuses = [s for s in _STATUS_WORDS if re.search(rf"\b{s.replace('_', ' ')}\b", q_lower)]
    if statuses:
        filtered = [ln for ln in filtered if ln.status in statuses]

    abc_match = _ABC_PATTERN.search(q_lower)
    if abc_match:
        letter = abc_match.group(1).upper()
        filtered = [ln for ln in filtered if ln.abc_class == letter]

    items = _matched_items(lines, q_lower)
    if items:
        item_ids = {ln.item_id for ln in items}
        filtered = [ln for ln in filtered if ln.item_id in item_ids]

    return filtered


def _health_narrative(lines: list[InventoryAnalyticsLine]) -> str:
    if not lines:
        return "No items match that question."
    counts: dict[str, int] = {}
    for ln in lines:
        counts[ln.status] = counts.get(ln.status, 0) + 1
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items(), key=lambda kv: -kv[1]))
    worst = sorted(
        (ln for ln in lines if ln.status in ("stockout", "low")), key=lambda ln: ln.days_of_supply or 0.0
    )[:3]
    text = f"{len(lines)} item(s): {summary}."
    if worst:
        parts = [
            f"{ln.item_sku} ({ln.status}, {ln.days_of_supply:.0f}d supply)"
            if ln.days_of_supply is not None
            else f"{ln.item_sku} ({ln.status})"
            for ln in worst
        ]
        text += f" Most urgent: {', '.join(parts)}."
    return text


def _compare_narrative(rca: RcaResult) -> str:
    if rca.method == "insufficient_data":
        return f"Not enough data to compare by {rca.dimension} yet."
    significant = [v for v in rca.values if v.is_significant]
    if not significant:
        return f"No {rca.dimension} stands out as significantly associated with stockouts/low stock (p={rca.p_value:.3f})."
    top = significant[0]
    direction = "more" if top.standardized_residual > 0 else "fewer"
    return (
        f"{top.value} has {direction} at-risk items than expected "
        f"({top.at_risk_count}/{top.item_count}, p={rca.p_value:.3f}) - the strongest {rca.dimension} effect."
    )


def _forecast_narrative(line: InventoryAnalyticsLine, forecast: DemandForecastResult) -> str:
    if forecast.method_used == "no_data" or not forecast.points:
        return f"{line.item_sku}: not enough consumption history to forecast."
    first, last = forecast.points[0], forecast.points[-1]
    trend = "flat"
    if last.yhat > first.yhat * 1.05:
        trend = "rising"
    elif last.yhat < first.yhat * 0.95:
        trend = "falling"
    return (
        f"{line.item_sku}: {forecast.method_used} forecast over {len(forecast.points)} days, "
        f"demand looks {trend} (~{first.yhat:.1f}/day now to ~{last.yhat:.1f}/day)."
    )


def _scenario_narrative(delta: ScenarioDelta | None, line: InventoryAnalyticsLine) -> str:
    if delta is None:
        return f"{line.item_sku}: couldn't run that scenario."
    return (
        f"{line.item_sku}: at a {delta.scenario_lead_time_days}-day lead time, status would go from "
        f"{delta.baseline_status} to {delta.scenario_status} ({delta.classification})."
    )


async def answer_question(db: AsyncSession, org_id: uuid.UUID, question: str) -> AiInsightResult:
    q_lower = question.lower()
    intents = detect_intents(question)
    lines = await compute_inventory_analytics(db, org_id)
    item_matches = _matched_items(lines, q_lower)

    narrative_parts: list[str] = []
    warnings: list[str] = []

    if "health" in intents:
        narrative_parts.append(_health_narrative(_filter_lines(lines, q_lower)))

    if "compare" in intents:
        dimension = next((d for d in DIMENSIONS if d.replace("_", " ") in q_lower or d in q_lower), "supplier")
        rca = await compute_rca(db, org_id, dimension)
        narrative_parts.append(_compare_narrative(rca))

    if "forecast" in intents:
        if len(item_matches) == 1:
            forecast = await compute_demand_forecast(db, org_id, item_matches[0].item_id)
            narrative_parts.append(_forecast_narrative(item_matches[0], forecast))
        else:
            warnings.append("Forecast needs exactly one item named by sku or name in the question.")

    if "scenario" in intents:
        lead_time_match = _LEAD_TIME_NUMBER.search(q_lower)
        if len(item_matches) == 1 and lead_time_match:
            override = {item_matches[0].item_id: int(lead_time_match.group(1))}
            deltas = await compare_lead_time_scenario(db, org_id, override)
            narrative_parts.append(_scenario_narrative(deltas[0] if deltas else None, item_matches[0]))
        else:
            warnings.append(
                'Scenario needs one item (by sku/name) and a lead time in days, '
                'e.g. "what if ITEM-1\'s lead time were 10 days".'
            )

    narrative = " ".join(narrative_parts) or "I couldn't compute anything for that question."
    return AiInsightResult(question=question, intents=intents, narrative=narrative, warnings=warnings)
