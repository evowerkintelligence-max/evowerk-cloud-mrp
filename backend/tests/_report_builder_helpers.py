from __future__ import annotations


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
    res = await client.post("/datasets", headers=headers, files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]


async def _create_chart(client, token, dataset_id):
    headers = {"Authorization": f"Bearer {token}"}
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
        "name": "Qty by SKU",
    }
    res = await client.post("/bi/charts", headers=headers, json=body)
    assert res.status_code == 201
    return res.json()["id"]


def _report_body(dataset_id, chart_id):
    return {
        "name": "Weekly ops summary",
        "sections": [
            {"type": "title", "title": "Overview", "text": "Week 12 summary"},
            {"type": "text", "title": "Notes", "text": "Everything nominal.\nNo incidents."},
            {"type": "findings", "title": "Findings", "items": ["Region west trending down"]},
            {
                "type": "table",
                "title": "Stock by region",
                "dataset_id": dataset_id,
                "query": {"select": [{"column": "sku"}, {"column": "region"}, {"column": "qty"}]},
            },
            {"type": "bi_chart", "title": "Qty by SKU", "chart_id": chart_id},
        ],
    }
