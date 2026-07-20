# backend/app/models/shift_forecast.py
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ShiftForecastDataset(Base):
    """One row per (org, shift) - points at the Dataset that
    app.services.shifts.prepare_forecast_source keeps overwriting with that
    shift's latest day-bucketed trend series, mirroring DowntimeLiveDataset's
    one-dataset-per-key materialization pattern.
    """

    __tablename__ = "shift_forecast_datasets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    )
    shift: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
