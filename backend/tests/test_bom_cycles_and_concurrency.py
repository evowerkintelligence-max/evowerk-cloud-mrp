from __future__ import annotations

import uuid

import pytest

from tests._bom_helpers import (
    _register_and_login,
    _create_item,
)

pytestmark = pytest.mark.asyncio


async def test_activate_rejects_immediate_self_reference(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token, sku="FG-3", name="Loopy", item_type="finished_good")
    bom = await client.post("/boms", json={"item_id": item["id"]}, headers=headers)
    bom_data = bom.json()

    res = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [{"component_item_id": item["id"], "quantity": "1", "sequence": 0}],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert res.status_code == 409


async def test_edit_rejects_multilevel_cycle(client):
    """A -> B (active), then editing a draft B -> A BOM must be rejected on the
    PATCH itself - the same check activate() runs, but surfaced immediately so
    the user doesn't build out a whole cyclic draft before finding out."""
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item_a = await _create_item(client, token, sku="A", name="Item A", item_type="finished_good")
    item_b = await _create_item(client, token, sku="B", name="Item B", item_type="component")

    bom_a = await client.post("/boms", json={"item_id": item_a["id"]}, headers=headers)
    bom_a_data = bom_a.json()
    await client.patch(
        f"/boms/{bom_a_data['id']}",
        json={
            "lines": [{"component_item_id": item_b["id"], "quantity": "1", "sequence": 0}],
            "expected_updated_at": bom_a_data["updated_at"],
        },
        headers=headers,
    )
    activate_a = await client.post(f"/boms/{bom_a_data['id']}/activate", headers=headers)
    assert activate_a.status_code == 200

    bom_b = await client.post("/boms", json={"item_id": item_b["id"]}, headers=headers)
    bom_b_data = bom_b.json()
    edit_b = await client.patch(
        f"/boms/{bom_b_data['id']}",
        json={
            "lines": [{"component_item_id": item_a["id"], "quantity": "1", "sequence": 0}],
            "expected_updated_at": bom_b_data["updated_at"],
        },
        headers=headers,
    )
    assert edit_b.status_code == 409


async def test_deep_acyclic_chain_activates_without_a_depth_ceiling(client):
    """A real ERP-scale BOM tree can nest well past 20 levels without ever
    cycling back on itself. There's no fixed depth cap on the cycle walk (it's
    naturally bounded by the org's item count), so a long acyclic chain must
    activate cleanly at every level instead of hitting a false-positive."""
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # I0 -> I1 -> ... -> I29, no closing edge - 30 levels, well past the old
    # MAX_CYCLE_CHECK_DEPTH=20 constant that used to fail closed here.
    n = 30
    items = [await _create_item(client, token, sku=f"CHAIN-{i}", name=f"Item {i}") for i in range(n)]
    for i in range(n - 1):
        bom = await client.post("/boms", json={"item_id": items[i]["id"]}, headers=headers)
        bom_data = bom.json()
        await client.patch(
            f"/boms/{bom_data['id']}",
            json={
                "lines": [{"component_item_id": items[i + 1]["id"], "quantity": "1", "sequence": 0}],
                "expected_updated_at": bom_data["updated_at"],
            },
            headers=headers,
        )
        activated = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
        assert activated.status_code == 200


async def test_deep_cycle_rejected_on_the_closing_edit(client):
    """Same 30-level chain, but this time the last item's BOM closes the loop
    back to the first. The closing PATCH itself must be rejected - the walk
    has no depth cap to run out of, so this can't be a fail-closed false
    positive, it's a real cycle."""
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    n = 30
    items = [await _create_item(client, token, sku=f"CYC-{i}", name=f"Item {i}") for i in range(n)]
    for i in range(n - 1):
        bom = await client.post("/boms", json={"item_id": items[i]["id"]}, headers=headers)
        bom_data = bom.json()
        await client.patch(
            f"/boms/{bom_data['id']}",
            json={
                "lines": [{"component_item_id": items[i + 1]["id"], "quantity": "1", "sequence": 0}],
                "expected_updated_at": bom_data["updated_at"],
            },
            headers=headers,
        )
        activated = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
        assert activated.status_code == 200

    # Close the loop: I29 -> I0. Rejected on the PATCH, before activation.
    closing_bom = await client.post("/boms", json={"item_id": items[n - 1]["id"]}, headers=headers)
    closing_bom_data = closing_bom.json()
    res = await client.patch(
        f"/boms/{closing_bom_data['id']}",
        json={
            "lines": [{"component_item_id": items[0]["id"], "quantity": "1", "sequence": 0}],
            "expected_updated_at": closing_bom_data["updated_at"],
        },
        headers=headers,
    )
    assert res.status_code == 409


