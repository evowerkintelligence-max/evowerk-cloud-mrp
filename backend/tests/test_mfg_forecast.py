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


def _forecast_csv_with_gap_and_outlier(n_days: int = 60, gap_day: int = 30, outlier_day: int = 40) -> bytes:
    start = date(2026, 1, 1)
    rows = ["date,machine,output"]
    for i in range(n_days):
        if i == gap_day:
            continue  # missing calendar day - lag-7/lag-14 must still line up on real dates
        d = start + timedelta(days=i)
        value = 100 + i * 0.8 + (5 if d.weekday() in (5, 6) else 0)
        if i == outlier_day:
            value += 5000  # one bad data-entry day
        rows.append(f"{d.isoformat()},M1,{value:.2f}")
    return ("\n".join(rows) + "\n").encode()


async def test_forecast_fills_gaps_and_clips_outliers(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, _forecast_csv_with_gap_and_outlier())

    res = await client.post(
        f"/datasets/{dataset_id}/mfg-forecast/run",
        headers=_h(token),
        json={"date_col": "date", "target_col": "output", "horizon": 7},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["gap_count"] == 1
    assert body["outliers_clipped"] >= 1
    # The forecast itself shouldn't be dragged toward the clipped spike.
    future_preds = [p["predicted"] for p in body["series"] if p["actual"] is None]
    assert all(p < 1000 for p in future_preds)


async def test_forecast_builds_track_record_across_dataset_reuploads(client):
    # Datasets are immutable after upload, so "new data came in" is a fresh
    # upload with a new dataset_id, not a growing row count on the same one -
    # track record must survive that re-upload, not reset on it.
    token = await _register_and_login(client)

    first_id = await _upload(client, token, _forecast_csv(n_days=60))
    first = await client.post(
        f"/datasets/{first_id}/mfg-forecast/run",
        headers=_h(token),
        json={"date_col": "date", "target_col": "output", "horizon": 7},
    )
    assert first.status_code == 200, first.text
    assert first.json()["track_record"] == []  # nothing to compare against yet
    assert first.json()["track_record_mape"] is None

    # A later export that now covers the 7 days the first run only predicted.
    second_id = await _upload(client, token, _forecast_csv(n_days=67))
    second = await client.post(
        f"/datasets/{second_id}/mfg-forecast/run",
        headers=_h(token),
        json={"date_col": "date", "target_col": "output", "horizon": 7},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert len(body["track_record"]) == 7
    assert body["track_record_mape"] is not None
    for point in body["track_record"]:
        assert point["actual"] is not None
        assert point["predicted"] is not None
        assert point["error_pct"] >= 0
