from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"

# total units = 240, planned = 270 -> efficiency 88.9%.
# periods: 2026-01-01 = 160 (peak), 2026-01-02 = 80. machines: M1=180 (75%), M2=60.
TP_CSV = (
    b"units,planned,machine,day\n"
    b"100,120,M1,2026-01-01\n"
    b"80,100,M1,2026-01-02\n"
    b"60,50,M2,2026-01-01\n"
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=TP_CSV):
    res = await client.post("/datasets", files={"file": ("tp.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_throughput_metrics(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        "/analytics/throughput",
        json={
            "dataset_id": dataset_id,
            "units_col": "units",
            "planned_units_col": "planned",
            "machine_col": "machine",
            "date_col": "day",
        },
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["total_units"] == 240.0
    assert b["total_planned"] == 270.0
    assert b["efficiency_pct"] == 88.9
    assert b["period_count"] == 2
    assert b["avg_units_per_period"] == 120.0
    assert b["peak_units"] == 160.0
    assert b["peak_period"] == "2026-01-01"
    machines = {m["machine"]: m for m in b["by_machine"]}
    assert machines["M1"]["pct_of_total"] == 75.0


async def test_throughput_negative_units_floored_and_flagged(client):
    token = await _register_and_login(client)
    csv = (
        b"units,planned,machine,day\n"
        b"100,120,M1,2026-01-01\n"
        b"-50,80,M1,2026-01-02\n"
    )
    dataset_id = await _upload(client, token, data=csv)

    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units", "planned_units_col": "planned"},
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    # negative units cell is floored to 0 in the sum, not passed through as-is.
    assert b["total_units"] == 100.0
    assert b["total_planned"] == 200.0
    assert b["invalid_cells"] == 1
    assert b["rows_with_issues"] == 1


async def test_throughput_unknown_optional_column_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units", "machine_col": "nope"},
        headers=_h(token),
    )
    assert res.status_code == 400


async def test_throughput_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    dataset_id = await _upload(client, token_a)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units"},
        headers=_h(token_b),
    )
    assert res.status_code == 404


async def test_throughput_dataset_not_found(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": str(uuid.uuid4()), "units_col": "units"},
        headers=_h(token),
    )
    assert res.status_code == 404
