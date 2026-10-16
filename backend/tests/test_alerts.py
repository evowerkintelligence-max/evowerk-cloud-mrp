from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD

DOWNTIME_CSV = (
    b"machine,downtime_minutes\n"
    b"M1,10\n"
    b"M1,20\n"
    b"M2,40\n"
    b"M2,50\n"
)

QUALITY_CSV = (
    b"machine,good_units,total_units\n"
    b"M1,98,100\n"
    b"M2,60,100\n"
)

SCRAP_CSV = (
    b"machine,scrap_units,total_units\n"
    b"M1,2,100\n"
    b"M2,40,100\n"
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data, name="alerts.csv"):
    res = await client.post("/datasets", files={"file": (name, data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_alert_rule_crud(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, DOWNTIME_CSV)

    create = await client.post(
        "/alerts/rules", headers=_h(token),
        json={
            "name": "Downtime watch", "type": "machine_downtime", "dataset_id": dataset_id,
            "config": {"duration_col": "downtime_minutes", "threshold": 60},
        },
    )
    assert create.status_code == 201, create.text
    rule = create.json()
    assert rule["enabled"] is True

    listed = await client.get("/alerts/rules", headers=_h(token))
    assert len(listed.json()) == 1

    updated = await client.put(f"/alerts/rules/{rule['id']}", headers=_h(token), json={"enabled": False})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    deleted = await client.delete(f"/alerts/rules/{rule['id']}", headers=_h(token))
    assert deleted.status_code == 204

    listed_after = await client.get("/alerts/rules", headers=_h(token))
    assert listed_after.json() == []


async def test_machine_downtime_scan_flags_offending_machine(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, DOWNTIME_CSV)

    create = await client.post(
        "/alerts/rules", headers=_h(token),
        json={
            "name": "Downtime watch", "type": "machine_downtime", "dataset_id": dataset_id,
            "config": {"duration_col": "downtime_minutes", "machine_col": "machine", "threshold": 60},
        },
    )
    rule_id = create.json()["id"]

    result = await client.post(f"/alerts/scan/{rule_id}", headers=_h(token))
    assert result.status_code == 200, result.text
    body = result.json()
    # M1 totals 30 (under threshold), M2 totals 90 (over threshold) - only M2 offends.
    assert body["triggered"] is True
    assert body["offenders"] == ["M2"]
    assert body["value"] == 90.0


async def test_quality_scan_computes_defect_rate_per_machine(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, QUALITY_CSV, name="quality.csv")

    create = await client.post(
        "/alerts/rules", headers=_h(token),
        json={
            "name": "Defect watch", "type": "quality", "dataset_id": dataset_id,
            "config": {"good_units_col": "good_units", "total_units_col": "total_units",
                       "machine_col": "machine", "defect_threshold_pct": 5.0},
        },
    )
    assert create.status_code == 201, create.text

    res = await client.post("/alerts/scan", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["evaluated_count"] == 1
    assert body["triggered_count"] == 1
    trigger = body["results"][0]
    # M2's defect rate is 40% (60/100 good), well over the 5% threshold.
    assert trigger["offenders"] == ["M2"]
    assert trigger["value"] == 40.0


async def test_scrap_scan_flags_offending_machine(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, SCRAP_CSV, name="scrap.csv")

    create = await client.post(
        "/alerts/rules", headers=_h(token),
        json={
            "name": "Scrap watch", "type": "scrap", "dataset_id": dataset_id,
            "config": {"scrap_col": "scrap_units", "total_units_col": "total_units",
                       "group_col": "machine", "scrap_threshold_pct": 5.0},
        },
    )
    assert create.status_code == 201, create.text

    res = await client.post("/alerts/scan", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["evaluated_count"] == 1
    assert body["triggered_count"] == 1
    trigger = body["results"][0]
    # M2's scrap rate is 40% (40/100), well over the 5% threshold; M1 (2%) is fine.
    assert trigger["offenders"] == ["M2"]
    assert trigger["value"] == 40.0


async def test_scan_reports_missing_column_without_failing_whole_scan(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, DOWNTIME_CSV)

    create = await client.post(
        "/alerts/rules", headers=_h(token),
        json={
            "name": "Bad config", "type": "machine_downtime", "dataset_id": dataset_id,
            "config": {"duration_col": "does_not_exist", "threshold": 60},
        },
    )
    rule_id = create.json()["id"]

    result = await client.post(f"/alerts/scan/{rule_id}", headers=_h(token))
    assert result.status_code == 200
    body = result.json()
    assert body["triggered"] is False
    assert "Column(s) not found" in body["message"]
    assert "duration" in body["message"]


async def test_scan_one_handles_blank_required_column_without_500(client):
    """A rule saved with an empty duration_col (the RuleForm's "required" flag
    is cosmetic - it doesn't block saving) used to reach DuckDB with an empty
    quoted identifier and 500 on the single-rule Test scan, even though the
    same case scanned fine via /alerts/scan. Both must fail gracefully now."""
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, DOWNTIME_CSV)

    create = await client.post(
        "/alerts/rules", headers=_h(token),
        json={
            "name": "Blank column", "type": "machine_downtime", "dataset_id": dataset_id,
            "config": {"duration_col": "", "threshold": 60},
        },
    )
    rule_id = create.json()["id"]

    result = await client.post(f"/alerts/scan/{rule_id}", headers=_h(token))
    assert result.status_code == 200
    assert result.json()["triggered"] is False


async def test_two_rules_on_the_same_dataset_scan_independently(client):
    """scan_all now materializes each scanned dataset once and shares that
    connection across every rule targeting it (see alerts.py's _materialize) -
    this checks that sharing doesn't cross-contaminate two different rules'
    results for the same dataset."""
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, DOWNTIME_CSV)

    await client.post(
        "/alerts/rules", headers=_h(token),
        json={
            "name": "Low threshold", "type": "machine_downtime", "dataset_id": dataset_id,
            "config": {"duration_col": "downtime_minutes", "machine_col": "machine", "threshold": 5},
        },
    )
    await client.post(
        "/alerts/rules", headers=_h(token),
        json={
            "name": "High threshold", "type": "machine_downtime", "dataset_id": dataset_id,
            "config": {"duration_col": "downtime_minutes", "machine_col": "machine", "threshold": 1000},
        },
    )

    res = await client.post("/alerts/scan", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["evaluated_count"] == 2
    by_name = {r["rule_name"]: r for r in body["results"]}
    assert by_name["Low threshold"]["triggered"] is True
    assert by_name["Low threshold"]["offenders"] == ["M1", "M2"]
    assert by_name["High threshold"]["triggered"] is False


async def test_alert_rule_not_found(client):
    token = await _register_and_login(client)
    res = await client.get("/alerts/rules", headers=_h(token))
    assert res.status_code == 200
    res = await client.delete("/alerts/rules/00000000-0000-0000-0000-000000000000", headers=_h(token))
    assert res.status_code == 404


async def test_alert_rule_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    dataset_id = await _upload(client, token_a, DOWNTIME_CSV)

    create = await client.post(
        "/alerts/rules", headers=_h(token_a),
        json={
            "name": "Downtime watch", "type": "machine_downtime", "dataset_id": dataset_id,
            "config": {"duration_col": "downtime_minutes", "threshold": 60},
        },
    )
    rule_id = create.json()["id"]

    res_b = await client.post(f"/alerts/scan/{rule_id}", headers=_h(token_b))
    assert res_b.status_code == 404
