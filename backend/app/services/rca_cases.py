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
from datetime import date, datetime, timedelta, timezone  # noqa: F401

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import compute_components_ideal, inv_expr, oee_grouped, oee_overall, qi, val_expr  # noqa: F401
from app.models.dataset import Dataset  # noqa: F401
from app.models.rca_case import RcaAction, RcaCase, RcaEvent, RcaNode
from app.services import datasets as ds_service
from app.services.rca_cases_analytics import (  # noqa: F401
    _MAX_CANDIDATES, _SEVERITY_ANCHOR_PTS, _SUPPORT_ANCHOR_PCT, RcaCandidateCause, RcaEvidenceResult, RcaParetoBucket,
    RcaParetoResult, RcaTimelineEvent, RcaTimelineResult, RcaVerificationResult, _confidence, compute_evidence,
    compute_pareto, compute_timeline, compute_verification,
)

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
