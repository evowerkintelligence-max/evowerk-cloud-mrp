from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

PLANT_CSV = (
    b"date,machine,planned_min,operating_min,total_units,good_units,scrap_units,defects\n"
    b"2026-01-01,M1,480,440,880,850,30,5\n"
    b"2026-01-01,M2,480,400,760,700,60,10\n"
    b"2026-01-02,M1,480,460,900,880,20,3\n"
    b"2026-01-02,M2,480,420,800,760,40,8\n"
)


async def _register_and_login(client):
    reg = await client.post(
        "/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    return login.json()["access_token"]


async def _upload_dataset(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("plant.csv", PLANT_CSV, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_plant_summary_combines_oee_and_scrap_tiles(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        "/analytics/plant-summary",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "date_col": "date",
            "machine_col": "machine",
            "scrap_units_col": "scrap_units",
            "defects_col": "defects",
        },
    )
    assert res.status_code == 200
    body = res.json()

    total_units = 880 + 760 + 900 + 800
    total_scrap = 30 + 60 + 20 + 40
    total_defects = 5 + 10 + 3 + 8

    assert body["performance_estimated"] is True
    assert len(body["machines"]) == 2
    assert len(body["trend"]) == 2
    assert body["scrap_rate_pct"] == round(total_scrap / total_units * 100, 2)
    assert body["fpq_pct"] == round(100.0 - body["scrap_rate_pct"], 2)
    assert body["defect_rate_per_1000"] == round(total_defects / total_units * 1000, 2)
    # Trend carries a per-date scrap rate alongside OEE when a scrap column is mapped.
    assert all(pt["scrap_rate_pct"] is not None for pt in body["trend"])
    # Machine table sorted by OEE descending, same convention as the OEE page.
    oees = [m["oee"] for m in body["machines"]]
    assert oees == sorted(oees, reverse=True)


TIMESTAMPED_CSV = (
    b"date,machine,planned_min,operating_min,total_units,good_units,scrap_units\n"
    b"2026-02-01 06:00:00,M1,240,220,440,420,10\n"
    b"2026-02-01 14:00:00,M1,240,230,460,440,15\n"
    b"2026-02-02 06:00:00,M1,240,225,450,430,12\n"
    b"2026-02-02 14:00:00,M1,240,235,470,455,8\n"
)


async def test_plant_summary_buckets_trend_by_day_for_timestamped_dates(client):
    # A date column with intraday timestamps must still bucket the trend by
    # calendar day (not one point per distinct timestamp) and the per-point
    # scrap rate must line up with the OEE point instead of silently dropping.
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    login_headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=login_headers, files={"file": ("plant.csv", TIMESTAMPED_CSV, "text/csv")}
    )
    assert res.status_code == 201
    dataset_id = res.json()["id"]

    res = await client.post(
        "/analytics/plant-summary",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "date_col": "date",
            "scrap_units_col": "scrap_units",
        },
    )
    assert res.status_code == 200
    body = res.json()

    assert [pt["date"] for pt in body["trend"]] == ["2026-02-01", "2026-02-02"]
    assert all(pt["scrap_rate_pct"] is not None for pt in body["trend"])
    assert body["trend"][0]["scrap_rate_pct"] == round((10 + 15) / (440 + 460) * 100, 2)
    assert body["trend"][1]["scrap_rate_pct"] == round((12 + 8) / (450 + 470) * 100, 2)


async def test_plant_summary_without_scrap_columns_omits_those_tiles(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        "/analytics/plant-summary",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["scrap_rate_pct"] is None
    assert body["fpq_pct"] is None
    assert body["defect_rate_per_1000"] is None
    assert body["trend"] == []
    assert body["machines"] == []


async def test_plant_summary_rejects_unknown_column(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        "/analytics/plant-summary",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "nope",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
        },
    )
    assert res.status_code == 400
