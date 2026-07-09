from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Dataset(Base):
    """A tenant-scoped uploaded tabular dataset (production/machine data) that the
    analytics engines run over. The file itself lives on the storage backend
    (see app/services/storage.py); this row is the metadata + sniffed schema.

    Datasets are read-only after upload — analytics compute is stateless over
    them, so there is no mutable state here beyond deletion.
    """

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Stored format / file extension the engines read (DuckDB-native): csv | tsv |
    # json | parquet. xlsx/xls are converted to csv on upload.
    fmt: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_ext: Mapped[str] = mapped_column(String(16), nullable=False)
    # Sniffed schema: [{"name": ..., "dtype": ...}], in column order.
    columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
