# backend/tests/test_shift_forecast.py
from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


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


SHIFT_TREND_CSV = (
    b"date,shift,units\n"
    b"2026-07-01,A,100\n"
    b"2026-07-02,A,110\n"
    b"2026-07-03,A,90\n"
    b"2026-07-01,B,50\n"
    b"2026-07-02,B,60\n"
)


async def _register_and_login(client, email: str, org_name: str = "SfOrg") -> str:
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _upload_trend_dataset(client, headers) -> str:
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("trend.csv", SHIFT_TREND_CSV, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_forecast_source_builds_period_value_dataset(client):
    token = await _register_and_login(client, "sfsource@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_trend_dataset(client, headers)

    res = await client.post(
        "/analytics/shifts/forecast-source", headers=headers,
        json={
            "dataset_id": dataset_id, "shift_col": "shift", "units_col": "units",
            "timestamp_col": "date", "shift": "A",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["target_column"] == "value"
    assert body["time_column"] == "period"
    assert body["metric_label"] == "Units"
    assert body["rows"] == 3

    ds = await client.get(f"/datasets/{body['dataset_id']}", headers=headers)
    assert ds.status_code == 200
    cols = {c["name"] for c in ds.json()["columns"]}
    assert cols == {"period", "value"}
    assert ds.json()["row_count"] == 3


async def test_forecast_source_reuses_same_dataset_on_repeat_calls(client):
    token = await _register_and_login(client, "sfreuse@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_trend_dataset(client, headers)
    body = {
        "dataset_id": dataset_id, "shift_col": "shift", "units_col": "units",
        "timestamp_col": "date", "shift": "A",
    }

    first = await client.post("/analytics/shifts/forecast-source", headers=headers, json=body)
    second = await client.post("/analytics/shifts/forecast-source", headers=headers, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["dataset_id"] == second.json()["dataset_id"]


async def test_forecast_source_requires_timestamp_column(client):
    token = await _register_and_login(client, "sfnots@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_trend_dataset(client, headers)

    res = await client.post(
        "/analytics/shifts/forecast-source", headers=headers,
        json={"dataset_id": dataset_id, "shift_col": "shift", "units_col": "units", "shift": "A"},
    )
    assert res.status_code == 400
    assert "timestamp" in res.json()["detail"].lower()


async def test_forecast_source_400_for_shift_with_no_trend_rows(client):
    token = await _register_and_login(client, "sfnoshift@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_trend_dataset(client, headers)

    res = await client.post(
        "/analytics/shifts/forecast-source", headers=headers,
        json={
            "dataset_id": dataset_id, "shift_col": "shift", "units_col": "units",
            "timestamp_col": "date", "shift": "C",
        },
    )
    assert res.status_code == 400
    assert "no trend data" in res.json()["detail"].lower()
