from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import uuid

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.tenant import Org

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"], reg.json()["org_id"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_gated_route_403s_without_entitlement_then_200s_with_it(client):
    token, org_id = await _register_and_login(client)

    res = await client.get("/boms", headers=_h(token))
    assert res.status_code == 403, res.text

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == uuid.UUID(org_id)))).scalar_one()
        org.enabled_features = ["boms"]
        await db.commit()

    res = await client.get("/boms", headers=_h(token))
    assert res.status_code == 200, res.text


async def test_ungated_route_ignores_entitlements(client):
    token, _org_id = await _register_and_login(client)

    # /datasets is deliberately never gated - Datasets is plumbing every
    # feature depends on, not a sellable feature itself (see the design spec's
    # Scope decision on Data & Reports).
    res = await client.get("/datasets", headers=_h(token))
    assert res.status_code == 200, res.text


async def test_suppliers_route_ignores_entitlements(client):
    token, _org_id = await _register_and_login(client)

    # suppliers is master-data plumbing (see features.py's module docstring),
    # not a sellable feature - unlike purchase_orders below, which is the
    # actual "procurement" feature.
    res = await client.get("/suppliers", headers=_h(token))
    assert res.status_code == 200, res.text


async def test_purchase_orders_route_403s_without_procurement_entitlement(client):
    token, org_id = await _register_and_login(client)

    res = await client.get("/purchase-orders", headers=_h(token))
    assert res.status_code == 403, res.text

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == uuid.UUID(org_id)))).scalar_one()
        org.enabled_features = ["procurement"]
        await db.commit()

    res = await client.get("/purchase-orders", headers=_h(token))
    assert res.status_code == 200, res.text


async def test_orgs_me_exposes_enabled_features(client):
    token, org_id = await _register_and_login(client)

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == uuid.UUID(org_id)))).scalar_one()
        org.enabled_features = ["boms", "oee"]
        await db.commit()

    res = await client.get("/orgs/me", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled_features"] == ["boms", "oee"]
