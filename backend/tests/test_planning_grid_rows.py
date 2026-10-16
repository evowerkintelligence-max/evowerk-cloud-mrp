from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import hash_password
from app.models.user import User
from app.services.planning_grid import GRID_HORIZON_DAYS
from tests._planning_grid_helpers import (
    PASSWORD,
    _register_and_login,
    _create_item,
    _create_active_bom,
    _plan,
    _grid,
    _product,
    _today,
)

pytestmark = pytest.mark.asyncio


async def test_only_top_level_products_get_rows(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    sub = await _create_item(client, token, "SUB-1")
    pump = await _create_item(client, token, "PUMP-A2", name="Coolant pump")
    await _create_active_bom(client, token, sub, [(seal, 2)])
    await _create_active_bom(client, token, pump, [(sub, 1)])

    grid = await _grid(client, token)

    skus = {row["item_sku"] for row in grid["products"]}
    assert skus == {"PUMP-A2"}, "sub-assemblies and raw components must not get their own rows"
    assert _product(grid, "PUMP-A2")["item_name"] == "Coolant pump"


async def test_forecasted_item_without_bom_gets_a_row(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    today = datetime.now(timezone.utc).date()
    needed_by = str(today + timedelta(days=10))
    await _plan(client, token, widget, 10, needed_by=needed_by)
    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["planned_qty"] == "10.0000"
    assert Decimal(row["on_hand"]) == Decimal(0)
    assert row["needed_by"] == needed_by


async def test_missing_when_a_component_is_short(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "missing"
    assert row["missing_materials"] == ["SEAL-V2"]
    assert grid["summary"]["missing"] == 1


async def test_in_stock_when_components_are_covered(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    headers = {"Authorization": f"Bearer {token}"}
    adj = await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "500", "reason_code": "found_stock"},
        headers=headers,
    )
    assert adj.status_code == 200, adj.text
    # An owner never queues for approval, so the stock is on the shelf now. If
    # this ever fails, the seeding is the problem, not the grid.
    assert adj.json()["applied"] is True

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "in_stock"
    assert row["missing_materials"] == []
    assert grid["summary"]["in_stock"] == 1


async def test_sub_assembly_shortfall_does_not_turn_the_product_red(client):
    """A sub-assembly's own net_requirement is a build instruction, not a
    material to order. PUMP-A2 has no stock and neither does SUB-1, but every
    buyable part (SEAL-V2) is on the shelf, so the row must be in_stock, not
    missing with SUB-1 listed."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    sub = await _create_item(client, token, "SUB-1", lead_time_days=45)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, sub, [(seal, 2)])
    await _create_active_bom(client, token, pump, [(sub, 1)])
    headers = {"Authorization": f"Bearer {token}"}
    # PUMP-A2 planned at 50 with no stock on either PUMP-A2 or SUB-1 nets a
    # build requirement of 50 SUB-1, which explodes to 100 SEAL-V2 (qty_per 2).
    # Stock SEAL-V2 well above that so it is genuinely covered, not just
    # coincidentally enough.
    adj = await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "500", "reason_code": "found_stock"},
        headers=headers,
    )
    assert adj.status_code == 200, adj.text
    assert adj.json()["applied"] is True

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "in_stock"
    assert row["missing_materials"] == []
    # SUB-1's 45-day lead time must not leak into ready_by: it is manufactured,
    # so only the buyable SEAL-V2 (fully stocked) can set the date.
    assert row["ready_by"] == str(_today())


async def test_member_cannot_set_planned_quantity(client, test_engine):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    me = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    org_id = me.json()["id"]

    # No invite flow exists yet to produce a non-owner user via the API, so
    # construct one directly against the test DB, same as
    # test_bom_lifecycle.test_rbac_blocks_non_owner_from_mutations.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        member = User(
            org_id=uuid.UUID(org_id),
            email="member@example.com",
            hashed_password=hash_password(PASSWORD),
            role="member",
            is_active=True,
        )
        session.add(member)
        await session.commit()

    login = await client.post(
        "/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    member_token = login.json()["access_token"]

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "0"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert res.status_code == 403

    # The grid itself stays open to everyone.
    read = await client.get(
        "/planning/grid", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert read.status_code == 200


async def test_manager_can_set_planned_quantity(client, test_engine):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    me = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        manager = User(
            org_id=uuid.UUID(org_id),
            email="manager@example.com",
            hashed_password=hash_password(PASSWORD),
            role="manager",
            is_active=True,
        )
        session.add(manager)
        await session.commit()

    login = await client.post(
        "/auth/login", json={"email": "manager@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    manager_token = login.json()["access_token"]

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "10", "needed_by": str(_today() + timedelta(days=10))},
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert res.status_code == 200, res.text


async def test_grid_window_is_fixed_90_days_from_today(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, widget, 10)

    grid = await _grid(client, token)

    today = datetime.now(timezone.utc).date()
    assert grid["horizon_start"] == today.isoformat()
    assert grid["horizon_end"] == (today + timedelta(days=GRID_HORIZON_DAYS)).isoformat()


async def test_arriving_when_a_component_is_only_covered_by_an_open_po(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await client.post("/suppliers", json={"name": "Sealtech"}, headers=headers)
    po = await client.post(
        "/purchase-orders", json={"supplier_id": supplier.json()["id"]}, headers=headers
    )
    po_id = po.json()["id"]
    await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": seal["id"], "quantity_ordered": "500", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    submit = await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    assert submit.status_code == 200, submit.text

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "arriving"
    assert row["missing_materials"] == []
    assert grid["summary"]["arriving"] == 1


async def test_purchased_product_judges_itself(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10)
    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["materials_status"] == "missing"
    assert row["missing_materials"] == ["WIDGET-1"]


async def test_missing_rows_sort_above_covered_rows(client):
    token = await _register_and_login(client)
    covered = await _create_item(client, token, "AAA-COVERED")
    short = await _create_item(client, token, "ZZZ-SHORT")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        f"/inventory/{covered['id']}/adjust",
        json={"quantity": "100", "reason_code": "found_stock"},
        headers=headers,
    )

    await _plan(client, token, covered, 5)
    await _plan(client, token, short, 5)
    grid = await _grid(client, token)

    assert [row["item_sku"] for row in grid["products"]] == ["ZZZ-SHORT", "AAA-COVERED"]


async def test_grid_is_org_scoped(client):
    token_a = await _register_and_login(client)
    widget = await _create_item(client, token_a, "WIDGET-1")
    await _plan(client, token_a, widget, 10)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    grid_b = await _grid(client, token_b)

    assert grid_b["products"] == []
    assert grid_b["to_buy"] == []
