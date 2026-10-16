# backend/tests/test_dataset_overwrite.py
from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import datasets as ds_service

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


async def test_overwrite_dataset_file_updates_content_and_keeps_id(client, test_engine):
    reg = await client.post(
        "/auth/register",
        json={"org_name": "OverwriteOrg", "email": "overwrite@example.com", "password": PASSWORD},
    )
    assert reg.status_code == 201
    body = reg.json()
    org_id = uuid.UUID(str(body["org_id"]))
    user_id = uuid.UUID(str(body["user_id"]))

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = await ds_service.create_dataset(
            db, org_id=org_id, created_by=user_id, name="Original",
            filename="original.csv", data=b"a,b\n1,2\n",
        )
        await db.commit()
        original_id = dataset.id
        assert dataset.row_count == 1

        new_bytes = b"a,b\n1,2\n3,4\n5,6\n"
        await ds_service.overwrite_dataset_file(db, dataset, data=new_bytes)
        await db.commit()

        assert dataset.id == original_id
        assert dataset.row_count == 3
        assert dataset.size_bytes == len(new_bytes)

        con = ds_service.open_con()
        try:
            rel = ds_service.dataset_relation(dataset)
            count = con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]  # nosec B608
        finally:
            con.close()
        assert count == 3
