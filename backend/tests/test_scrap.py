from __future__ import annotations


import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"

# total=400, scrap=30 -> rate 7.5%, FPY 92.5%, 75 defects/1000.
# reasons: Crack=15, Warp=15 (each 50%). machines: M2 rate 10%, M1 rate 6.67%.
SCRAP_CSV = (
    b"total,scrap,reason,machine,day\n"
    b"100,5,Crack,M1,2026-01-01\n"
    b"200,15,Warp,M1,2026-01-02\n"
    b"100,10,Crack,M2,2026-01-01\n"
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


async def _upload(client, token, data=SCRAP_CSV):
    res = await client.post("/datasets", files={"file": ("s.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_scrap_metrics(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        "/analytics/scrap",
        json={
            "dataset_id": dataset_id,
            "total_units_col": "total",
            "scrap_col": "scrap",
            "reason_col": "reason",
            "machine_col": "machine",
            "date_col": "day",
        },
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["total_units"] == 400.0
    assert b["total_scrap"] == 30.0
    assert b["scrap_rate_pct"] == 7.5
    assert b["fpq_pct"] == 92.5
    assert b["defect_rate_per_1000"] == 75.0

    reasons = {r["reason"]: r for r in b["by_reason"]}
    assert set(reasons) == {"Crack", "Warp"}
    assert reasons["Crack"]["pct_of_total"] == 50.0

    machines = {m["machine"]: m for m in b["by_machine"]}
    assert machines["M2"]["scrap_rate_pct"] == 10.0
    assert len(b["trend"]) == 2


async def test_scrap_negative_units_floored_and_flagged(client):
    token = await _register_and_login(client)
    csv = (
        b"total,scrap,reason,machine,day\n"
        b"100,5,Crack,M1,2026-01-01\n"
        b"50,-10,Warp,M2,2026-01-02\n"
    )
    dataset_id = await _upload(client, token, data=csv)

    res = await client.post(
        "/analytics/scrap",
        json={
            "dataset_id": dataset_id,
            "total_units_col": "total",
            "scrap_col": "scrap",
            "reason_col": "reason",
            "machine_col": "machine",
        },
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    # negative scrap cell is floored to 0, not subtracted from the total as-is.
    assert b["total_units"] == 150.0
    assert b["total_scrap"] == 5.0
    assert b["invalid_cells"] == 1
    assert b["rows_with_issues"] == 1

    machines = {m["machine"]: m for m in b["by_machine"]}
    assert machines["M1"]["invalid_cells"] == 0
    assert machines["M2"]["scrap_units"] == 0.0
    assert machines["M2"]["invalid_cells"] == 1

    reasons = {r["reason"]: r for r in b["by_reason"]}
    assert reasons["Warp"]["count"] == 0.0
    assert reasons["Warp"]["invalid_cells"] == 1
    assert reasons["Crack"]["invalid_cells"] == 0


async def test_scrap_required_column_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    res = await client.post(
        "/analytics/scrap",
        json={"dataset_id": dataset_id, "total_units_col": "total", "scrap_col": "missing"},
        headers=_h(token),
    )
    assert res.status_code == 400


async def test_scrap_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    dataset_id = await _upload(client, token_a)
    res = await client.post(
        "/analytics/scrap",
        json={"dataset_id": dataset_id, "total_units_col": "total", "scrap_col": "scrap"},
        headers=_h(token_b),
    )
    assert res.status_code == 404
