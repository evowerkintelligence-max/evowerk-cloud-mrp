from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register(client, email: str = "ar@example.com", org_name: str = "ArOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    body = reg.json()
    return uuid.UUID(str(body["org_id"])), uuid.UUID(str(body["user_id"]))


async def test_automl_run_retrain_columns_roundtrip(client, test_engine):
    from app.models.automl_run import AutoMLRun
    from app.models.dataset import Dataset

    org_id, user_id = await _register(client)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = Dataset(
            org_id=org_id, name="D", original_filename="d.csv", fmt="csv", storage_ext="csv",
            columns=[{"name": "x", "dtype": "VARCHAR"}], row_count=1, size_bytes=1, created_by=user_id,
        )
        db.add(dataset)
        await db.flush()

        parent = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Parent",
            task="regression", target_column="x", status="success", auto_retrain=True,
        )
        db.add(parent)
        await db.flush()

        child = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Child",
            task="regression", target_column="x", status="success",
            auto_retrain=True, retrained_from_run_id=parent.id, auto_retrain_streak=0,
        )
        db.add(child)
        await db.commit()
        await db.refresh(child)

        assert child.retrained_from_run_id == parent.id
        assert child.auto_retrain is True
        assert child.auto_retrain_streak == 0

        default_run = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Default",
            task="regression", target_column="x", status="queued",
        )
        db.add(default_run)
        await db.commit()
        await db.refresh(default_run)
        assert default_run.auto_retrain is False
        assert default_run.retrained_from_run_id is None
        assert default_run.auto_retrain_streak == 0
