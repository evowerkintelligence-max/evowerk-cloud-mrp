from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


def _forecast_csv(n_days: int = 60) -> bytes:
    # A mild upward trend with a weekly wobble - enough signal for the model
    # to train without needing this test to assert a specific MAPE/R2 number.
    start = date(2026, 1, 1)
    rows = ["date,machine,output"]
    for i in range(n_days):
        d = start + timedelta(days=i)
        value = 100 + i * 0.8 + (5 if d.weekday() in (5, 6) else 0)
        rows.append(f"{d.isoformat()},M1,{value:.2f}")
    return ("\n".join(rows) + "\n").encode()


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data):
    res = await client.post("/datasets", files={"file": ("forecast.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_forecast_run_returns_history_and_horizon(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, _forecast_csv())

    res = await client.post(
        f"/datasets/{dataset_id}/mfg-forecast/run",
        headers=_h(token),
        json={"date_col": "date", "target_col": "output", "machine_col": "machine", "horizon": 7},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["horizon"] == 7
    assert body["machines"] == ["M1"]
    future_points = [p for p in body["series"] if p["actual"] is None]
    assert len(future_points) == 7
    # Confidence band must bracket the point forecast.
    for p in future_points:
        assert p["lower"] <= p["predicted"] <= p["upper"]
    # Intervals widen (or at least don't shrink) further out the horizon.
    widths = [p["upper"] - p["lower"] for p in future_points]
    assert widths[-1] >= widths[0]
    assert len(body["prescriptive"]) >= 1
    assert body["summary"]


async def test_forecast_rejects_too_little_data(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, _forecast_csv(n_days=10))

    res = await client.post(
        f"/datasets/{dataset_id}/mfg-forecast/run",
        headers=_h(token),
        json={"date_col": "date", "target_col": "output"},
    )
    assert res.status_code == 400


async def test_forecast_rejects_unknown_column(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, _forecast_csv())

    res = await client.post(
        f"/datasets/{dataset_id}/mfg-forecast/run",
        headers=_h(token),
        json={"date_col": "date", "target_col": "does_not_exist"},
    )
    assert res.status_code == 400
