from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

# 3 cases follow Cut -> Assemble -> Inspect; case 3 skips Assemble (a deviation
# from the majority path) so conformance checking has something to find.
PROCESS_CSV = (
    b"case_id,activity,ts\n"
    b"1,Cut,2026-01-01T08:00:00\n"
    b"1,Assemble,2026-01-01T08:10:00\n"
    b"1,Inspect,2026-01-01T08:30:00\n"
    b"2,Cut,2026-01-01T09:00:00\n"
    b"2,Assemble,2026-01-01T09:12:00\n"
    b"2,Inspect,2026-01-01T09:35:00\n"
    b"3,Cut,2026-01-01T10:00:00\n"
    b"3,Inspect,2026-01-01T10:20:00\n"
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
        "/datasets", headers=headers, files={"file": ("process.csv", PROCESS_CSV, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_process_mining_discovers_flow_and_conformance(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/process-mining/analyze",
        headers=headers,
        json={"case_column": "case_id", "activity_column": "activity", "timestamp_column": "ts"},
    )
    assert res.status_code == 200
    body = res.json()

    assert body["summary"]["total_cases"] == 3
    assert {a["name"] for a in body["activities"]} == {"Cut", "Assemble", "Inspect"}
    assert body["conformance"]["reference_sequence"] == ["Cut", "Assemble", "Inspect"]
    assert body["conformance"]["conforming_cases"] == 2
    assert body["conformance"]["deviating_cases"] == 1
    dev = body["deviations"][0]
    assert dev["case_id"] == "3"
    assert dev["missing_activities"] == ["Assemble"]


async def test_process_mining_rejects_unknown_column(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/process-mining/analyze",
        headers=headers,
        json={"case_column": "nope", "activity_column": "activity", "timestamp_column": "ts"},
    )
    assert res.status_code == 400
