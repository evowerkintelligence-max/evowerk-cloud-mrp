from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD


PASSWORD = TEST_PASSWORD


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


SIX_LOSSES_CSV = (
    b"planned_min,operating_min,total_units,good_units,"
    b"unplanned_dt_min,planned_dt_min,minor_stops_min,startup_rejects_units\n"
    b"500,400,350,320,60,30,20,10\n"
)


SWAPPED_COLUMNS_CSV = (
    b"planned_min,operating_min,total_units,good_units\n"
    b"400,500,320,350\n"
)


RATE_BLEND_CSV = (
    b"machine,planned_min,operating_min,total_units,good_units\n"
    b"M1,100,100,80,80\n"
    b"M2,200,200,300,300\n"
)


DROP_PERIOD_CSV = (
    b"date,planned_min,operating_min,total_units,good_units\n"
    b"2026-02-01,100,100,100,100\n"
    b"2026-02-02,100,50,100,100\n"
    b"2026-02-03,100,100,100,100\n"
)


DATE_GAP_CSV = (
    b"date,planned_min,operating_min,total_units,good_units\n"
    b"2026-03-01,100,100,100,100\n"
    b"2026-03-03,100,100,100,100\n"
)


DUPLICATE_ROWS_CSV = (
    b"date,machine,planned_min,operating_min,total_units,good_units\n"
    b"2026-04-01,M1,100,90,100,95\n"
    b"2026-04-01,M1,100,90,100,95\n"
    b"2026-04-01,M2,100,80,100,90\n"
)


LOW_SAMPLE_CSV = (
    b"machine,planned_min,operating_min,total_units,good_units\n"
    + b"M1,10,10,10,10\n" * 6
    + b"M2,10,5,10,10\n" * 2
)
