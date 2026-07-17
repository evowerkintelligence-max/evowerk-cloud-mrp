# backend/tests/test_shift_forecast.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register(client, email: str = "sf@example.com", org_name: str = "SfOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    body = reg.json()
    return uuid.UUID(str(body["org_id"])), uuid.UUID(str(body["user_id"]))


async def test_shift_forecast_dataset_model_roundtrip(client, test_engine):
    from app.models.dataset import Dataset
    from app.models.shift_forecast import ShiftForecastDataset

    org_id, user_id = await _register(client)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = Dataset(
            org_id=org_id,
            name="Shift Forecast Source: A",
            original_filename="shift_forecast.csv",
            fmt="csv",
            storage_ext="csv",
            columns=[{"name": "period", "dtype": "VARCHAR"}, {"name": "value", "dtype": "DOUBLE"}],
            row_count=1,
            size_bytes=10,
            created_by=user_id,
        )
        db.add(dataset)
        await db.flush()

        mapping = ShiftForecastDataset(org_id=org_id, shift="A", dataset_id=dataset.id)
        db.add(mapping)
        await db.commit()
        await db.refresh(mapping)

        assert mapping.org_id == org_id
        assert mapping.shift == "A"
        assert mapping.dataset_id == dataset.id
