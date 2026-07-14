"""Query history for the ad-hoc dataset query layer (structured queries and
free-text SQL) - ported from the desktop app's Query Studio history panel.

Rather than a dedicated table, this reads the existing AuditLog: every
/datasets/{id}/query and /datasets/{id}/sql call already writes an entry
there (see app.api.routes.datasets), so history is "the audit log, filtered
and shaped for replay" instead of a second write path to keep in sync.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog

QUERY_ACTIONS = ("dataset.query", "dataset.sql_query")


@dataclass
class QueryHistoryEntry:
    id: uuid.UUID
    action: str
    dataset_id: uuid.UUID | None
    query: dict | None
    sql: str | None
    created_at: datetime


def _to_entry(row: AuditLog) -> QueryHistoryEntry:
    meta = row.event_metadata or {}
    return QueryHistoryEntry(
        id=row.id,
        action=row.action,
        dataset_id=uuid.UUID(row.target) if row.target else None,
        query=meta.get("query"),
        sql=meta.get("sql"),
        created_at=row.created_at,
    )


async def list_query_history(
    db: AsyncSession, org_id: uuid.UUID, *, dataset_id: uuid.UUID | None = None, limit: int = 50
) -> list[QueryHistoryEntry]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.action.in_(QUERY_ACTIONS))
        .order_by(AuditLog.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    if dataset_id is not None:
        stmt = stmt.where(AuditLog.target == str(dataset_id))
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_entry(r) for r in rows]


async def delete_query_history_entry(db: AsyncSession, org_id: uuid.UUID, entry_id: uuid.UUID) -> None:
    result = await db.execute(
        select(AuditLog).where(
            AuditLog.id == entry_id, AuditLog.org_id == org_id, AuditLog.action.in_(QUERY_ACTIONS)
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError("History entry not found")
    await db.delete(row)
    await db.commit()


async def clear_query_history(db: AsyncSession, org_id: uuid.UUID, *, action: str | None = None) -> None:
    stmt = delete(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action.in_(QUERY_ACTIONS))
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    await db.execute(stmt)
    await db.commit()
