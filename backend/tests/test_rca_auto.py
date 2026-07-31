from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.bom import Item
from app.models.inventory import InventoryTransaction

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret - satisfies policy: 12+ chars, letter + digit


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
) -> list[str]:
    """Direct DB insert - same helper pattern as test_rca.py."""
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
                    item_type="raw_material",
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
    """Gives each item real consumption history with no on-hand received -
    status becomes "stockout", same helper pattern as test_rca.py."""
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


async def test_no_items_returns_no_significant_dimension(client, test_engine):
    _, token = await _register_and_login(client)
    res = await client.get("/inventory/rca/auto", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["method"] == "no_significant_dimension"
    assert body["dimension"] == ""
    assert body["chi2_statistic"] is None
    assert body["values"] == []


async def test_picks_the_significant_supplier_dimension(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)
    supplier_a = await _create_supplier(client, token, "Supplier A")
    supplier_b = await _create_supplier(client, token, "Supplier B")

    # Same perfect-separation setup as test_rca.py's
    # test_significant_association_by_supplier: A is all stockout, B is all
    # healthy/no_data. item_type is constant ("raw_material") across every
    # item here, and abc_class is unclassified for all of them (no price on
    # file), so supplier is the only dimension that can come out significant.
    stockout_items = await _bulk_create_items(
        test_engine, org_id=org_id, sku_prefix="A", count=10, supplier_id=supplier_a["id"]
    )
    await _insert_consumption_for_items(
        test_engine, org_id=org_id, item_ids=stockout_items, location_id=location_id, created_by=user_id
    )
    await _bulk_create_items(test_engine, org_id=org_id, sku_prefix="B", count=10, supplier_id=supplier_b["id"])

    res = await client.get("/inventory/rca/auto", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["dimension"] == "supplier"
    assert body["method"] == "chi_square_independence"
    assert any(v["is_significant"] for v in body["values"])

    # Assert which specific value wins, not just that something is
    # significant - Supplier A is the all-stockout supplier, so it should
    # have a positive standardized residual and be the top-ranked value.
    top_value = body["values"][0]
    assert top_value["value"] == "Supplier A"
    assert top_value["is_significant"] is True
    assert top_value["standardized_residual"] > 0


async def test_org_isolation(client, test_engine):
    reg_a, _token_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    await _bulk_create_items(test_engine, org_id=reg_a["org_id"], sku_prefix="A", count=5)
    _, token_b = await _register_and_login(client, email="b@example.com", org_name="Org B")

    res = await client.get("/inventory/rca/auto", headers={"Authorization": f"Bearer {token_b}"})
    assert res.status_code == 200
    assert res.json()["method"] == "no_significant_dimension"
