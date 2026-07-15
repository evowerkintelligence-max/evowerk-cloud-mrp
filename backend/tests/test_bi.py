from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

CSV = b"sku,qty,region\nA-1,10,east\nA-2,5,west\nA-1,3,west\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, content=CSV, filename="stock.csv"):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": (filename, content, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


def _chart_body(dataset_id, **overrides):
    body = {
        "dataset_id": dataset_id,
        "chart_type": "bar",
        "query": {
            "select": [{"column": "sku"}, {"column": "qty", "agg": "sum", "alias": "total_qty"}],
            "group_by": ["sku"],
            "order_by": "sku",
        },
        "category_field": "sku",
        "value_field": "total_qty",
    }
    body.update(overrides)
    return body


async def test_chart_preview_matches_manual_aggregate(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post("/bi/charts/preview", headers=headers, json=_chart_body(dataset_id))
    assert res.status_code == 200
    rows = {r["sku"]: r["total_qty"] for r in res.json()["rows"]}
    assert rows == {"A-1": 13.0, "A-2": 5.0}
    assert res.json()["chart_type"] == "bar"


async def test_invalid_chart_type_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        "/bi/charts/preview", headers=headers, json=_chart_body(dataset_id, chart_type="scatter")
    )
    assert res.status_code == 400


async def test_save_chart_and_fetch_data(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        "/bi/charts", headers=headers, json={**_chart_body(dataset_id), "name": "Qty by SKU"}
    )
    assert res.status_code == 201
    chart = res.json()
    assert chart["name"] == "Qty by SKU"
    assert chart["dashboard_id"] is None

    data = await client.get(f"/bi/charts/{chart['id']}/data", headers=headers)
    assert data.status_code == 200
    rows = {r["sku"]: r["total_qty"] for r in data.json()["rows"]}
    assert rows == {"A-1": 13.0, "A-2": 5.0}


async def test_list_charts_scoped_to_org(client):
    token_a = await _register_and_login(client, email="la@example.com", org_name="ListOrgA")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    dataset_a = await _upload(client, token_a)
    chart_a = await client.post(
        "/bi/charts", headers=headers_a, json={**_chart_body(dataset_a), "name": "A's chart"}
    )
    assert chart_a.status_code == 201

    token_b = await _register_and_login(client, email="lb@example.com", org_name="ListOrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    dataset_b = await _upload(client, token_b)
    chart_b = await client.post(
        "/bi/charts", headers=headers_b, json={**_chart_body(dataset_b), "name": "B's chart"}
    )
    assert chart_b.status_code == 201

    listed_a = await client.get("/bi/charts", headers=headers_a)
    assert listed_a.status_code == 200
    names_a = {c["name"] for c in listed_a.json()}
    assert names_a == {"A's chart"}


async def test_dashboard_crud_with_charts(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    dash = await client.post("/bi/dashboards", headers=headers, json={"name": "Ops"})
    assert dash.status_code == 201
    dashboard_id = dash.json()["id"]

    listed = await client.get("/bi/dashboards", headers=headers)
    assert listed.status_code == 200
    assert any(d["id"] == dashboard_id for d in listed.json())

    c1 = (
        await client.post(
            "/bi/charts",
            headers=headers,
            json={**_chart_body(dataset_id), "name": "Chart 1", "dashboard_id": dashboard_id},
        )
    ).json()
    c2 = (
        await client.post(
            "/bi/charts",
            headers=headers,
            json={
                **_chart_body(dataset_id, chart_type="pie"),
                "name": "Chart 2",
                "dashboard_id": dashboard_id,
            },
        )
    ).json()

    detail = await client.get(f"/bi/dashboards/{dashboard_id}", headers=headers)
    assert detail.status_code == 200
    chart_ids = {c["id"] for c in detail.json()["charts"]}
    assert chart_ids == {c1["id"], c2["id"]}

    removed = await client.delete(f"/bi/charts/{c1['id']}", headers=headers)
    assert removed.status_code == 204

    detail2 = await client.get(f"/bi/dashboards/{dashboard_id}", headers=headers)
    assert [c["id"] for c in detail2.json()["charts"]] == [c2["id"]]


async def test_cross_org_isolation_on_dashboard_and_chart(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    dataset_id = await _upload(client, token_a)
    headers_a = {"Authorization": f"Bearer {token_a}"}

    dash = await client.post("/bi/dashboards", headers=headers_a, json={"name": "OrgA dash"})
    dashboard_id = dash.json()["id"]
    chart = await client.post(
        "/bi/charts",
        headers=headers_a,
        json={**_chart_body(dataset_id), "name": "OrgA chart", "dashboard_id": dashboard_id},
    )
    chart_id = chart.json()["id"]

    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    assert (await client.get(f"/bi/dashboards/{dashboard_id}", headers=headers_b)).status_code == 404
    assert (await client.get(f"/bi/charts/{chart_id}/data", headers=headers_b)).status_code == 404
    assert (await client.delete(f"/bi/dashboards/{dashboard_id}", headers=headers_b)).status_code == 404


async def test_deleting_dataset_cascades_its_charts(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    chart = await client.post(
        "/bi/charts", headers=headers, json={**_chart_body(dataset_id), "name": "Chart"}
    )
    chart_id = chart.json()["id"]

    deleted = await client.delete(f"/datasets/{dataset_id}", headers=headers)
    assert deleted.status_code == 204

    assert (await client.get(f"/bi/charts/{chart_id}/data", headers=headers)).status_code == 404
