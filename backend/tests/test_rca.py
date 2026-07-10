from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.bom import Item
from app.models.inventory import InventoryTransaction

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _create_supplier(client, token, name="Supplier"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _default_location_id(client, token) -> str:
    res = await client.get("/locations", headers={"Authorization": f"Bearer {token}"})
    return next(loc["id"] for loc in res.json() if loc["is_default"])


async def _bulk_create_items(
    test_engine,
    *,
    org_id: str,
    sku_prefix: str,
    count: int,
    supplier_id: str | None = None,
    item_type: str = "raw_material",
) -> list[str]:
    """Direct DB insert - faster than round-tripping the API for a batch this
    size, same as this file's other direct-DB test setup helpers."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ids: list[uuid_module.UUID] = []
    async with session_factory() as session:
        for i in range(count):
            item_id = uuid_module.uuid4()
            ids.append(item_id)
            session.add(
                Item(
                    id=item_id,
                    org_id=uuid_module.UUID(org_id),
                    sku=f"{sku_prefix}-{i}",
                    name=f"{sku_prefix} {i}",
                    item_type=item_type,
                    unit_of_measure="ea",
                    is_lot_tracked=False,
                    preferred_supplier_id=uuid_module.UUID(supplier_id) if supplier_id else None,
                )
            )
        await session.commit()
    return [str(i) for i in ids]


async def _insert_consumption_for_items(
    test_engine, *, org_id: str, item_ids: list[str], location_id: str, created_by: str, qty=5
):
    """Gives each item some real consumption history (avg_daily_demand > 0)
    with no on-hand received - status becomes "stockout", not "no_data"."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for item_id in item_ids:
            session.add(
                InventoryTransaction(
                    org_id=uuid_module.UUID(org_id),
                    item_id=uuid_module.UUID(item_id),
                    location_id=uuid_module.UUID(location_id),
                    transaction_type="consumption",
                    quantity_delta=-Decimal(str(qty)),
                    created_by=uuid_module.UUID(created_by),
                    created_at=now,
                )
            )
        await session.commit()


def _by_value(rca_json) -> dict:
    return {v["value"]: v for v in rca_json["values"]}


async def test_significant_association_by_supplier(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)
    supplier_a = await _create_supplier(client, token, "Supplier A")
    supplier_b = await _create_supplier(client, token, "Supplier B")

    # Perfect separation, N=20: A is all stockout, B is all no_data (healthy
    # side). Expected count per cell is exactly 10*10/20 = 5 - right at (not
    # under) the low-expected-frequency threshold, so the warning must be off.
    stockout_items = await _bulk_create_items(
        test_engine, org_id=org_id, sku_prefix="A", count=10, supplier_id=supplier_a["id"]
    )
    await _insert_consumption_for_items(
        test_engine, org_id=org_id, item_ids=stockout_items, location_id=location_id, created_by=user_id
    )
    await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="B", count=10, supplier_id=supplier_b["id"])

    res = await client.get("/inventory/rca?dimension=supplier", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["dimension"] == "supplier"
    assert body["method"] == "chi_square_independence"
    assert body["p_value"] < 0.05
    assert body["low_expected_frequency_warning"] is False

    values = _by_value(body)
    assert values["Supplier A"]["at_risk_count"] == 10
    assert values["Supplier A"]["item_count"] == 10
    assert values["Supplier A"]["is_significant"] is True
    assert values["Supplier A"]["standardized_residual"] > 0

    assert values["Supplier B"]["at_risk_count"] == 0
    assert values["Supplier B"]["is_significant"] is True
    assert values["Supplier B"]["standardized_residual"] < 0

    # Ranked by |residual| descending.
    assert abs(body["values"][0]["standardized_residual"]) >= abs(body["values"][1]["standardized_residual"])


async def test_low_expected_frequency_warning_on_small_samples(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)
    supplier_a = await _create_supplier(client, token, "Tiny A")
    supplier_b = await _create_supplier(client, token, "Tiny B")

    stockout_items = await _bulk_create_items(
        test_engine, org_id=org_id, sku_prefix="TA", count=1, supplier_id=supplier_a["id"]
    )
    await _insert_consumption_for_items(
        test_engine, org_id=org_id, item_ids=stockout_items, location_id=location_id, created_by=user_id
    )
    await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="TB", count=1, supplier_id=supplier_b["id"])

    res = await client.get("/inventory/rca?dimension=supplier", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["method"] == "chi_square_independence"
    assert body["low_expected_frequency_warning"] is True


async def test_no_at_risk_items_is_insufficient_data(client, test_engine):
    """Every item healthy/no_data - the at-risk column is entirely zero, which
    scipy can't build an expected-frequency table from. Must degrade to
    insufficient_data, not raise a 500."""
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = reg["org_id"]
    supplier_a = await _create_supplier(client, token, "Supplier A")
    supplier_b = await _create_supplier(client, token, "Supplier B")
    await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="A", count=5, supplier_id=supplier_a["id"])
    await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="B", count=3, supplier_id=supplier_b["id"])

    res = await client.get("/inventory/rca?dimension=supplier", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["method"] == "insufficient_data"
    assert body["p_value"] is None
    assert body["values"] == []


async def test_single_dimension_value_is_insufficient_data(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = reg["org_id"]
    # No preferred supplier on any item -> everything buckets into
    # "unassigned", a single dimension value with nothing to compare against.
    await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="X", count=5)

    res = await client.get("/inventory/rca?dimension=supplier", headers=headers)
    assert res.status_code == 200
    assert res.json()["method"] == "insufficient_data"


async def test_no_items_is_insufficient_data(client, test_engine):
    _, token = await _register_and_login(client)
    res = await client.get("/inventory/rca?dimension=supplier", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["method"] == "insufficient_data"
    assert body["values"] == []


async def test_item_type_and_abc_class_dimensions_run(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)

    raw_items = await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="RAW", count=6, item_type="raw_material")
    await _insert_consumption_for_items(
        test_engine, org_id=org_id, item_ids=raw_items, location_id=location_id, created_by=user_id
    )
    await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="FG", count=6, item_type="finished_good")

    for dimension in ("item_type", "abc_class"):
        res = await client.get(f"/inventory/rca?dimension={dimension}", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["dimension"] == dimension
        assert body["method"] in ("chi_square_independence", "insufficient_data")


async def test_invalid_dimension_returns_400(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get("/inventory/rca?dimension=bogus", headers=headers)
    assert res.status_code == 400


async def test_org_isolation(client, test_engine):
    reg_a, _token_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    await _bulk_create_items(test_engine, org_id=reg_a["org_id"], sku_prefix="A", count=5)
    _, token_b = await _register_and_login(client, email="b@example.com", org_name="Org B")

    res = await client.get("/inventory/rca?dimension=supplier", headers={"Authorization": f"Bearer {token_b}"})
    assert res.status_code == 200
    assert res.json()["method"] == "insufficient_data"
    assert res.json()["values"] == []
