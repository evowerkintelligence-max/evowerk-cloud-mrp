"""The sales feature key is only real if existing orgs actually get it.

Org.enabled_features is a stored JSON array, so adding a key to the FEATURES
registry does nothing for rows already in the database. The suite builds its
schema with create_all and never runs Alembic, so the backfill in the sales
migration has no other coverage.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import sqlalchemy as sa

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "b41f7c2e8d05_sales_orders.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("sales_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _orgs_table(metadata):
    return sa.Table(
        "orgs",
        metadata,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("enabled_features", sa.JSON(), nullable=False),
    )


def _seed(connection, table, rows):
    ids = {}
    for name, features in rows.items():
        ids[name] = uuid.uuid4()
        connection.execute(table.insert().values(id=ids[name], enabled_features=features))
    return ids


def _features(connection, table, org_id):
    return connection.execute(
        sa.select(table.c.enabled_features).where(table.c.id == org_id)
    ).scalar_one()


def test_backfill_grants_sales_without_disturbing_other_entitlements():
    migration = _load_migration()
    metadata = sa.MetaData()
    table = _orgs_table(metadata)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata.create_all(connection)
        ids = _seed(
            connection,
            table,
            {
                # A Core org, the shape b2e9f4a71c83 left every pre-existing
                # customer in.
                "core": ["boms", "procurement", "planning"],
                # A Premium org: its premium keys must survive untouched.
                "premium": ["boms", "procurement", "simulation", "automl"],
                # Not provisioned: deliberately entitled to nothing.
                "empty": [],
                # Already granted, e.g. a re-run of the migration.
                "already": ["boms", "sales"],
            },
        )

        migration._set_sales_entitlement(granted=True, connection=connection)

        assert _features(connection, table, ids["core"]) == [
            "boms",
            "procurement",
            "planning",
            "sales",
        ]
        assert _features(connection, table, ids["premium"]) == [
            "boms",
            "procurement",
            "simulation",
            "automl",
            "sales",
        ], "a premium org must keep its premium keys"
        assert _features(connection, table, ids["empty"]) == [], (
            "an unprovisioned org must not be handed a feature nobody granted it"
        )
        assert _features(connection, table, ids["already"]) == ["boms", "sales"], (
            "granting twice must not duplicate the key"
        )


def test_downgrade_removes_only_the_sales_key():
    migration = _load_migration()
    metadata = sa.MetaData()
    table = _orgs_table(metadata)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata.create_all(connection)
        ids = _seed(connection, table, {"core": ["boms", "sales", "planning"]})

        migration._set_sales_entitlement(granted=False, connection=connection)

        assert _features(connection, table, ids["core"]) == ["boms", "planning"]
