from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Integration(Base):
    """A tenant-scoped external data source connection, ported from the
    desktop app's integrations_store.py. See app/services/connectors for the
    9 connector implementations and app/services/integrations.py for the
    test/preview/sync flow that materializes a sync into a Dataset.

    Unlike the desktop app (single-tenant, local file, "no network exposure"),
    `config`'s secret fields (password, auth_value) are Fernet-encrypted at
    rest via app.core.security.encrypt_secret — see
    app.services.integrations.merge_secrets.
    """

    __tablename__ = "integrations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # csv | excel | sqlserver | oracle | sap_odata | erp_rest | mes_rest | opcua | mqtt | opcda | pi_webapi
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # untested | connected | error
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
