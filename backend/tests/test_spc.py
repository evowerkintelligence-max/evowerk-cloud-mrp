from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD

# Seven 10s then a 50: mean=15, MR-bar=40/7, UCL~30.2 -> the 50 is out of control.
SPC_CSV = b"value\n10\n10\n10\n10\n10\n10\n10\n50\n"


async def _token(client):
    await client.post("/auth/register", json={"org_name": "Acme", "email": "o@x.com", "password": PASSWORD})
    r = await client.post("/auth/login", json={"email": "o@x.com", "password": PASSWORD})
    return r.json()["access_token"]


async def _upload(client, token, data):
    h = {"Authorization": f"Bearer {token}"}
    r = await client.post("/datasets", files={"file": ("v.csv", data, "text/csv")}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_spc_individuals_out_of_control(client):
    token = await _token(client)
    ds = await _upload(client, token, SPC_CSV)
    r = await client.post(
        "/analytics/spc",
        json={"dataset_id": ds, "value_col": "value"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["chart_type"] == "individuals"
    assert b["out_of_control_count"] == 1
    outlier = next(p for p in b["points"] if p["value"] == 50.0)
    assert outlier["in_control"] is False


async def test_spc_too_few_points(client):
    token = await _token(client)
    ds = await _upload(client, token, b"value\n10\n")
    r = await client.post(
        "/analytics/spc",
        json={"dataset_id": ds, "value_col": "value"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
