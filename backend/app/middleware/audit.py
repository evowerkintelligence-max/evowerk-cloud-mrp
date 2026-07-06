from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def log_event(
    db: AsyncSession,
    *,
    action: str,
    org_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    target: str | None = None,
    ip_address: str | None = None,
    metadata: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            action=action,
            org_id=org_id,
            actor_user_id=actor_user_id,
            target=target,
            ip_address=ip_address,
            event_metadata=metadata or {},
        )
    )
    await db.commit()