async def test_revise_bom_survives_version_collision_retry(client, monkeypatch):
    """Regression test for the retry loop expiring `source`/`source_lines` on
    rollback (see revise_bom): forces one IntegrityError on the first
    version-allocation flush, so the loop takes its rollback+retry path, then
    confirms the endpoint still succeeds and clones the source's lines
    correctly rather than 500ing on an expired ORM attribute read.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.bom import Bom

    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="REV-RACE-FG", name="Widget", item_type="finished_good")
    comp = await _create_item(client, token, sku="REV-RACE-1", name="Bolt", item_type="raw_material")

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {"component_item_id": comp["id"], "quantity": "4.5", "sequence": 0, "scrap_pct": "5"}
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200

    # Only intercept the flush that's about to insert the new draft Bom (the
    # version-allocation flush inside revise_bom's retry loop) - not any of
    # the setup flushes above, and not the second attempt's flush.
    original_flush = AsyncSession.flush
    state = {"raised": False}

    async def flaky_flush(self, *args, **kwargs):
        pending_boms = [obj for obj in self.new if isinstance(obj, Bom)]
        if pending_boms and not state["raised"]:
            state["raised"] = True
            raise IntegrityError("INSERT INTO boms ...", {}, Exception("uq_boms_org_item_version"))
        return await original_flush(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", flaky_flush)

    revise = await client.post(f"/boms/{bom_data['id']}/revise", headers=headers)

    assert state["raised"], "test setup didn't actually trigger the forced collision"
    assert revise.status_code == 201
    revised = revise.json()
    assert revised["status"] == "draft"
    assert revised["version"] == 2
    assert len(revised["lines"]) == 1
    assert revised["lines"][0]["component_sku"] == "REV-RACE-1"
    assert revised["lines"][0]["quantity"] == "4.5000"
    assert revised["lines"][0]["scrap_pct"] == "5.00"


async def test_create_bom_survives_version_collision_retry(client, monkeypatch):
    """Regression test for the same expired-`item` hazard revise_bom already
    has a test for: create_bom's retry loop also calls db.rollback() on a
    version collision, which expires the `item` fetched before the loop. The
    fix is to let _bom_to_response refetch item instead of reusing that
    (possibly expired) reference.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.bom import Bom

    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="CREATE-RACE-1", name="Widget", item_type="finished_good")

    original_flush = AsyncSession.flush
    state = {"raised": False}

    async def flaky_flush(self, *args, **kwargs):
        pending_boms = [obj for obj in self.new if isinstance(obj, Bom)]
        if pending_boms and not state["raised"]:
            state["raised"] = True
            raise IntegrityError("INSERT INTO boms ...", {}, Exception("uq_boms_org_item_version"))
        return await original_flush(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", flaky_flush)

    res = await client.post("/boms", json={"item_id": item["id"]}, headers=headers)

    assert state["raised"], "test setup didn't actually trigger the forced collision"
    assert res.status_code == 201
    body = res.json()
    assert body["item_sku"] == "CREATE-RACE-1"
    assert body["version"] == 1


async def test_update_bom_lines_rejects_oversized_unit_quantity(client):
    """unit_quantity shares BomLine.quantity's NUMERIC(12,4) column, so it
    needs the same MAX_LINE_QUANTITY upper-bound guard - otherwise a large
    unit_quantity paired with a small factor_to_base passes the derived
    quantity check while overflowing the raw column on insert.
    """
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="OVERFLOW-FG", name="Widget", item_type="finished_good")
    comp = await _create_item(client, token, sku="OVERFLOW-1", name="Wire", item_type="raw_material")

    unit = await client.post(
        f"/items/{comp['id']}/units",
        json={"code": "spool", "factor_to_base": "0.000001"},
        headers=headers,
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()

    res = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": comp["id"],
                    "quantity": "1",
                    "sequence": 0,
                    "unit_id": unit_id,
                    "unit_quantity": "100000000",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert res.status_code == 400


async def test_db_rejects_two_active_boms_for_same_item(client, test_engine):
    """uq_boms_org_item_active (app/models/bom.py) is the DB-level backstop
    for activate_bom's app-level one-active-per-item invariant - it has to
    hold even for writes that bypass that route entirely (a bad migration, a
    direct DB write), so it's tested by inserting a second active BOM
    directly through a session rather than through activate_bom.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.bom import Bom
    from app.models.user import User

    token, org_id = await _register_and_login(client)
    item = await _create_item(client, token, sku="DIRECT-ACTIVE-1", name="Widget", item_type="finished_good")

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        user_id = (await session.execute(select(User.id).where(User.org_id == uuid.UUID(org_id)))).scalar_one()
        session.add(
            Bom(
                org_id=uuid.UUID(org_id),
                item_id=uuid.UUID(item["id"]),
                version=1,
                status="active",
                created_by=user_id,
            )
        )
        await session.commit()

        session.add(
            Bom(
                org_id=uuid.UUID(org_id),
                item_id=uuid.UUID(item["id"]),
                version=2,
                status="active",
                created_by=user_id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
