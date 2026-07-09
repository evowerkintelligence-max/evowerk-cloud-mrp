from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"

# Two machines, two days. Hand-computed with target_rate=2 units/min:
#   planned=200, operating=170, total=340, good=323, ideal=(90+80)*2=340
#   availability = 170/200 = 85.0
#   performance  = 340/340 = 100.0
#   quality      = 323/340 = 95.0
#   oee          = 0.85 * 1.0 * 0.95 = 80.75
OEE_CSV = (
    b"planned,operating,total,good,machine,day\n"
    b"100,90,180,171,M1,2026-01-01\n"
    b"100,80,160,152,M2,2026-01-02\n"
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=OEE_CSV):
    res = await client.post(
        "/datasets", files={"file": ("prod.csv", data, "text/csv")}, headers=_h(token)
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _base_mapping(dataset_id, **extra):
    body = {
        "dataset_id": dataset_id,
        "planned_time_col": "planned",
        "operating_time_col": "operating",
        "total_units_col": "total",
        "good_units_col": "good",
    }
    body.update(extra)
    return body


async def test_oee_exact_numbers(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        "/analytics/oee",
        json=_base_mapping(dataset_id, target_rate=2, machine_col="machine", date_col="day"),
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["availability"] == 85.0
    assert body["performance"] == 100.0
    assert body["quality"] == 95.0
    assert body["oee"] == 80.75
    assert body["performance_estimated"] is False
    assert body["row_count"] == 2
    assert {m["machine"] for m in body["machines"]} == {"M1", "M2"}


async def test_oee_performance_estimated_without_rate(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post("/analytics/oee", json=_base_mapping(dataset_id), headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    # No target rate: performance is pinned to 100% and flagged as estimated.
    assert body["performance_estimated"] is True
    assert body["performance"] == 100.0
    assert body["availability"] == 85.0
    assert body["quality"] == 95.0


async def test_oee_unknown_column_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        "/analytics/oee",
        json=_base_mapping(dataset_id, planned_time_col="does_not_exist"),
        headers=_h(token),
    )
    assert res.status_code == 400


async def test_oee_dataset_not_found(client):
    token = await _register_and_login(client)
    import uuid

    res = await client.post(
        "/analytics/oee",
        json=_base_mapping(str(uuid.uuid4())),
        headers=_h(token),
    )
    assert res.status_code == 404


async def test_oee_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    dataset_id = await _upload(client, token_a)

    res = await client.post(
        "/analytics/oee", json=_base_mapping(dataset_id), headers=_h(token_b)
    )
    assert res.status_code == 404
