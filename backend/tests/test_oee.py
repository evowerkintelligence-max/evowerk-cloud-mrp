from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

OEE_CSV = (
    b"date,machine,planned_min,operating_min,total_units,good_units\n"
    b"2026-01-01,M1,480,440,880,850\n"
    b"2026-01-01,M2,480,400,760,700\n"
    b"2026-01-02,M1,480,460,900,880\n"
    b"2026-01-02,M2,480,420,800,760\n"
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
        "/datasets", headers=headers, files={"file": ("oee.csv", OEE_CSV, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_oee_compute_availability_performance_quality(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/oee/compute",
        headers=headers,
        json={
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "date_col": "date",
            "machine_col": "machine",
        },
    )
    assert res.status_code == 200
    body = res.json()

    total_planned = 480 * 4
    total_operating = 440 + 400 + 460 + 420
    total_units = 880 + 760 + 900 + 800
    good_units = 850 + 700 + 880 + 760

    assert body["total_planned"] == total_planned
    assert body["total_operating"] == total_operating
    assert body["availability"] == round(total_operating / total_planned * 100, 2)
    # No target_rate/machine_rates supplied -> performance is fully estimated at 100%.
    assert body["performance"] == 100.0
    assert body["performance_estimated"] is True
    assert body["quality"] == round(good_units / total_units * 100, 2)
    assert len(body["machines"]) == 2
    assert len(body["trend"]) == 2


async def test_oee_compute_rejects_unknown_column(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/oee/compute",
        headers=headers,
        json={
            "planned_time_col": "nope",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
        },
    )
    assert res.status_code == 400


async def test_oee_rca_ranks_worst_machine_first(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/oee/rca",
        headers=headers,
        json={
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "machine_col": "machine",
        },
    )
    assert res.status_code == 200
    body = res.json()
    machine_dim = next(d for d in body["dimensions"] if d["name"] == "Machine")
    oees = [r["oee"] for r in machine_dim["rows"]]
    assert oees == sorted(oees)  # worst (lowest) OEE first
