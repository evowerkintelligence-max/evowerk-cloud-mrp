from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

# M1: healthy, high OEE every day. M2: availability crisis, OEE well below the
# 0.40 critical threshold - risk_level="high" is then guaranteed regardless of
# the statistical engine's exact weighting (current_oee < _CRITICAL_OEE alone
# forces "high"), which keeps this test robust to the underlying formula.
_ROWS = []
for day in range(1, 11):
    _ROWS.append(f"2026-01-{day:02d},M1,480,470,920,900")
    _ROWS.append(f"2026-01-{day:02d},M2,480,150,200,150")
PREDICTIVE_CSV = ("date,machine,planned,operating,total,good\n" + "\n".join(_ROWS) + "\n").encode()


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=PREDICTIVE_CSV):
    res = await client.post("/datasets", files={"file": ("predictive.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_analyze_flags_low_oee_machine_high_risk(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/predictive/analyze",
        headers=_h(token),
        json={
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    by_machine = {m["machine"]: m for m in body["machines"]}
    assert by_machine["M2"]["risk_level"] == "high"
    assert by_machine["M2"]["current_oee"] < 0.40
    assert by_machine["M1"]["risk_level"] == "low"

    rec_machines = {r["machine"] for r in body["recommendations"]}
    assert "M2" in rec_machines
    assert body["cost_summary"]["machines_at_risk"] >= 1


async def test_analyze_rejects_missing_columns(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/predictive/analyze",
        headers=_h(token),
        json={
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "does_not_exist",
        },
    )
    assert res.status_code == 400


async def test_schedule_crud(client):
    token = await _register_and_login(client)

    create = await client.post(
        "/predictive/schedules",
        headers=_h(token),
        json={"machine": "M2", "priority": "immediate", "action": "Replace hydraulic seal", "pm_hours": 16},
    )
    assert create.status_code == 201, create.text
    item = create.json()
    assert item["status"] == "pending"

    listed = await client.get("/predictive/schedules", headers=_h(token))
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    patched = await client.patch(
        f"/predictive/schedules/{item['id']}", headers=_h(token), json={"status": "done"}
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "done"

    deleted = await client.delete(f"/predictive/schedules/{item['id']}", headers=_h(token))
    assert deleted.status_code == 204

    listed_after = await client.get("/predictive/schedules", headers=_h(token))
    assert listed_after.json() == []


async def test_schedule_not_found(client):
    token = await _register_and_login(client)
    res = await client.patch(
        "/predictive/schedules/00000000-0000-0000-0000-000000000000", headers=_h(token), json={"status": "done"}
    )
    assert res.status_code == 404


async def test_predictive_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")

    await client.post(
        "/predictive/schedules", headers=_h(token_a), json={"machine": "M1", "action": "Inspect"}
    )
    res_b = await client.get("/predictive/schedules", headers=_h(token_b))
    assert res_b.json() == []
