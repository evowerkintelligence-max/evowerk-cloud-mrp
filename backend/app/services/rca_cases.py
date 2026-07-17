"""RCA case-management workflow: cause tree, evidence engine, Pareto,
timeline, and CAPA action verification, all computed over a Dataset's own
columns using the shared OEE math in app.analytics.oee_calc (the same engine
app/api/routes/oee.py uses) - never a second OEE implementation.

Ported from the desktop app's rca_engine.py. Distinct from
app/services/rca.py (an unrelated inventory chi-square check).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import compute_components_ideal, inv_expr, oee_grouped, oee_overall, qi, val_expr
from app.models.dataset import Dataset
from app.models.rca_case import RcaAction, RcaCase, RcaEvent, RcaNode
from app.services import datasets as ds_service

# 6M (Ishikawa/fishbone) manufacturing cause taxonomy - static, deterministic,
# not a model call. Ported from the desktop app's rca_taxonomy.py.
RCA_TAXONOMY: dict[str, list[str]] = {
    "Machine": [
        "Equipment failure / breakdown", "Tooling wear", "Calibration drift",
        "Preventive maintenance overdue", "Design limitation", "Software / control fault",
    ],
    "Method": [
        "Procedure not followed", "Procedure inadequate or outdated",
        "Changeover / setup process", "Missing standard work", "Scheduling / sequencing issue",
    ],
    "Material": [
        "Raw material defect", "Wrong material used", "Supplier variability",
        "Material shortage / stockout", "Improper storage / handling",
    ],
    "Manpower": [
        "Training gap", "Operator error", "Staffing / shift coverage",
        "Fatigue", "Communication breakdown",
    ],
    "Measurement": [
        "Sensor / gauge inaccuracy", "Inspection method inadequate",
        "Sampling error", "Data entry / logging error",
    ],
    "Environment": [
        "Temperature / humidity", "Contamination", "Layout / ergonomics",
        "Utility interruption (power, air, etc.)", "Vibration / noise",
    ],
}

_MAX_CANDIDATES = 25
_SEVERITY_ANCHOR_PTS = 50.0
_SUPPORT_ANCHOR_PCT = 20.0


# ─── Case / node / action / event CRUD ─────────────────────────────────────

async def get_case_or_404(db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID) -> RcaCase:
    result = await db.execute(select(RcaCase).where(RcaCase.org_id == org_id, RcaCase.id == case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise ValueError("RCA case not found")
    return case


async def get_node_or_404(db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, node_id: uuid.UUID) -> RcaNode:
    result = await db.execute(
        select(RcaNode).where(RcaNode.org_id == org_id, RcaNode.case_id == case_id, RcaNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise ValueError("RCA node not found")
    return node


async def get_action_or_404(
    db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, action_id: uuid.UUID
) -> RcaAction:
    result = await db.execute(
        select(RcaAction).where(RcaAction.org_id == org_id, RcaAction.case_id == case_id, RcaAction.id == action_id)
    )
    action = result.scalar_one_or_none()
    if action is None:
        raise ValueError("RCA action not found")
    return action


async def log_event(
    db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, event_type: str, summary: str, actor: str | None
) -> None:
    db.add(RcaEvent(org_id=org_id, case_id=case_id, event_type=event_type, summary=summary, actor=actor))
    await db.flush()


async def create_case(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    actor_label: str | None,
    title: str,
    problem_statement: str,
    dataset_id: uuid.UUID,
    owner: str | None,
    trigger_context: dict,
) -> RcaCase:
    # Fail fast if the dataset doesn't resolve - a case with a dead dataset_id
    # is useless (every evidence/pareto call would 404 later instead of now).
    await ds_service.get_dataset_or_404(db, org_id, dataset_id)

    case = RcaCase(
        org_id=org_id, dataset_id=dataset_id, title=title,
        problem_statement=problem_statement, status="open",
        owner=owner or actor_label, trigger_context=trigger_context or {}, created_by=created_by,
    )
    db.add(case)
    await db.flush()

    # Auto-seed the tree's root "problem" node so an investigator always has a
    # starting point.
    root_text = (problem_statement or "").strip() or title
    db.add(RcaNode(org_id=org_id, case_id=case.id, parent_id=None, text=root_text, node_type="problem"))
    await log_event(db, org_id, case.id, "case_created", f"Case created: {title}", actor_label)
    await db.commit()
    await db.refresh(case)
    return case


async def update_case(
    db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, actor_label: str | None, patch: dict
) -> RcaCase:
    case = await get_case_or_404(db, org_id, case_id)
    new_status = patch.get("status")
    if new_status == "closed":
        case.closed_at = datetime.now(timezone.utc)
    elif new_status == "open":
        case.closed_at = None
    for key, value in patch.items():
        setattr(case, key, value)
    if new_status is not None:
        await log_event(db, org_id, case_id, "status_changed", f"Status changed to {case.status}", actor_label)
    await db.commit()
    await db.refresh(case)
    return case


async def delete_case(db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID) -> None:
    case = await get_case_or_404(db, org_id, case_id)
    await db.delete(case)
    await db.commit()


async def list_cases(db: AsyncSession, org_id: uuid.UUID) -> list[RcaCase]:
    result = await db.execute(select(RcaCase).where(RcaCase.org_id == org_id).order_by(RcaCase.created_at.desc()))
    return list(result.scalars().all())


async def list_nodes(db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID) -> list[RcaNode]:
    result = await db.execute(
        select(RcaNode).where(RcaNode.org_id == org_id, RcaNode.case_id == case_id).order_by(RcaNode.created_at)
    )
    return list(result.scalars().all())


async def create_node(
    db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, actor_label: str | None, **fields
) -> RcaNode:
    case = await get_case_or_404(db, org_id, case_id)
    if fields.get("parent_id"):
        await get_node_or_404(db, org_id, case_id, fields["parent_id"])
    node = RcaNode(org_id=org_id, case_id=case_id, **fields)
    db.add(node)
    await db.flush()
    await log_event(db, org_id, case_id, "node_added", f"Cause added: {node.text}", actor_label)
    # A case sits in "open" until an investigator actually starts building the
    # cause tree - the first real cause (not the auto-seeded root problem node)
    # is what "investigating" means, so advance it automatically rather than
    # relying on a manual status click every case is stuck without.
    if node.node_type != "problem" and case.status == "open":
        case.status = "investigating"
        await log_event(db, org_id, case_id, "status_changed", "Status changed to investigating", actor_label)
    await db.commit()
    await db.refresh(node)
    return node


async def update_node(
    db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, node_id: uuid.UUID, actor_label: str | None, patch: dict
) -> RcaNode:
    node = await get_node_or_404(db, org_id, case_id, node_id)
    before_type, before_status = node.node_type, node.status
    for key, value in patch.items():
        setattr(node, key, value)
    if "node_type" in patch and node.node_type != before_type:
        if node.node_type == "root_cause":
            await log_event(db, org_id, case_id, "root_cause_marked", f"Marked as root cause: {node.text}", actor_label)
        elif before_type == "root_cause":
            await log_event(db, org_id, case_id, "root_cause_unmarked", f"Unmarked as root cause: {node.text}", actor_label)
    if "status" in patch and node.status != before_status:
        await log_event(db, org_id, case_id, "node_status_changed", f"Cause status: {node.text} -> {node.status}", actor_label)
    await db.commit()
    await db.refresh(node)
    return node


async def delete_node(db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, node_id: uuid.UUID, actor_label: str | None) -> None:
    node = await get_node_or_404(db, org_id, case_id, node_id)
    text = node.text
    await db.delete(node)
    await log_event(db, org_id, case_id, "node_deleted", f"Cause deleted: {text}", actor_label)
    await db.commit()


async def list_actions(db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID) -> list[RcaAction]:
    result = await db.execute(
        select(RcaAction).where(RcaAction.org_id == org_id, RcaAction.case_id == case_id).order_by(RcaAction.created_at)
    )
    return list(result.scalars().all())


async def create_action(
    db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, created_by: uuid.UUID, actor_label: str | None, **fields
) -> RcaAction:
    case = await get_case_or_404(db, org_id, case_id)
    if fields.get("node_id"):
        await get_node_or_404(db, org_id, case_id, fields["node_id"])
    action = RcaAction(org_id=org_id, case_id=case_id, created_by=created_by, **fields)
    db.add(action)
    await db.flush()
    await log_event(db, org_id, case_id, "action_added", f"Action added: {action.description}", actor_label)
    # Same auto-progression as create_node: a CAPA action means the case has
    # moved from "investigating" to actually acting on it.
    if case.status in ("open", "investigating"):
        case.status = "actions_open"
        await log_event(db, org_id, case_id, "status_changed", "Status changed to actions_open", actor_label)
    await db.commit()
    await db.refresh(action)
    return action


async def update_action(
    db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, action_id: uuid.UUID,
    completed_by: uuid.UUID, actor_label: str | None, patch: dict,
) -> RcaAction:
    action = await get_action_or_404(db, org_id, case_id, action_id)
    new_status = patch.get("status")
    if new_status == "done":
        patch["completed_at"] = datetime.now(timezone.utc)
        patch["completed_by"] = completed_by
    elif new_status in ("open", "in_progress"):
        patch["completed_at"] = None
        patch["completed_by"] = None
        patch["verification"] = None
    for key, value in patch.items():
        setattr(action, key, value)
    if new_status == "done":
        await log_event(db, org_id, case_id, "action_done", f"Action marked done: {action.description}", actor_label)
    elif new_status in ("open", "in_progress"):
        await log_event(db, org_id, case_id, "action_reopened", f"Action reopened: {action.description}", actor_label)
    await db.commit()
    await db.refresh(action)
    return action


async def delete_action(db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID, action_id: uuid.UUID, actor_label: str | None) -> None:
    action = await get_action_or_404(db, org_id, case_id, action_id)
    desc = action.description
    await db.delete(action)
    await log_event(db, org_id, case_id, "action_deleted", f"Action deleted: {desc}", actor_label)
    await db.commit()


async def list_events(db: AsyncSession, org_id: uuid.UUID, case_id: uuid.UUID) -> list[RcaEvent]:
    result = await db.execute(
        select(RcaEvent).where(RcaEvent.org_id == org_id, RcaEvent.case_id == case_id).order_by(RcaEvent.created_at)
    )
    return list(result.scalars().all())


# ─── Evidence engine ────────────────────────────────────────────────────────

@dataclass
class RcaCandidateCause:
    dimension: str
    label: str
    oee: float
    delta_oee: float
    row_count: int
    total_units: float
    support_pct: float
    confidence: float


@dataclass
class RcaEvidenceResult:
    overall_oee: float
    overall_availability: float
    overall_performance: float
    overall_quality: float
    candidates: list[RcaCandidateCause] = field(default_factory=list)
    candidates_total: int = 0


def _confidence(delta_oee: float, support_pct: float) -> float:
    """Composite ranking score, 0-100: favors candidates that are BOTH bad
    (large negative delta_oee) AND represent a meaningful share of volume
    (support_pct) - a huge delta from 2 rows out of 20,000 is noise."""
    severity_score = min(1.0, abs(delta_oee) / _SEVERITY_ANCHOR_PTS)
    support_score = min(1.0, max(0.0, support_pct) / _SUPPORT_ANCHOR_PCT)
    return round(severity_score * support_score * 100.0, 1)


def compute_evidence(
    dataset: Dataset, *, planned_col: str, operating_col: str, total_col: str, good_col: str,
    dimension_cols: list[str], target_rate: float | None, min_support_rows: int,
) -> RcaEvidenceResult:
    ds_service.validate_columns(dataset, [planned_col, operating_col, total_col, good_col, *dimension_cols])
    rel = ds_service.dataset_relation(dataset)
    has_rate = bool(target_rate and target_rate > 0)

    def _ideal_and_oee(g: dict) -> tuple[float, float, float, float, float]:
        # A flat target_rate has no per-row grouping here (no machine
        # dimension), so ideal units = operating minutes * the one rate - the
        # same shape as oee_calc's ideal_units_expr collapsed to a constant.
        ideal = g["operating"] * float(target_rate) if has_rate else g["total"]
        return compute_components_ideal(g["planned"], g["operating"], g["total"], g["good"], ideal, not has_rate)

    con = ds_service.open_con()
    try:
        overall = oee_overall(con, rel, planned_col, operating_col, total_col, good_col)
        overall_avail, overall_perf, overall_qual, overall_oee, _ = _ideal_and_oee(overall)

        total_planned = overall["planned"]
        candidates: list[RcaCandidateCause] = []
        for dim_col in dimension_cols:
            grouped = oee_grouped(con, rel, planned_col, operating_col, total_col, good_col, dim_col, with_count=True)
            for label, grp in grouped:
                row_count = int(grp.get("count", 0))
                if row_count < min_support_rows:
                    continue
                _, _, _, oee_val, _ = _ideal_and_oee(grp)
                delta = round(oee_val - overall_oee, 2)
                if delta >= 0:
                    continue  # only surface candidates WORSE than overall - evidence of a problem
                support_pct = round((grp["planned"] / total_planned) * 100.0, 2) if total_planned > 0 else 0.0
                candidates.append(RcaCandidateCause(
                    dimension=dim_col, label=label, oee=oee_val, delta_oee=delta, row_count=row_count,
                    total_units=grp["total"], support_pct=support_pct, confidence=_confidence(delta, support_pct),
                ))
    finally:
        con.close()

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    candidates_total = len(candidates)
    return RcaEvidenceResult(
        overall_oee=overall_oee, overall_availability=overall_avail,
        overall_performance=overall_perf, overall_quality=overall_qual,
        candidates=candidates[:_MAX_CANDIDATES], candidates_total=candidates_total,
    )


# ─── Pareto ─────────────────────────────────────────────────────────────────

@dataclass
class RcaParetoBucket:
    label: str
    value: float
    pct_of_total: float
    cumulative_pct: float


@dataclass
class RcaParetoResult:
    total: float
    buckets: list[RcaParetoBucket] = field(default_factory=list)
    buckets_total: int = 0


def compute_pareto(dataset: Dataset, *, category_col: str, value_col: str, top_n: int) -> RcaParetoResult:
    """Classic 80/20: sum value_col per category_col, sorted descending, with a
    running cumulative %. Categories beyond top_n fold into one "Other"
    bucket so cumulative_pct still reaches 100%."""
    ds_service.validate_columns(dataset, [category_col, value_col])
    rel = ds_service.dataset_relation(dataset)
    cat_q, val_e, inv_e = qi(category_col), val_expr(value_col), inv_expr(value_col)
    con = ds_service.open_con()
    try:
        sql = (
            f"SELECT CAST({cat_q} AS VARCHAR), SUM({val_e}), SUM({inv_e}) FROM {rel} "  # nosec B608 - cat_q/val_e/inv_e are qi()/validate_columns-checked
            f"WHERE NULLIF(TRIM(CAST({cat_q} AS VARCHAR)), '') IS NOT NULL GROUP BY 1"
        )
        rows = con.execute(sql).fetchall()
    finally:
        con.close()

    buckets_all = sorted(
        ((str(r[0]), float(r[1] or 0.0)) for r in rows), key=lambda b: b[1], reverse=True
    )
    total = sum(v for _, v in buckets_all)
    top, rest = buckets_all[:top_n], buckets_all[top_n:]

    result: list[RcaParetoBucket] = []
    cum = 0.0
    for label, value in top:
        cum += value
        result.append(RcaParetoBucket(
            label=label, value=round(value, 2),
            pct_of_total=round(value / total * 100.0, 2) if total > 0 else 0.0,
            cumulative_pct=round(cum / total * 100.0, 2) if total > 0 else 0.0,
        ))
    if rest:
        rest_sum = sum(v for _, v in rest)
        cum += rest_sum
        result.append(RcaParetoBucket(
            label=f"Other ({len(rest)})", value=round(rest_sum, 2),
            pct_of_total=round(rest_sum / total * 100.0, 2) if total > 0 else 0.0,
            cumulative_pct=round(cum / total * 100.0, 2) if total > 0 else 0.0,
        ))
    return RcaParetoResult(total=round(total, 2), buckets=result, buckets_total=len(buckets_all))


# ─── Timeline ───────────────────────────────────────────────────────────────

@dataclass
class RcaTimelineEvent:
    date: str
    oee: float
    delta_oee: float
    row_count: int
    is_center: bool = False


@dataclass
class RcaTimelineResult:
    overall_oee: float
    center_date: str
    window_days: int
    events: list[RcaTimelineEvent] = field(default_factory=list)


def compute_timeline(
    dataset: Dataset, *, date_col: str, planned_col: str, operating_col: str, total_col: str, good_col: str,
    window_days: int, center_date: str | None,
) -> RcaTimelineResult:
    """Group by day, then show a window of days around whichever day is worst
    (or an investigator-chosen center_date)."""
    ds_service.validate_columns(dataset, [date_col, planned_col, operating_col, total_col, good_col])
    rel = ds_service.dataset_relation(dataset)
    con = ds_service.open_con()
    try:
        overall = oee_overall(con, rel, planned_col, operating_col, total_col, good_col)
        _, _, _, overall_oee, _ = compute_components_ideal(
            overall["planned"], overall["operating"], overall["total"], overall["good"], overall["total"], True
        )
        day_rows = oee_grouped(con, rel, planned_col, operating_col, total_col, good_col, date_col, with_count=True)
    finally:
        con.close()

    daily = []
    for day_key, g in day_rows:
        _, _, _, oee_val, _ = compute_components_ideal(g["planned"], g["operating"], g["total"], g["good"], g["total"], True)
        daily.append({"date": day_key[:10], "oee": oee_val, "row_count": int(g.get("count", 0))})
    daily.sort(key=lambda d: d["date"])

    if not daily:
        return RcaTimelineResult(overall_oee=overall_oee, center_date=center_date or "", window_days=window_days)

    center = center_date or min(daily, key=lambda d: d["oee"])["date"]
    try:
        center_d = date.fromisoformat(center)
    except ValueError:
        center_d = date.fromisoformat(daily[0]["date"])
        center = daily[0]["date"]
    start_d, end_d = center_d - timedelta(days=window_days), center_d + timedelta(days=window_days)

    events = [
        RcaTimelineEvent(
            date=d["date"], oee=d["oee"], delta_oee=round(d["oee"] - overall_oee, 2),
            row_count=d["row_count"], is_center=(d["date"] == center),
        )
        for d in daily
        if start_d <= date.fromisoformat(d["date"]) <= end_d
    ]
    return RcaTimelineResult(overall_oee=overall_oee, center_date=center, window_days=window_days, events=events)


# ─── Action effectiveness verification ─────────────────────────────────────

@dataclass
class RcaVerificationResult:
    before_oee: float
    after_oee: float
    delta_oee: float
    before_row_count: int
    after_row_count: int
    date_col: str
    split_at: str


def compute_verification(
    dataset: Dataset, *, planned_col: str, operating_col: str, total_col: str, good_col: str,
    date_col: str, split_at: datetime,
) -> RcaVerificationResult:
    ds_service.validate_columns(dataset, [planned_col, operating_col, total_col, good_col, date_col])
    rel = ds_service.dataset_relation(dataset)
    date_expr = f"TRY_CAST({qi(date_col)} AS TIMESTAMP)"
    split_iso = split_at.isoformat()
    con = ds_service.open_con()
    try:
        before = oee_overall(con, rel, planned_col, operating_col, total_col, good_col, where_sql=f"{date_expr} < TIMESTAMP '{split_iso}'")
        after = oee_overall(con, rel, planned_col, operating_col, total_col, good_col, where_sql=f"{date_expr} >= TIMESTAMP '{split_iso}'")
    finally:
        con.close()

    _, _, _, before_oee, _ = compute_components_ideal(before["planned"], before["operating"], before["total"], before["good"], before["total"], True)
    _, _, _, after_oee, _ = compute_components_ideal(after["planned"], after["operating"], after["total"], after["good"], after["total"], True)

    return RcaVerificationResult(
        before_oee=before_oee, after_oee=after_oee, delta_oee=round(after_oee - before_oee, 2),
        before_row_count=before["row_count"], after_row_count=after["row_count"],
        date_col=date_col, split_at=split_iso,
    )


# ─── Recurring-cause trends (org-wide) ──────────────────────────────────────

@dataclass
class RcaTrendEntry:
    kind: str
    key: str
    count: int
    case_ids: list[str]
    case_titles: list[str]


@dataclass
class RcaTrendsResult:
    total_root_causes: int
    by_category: list[RcaTrendEntry] = field(default_factory=list)


async def compute_trends(db: AsyncSession, org_id: uuid.UUID) -> RcaTrendsResult:
    cases = await list_cases(db, org_id)
    cases_by_id = {c.id: c for c in cases}
    by_category: dict[str, dict] = {}
    total_root_causes = 0

    for case in cases:
        nodes = await list_nodes(db, org_id, case.id)
        for node in nodes:
            if node.node_type != "root_cause":
                continue
            total_root_causes += 1
            if node.category:
                bucket = by_category.setdefault(node.category, {"case_ids": set(), "count": 0})
                bucket["count"] += 1
                bucket["case_ids"].add(case.id)

    entries = [
        RcaTrendEntry(
            kind="category", key=key, count=b["count"],
            case_ids=[str(cid) for cid in sorted(b["case_ids"], key=str)],
            case_titles=[cases_by_id[cid].title for cid in b["case_ids"] if cid in cases_by_id],
        )
        for key, b in by_category.items()
        if b["count"] >= 2  # "recurring" means more than once
    ]
    entries.sort(key=lambda e: e.count, reverse=True)
    return RcaTrendsResult(total_root_causes=total_root_causes, by_category=entries)
