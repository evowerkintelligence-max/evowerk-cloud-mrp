"""A new core feature key must reach the customers who already exist.

Org.enabled_features is a stored JSON array read by require_feature, so
adding a key to the FEATURES registry does nothing for rows already in the
database: the feature ships invisible to every current customer until someone
re-saves each org by hand. This is not hypothetical, it is exactly what
happened when the `sales` key was added.

No other test can catch it. tests/conftest.py grants entitlements to every
org it creates, so the gate never bites in the suite, and any check derived
from the live registry would move in lockstep with the thing it is meant to
be checking. The only honest guard compares the registry against the
migrations, which are static history.

Premium keys are deliberately excluded: b2e9f4a71c83 chose to grandfather
pre-existing orgs into Core only, leaving premium grants a manual step (see
docs/superpowers/plans/2026-07-23-feature-entitlements-deploy-runbook.md).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core.features import CORE_FEATURES, FEATURES

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Migrations declare what they hand to existing orgs with this attribute.
_MARKER = "GRANTS_FEATURES"


def _backfilled_keys() -> dict[str, list[str]]:
    """Every key any migration backfills, mapped to the migrations granting it."""
    granted: dict[str, list[str]] = {}
    for path in sorted(_VERSIONS.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for key in getattr(module, _MARKER, []):
            granted.setdefault(key, []).append(path.name)
    return granted


def test_every_core_feature_is_backfilled_onto_existing_orgs():
    granted = _backfilled_keys()
    missing = sorted(set(CORE_FEATURES) - set(granted))
    assert not missing, (
        f"Core feature key(s) {missing} are in app.core.features.FEATURES but no "
        f"migration grants them to orgs that already exist, so the feature would "
        f"ship 403-ing for every current customer.\n\n"
        f"Fix: in the migration that ships the feature, append the key to each "
        f"org's enabled_features and declare it via a module-level "
        f"{_MARKER} list. See b41f7c2e8d05_sales_orders.py for the pattern, "
        f"and remove it again in downgrade()."
    )


def test_backfills_only_name_keys_the_registry_still_defines():
    """A key dropped from the registry but still backfilled means a migration
    is handing out an entitlement nothing gates on any more."""
    unknown = sorted(set(_backfilled_keys()) - set(FEATURES))
    assert not unknown, f"migration(s) backfill unknown feature key(s): {unknown}"


def test_the_marker_is_actually_found():
    """Guards the guard: if the attribute were renamed or the discovery loop
    silently stopped importing anything, both tests above would pass while
    checking nothing at all."""
    granted = _backfilled_keys()
    assert len(granted) >= len(CORE_FEATURES), (
        "no migration declared any granted feature, so the coverage check above "
        "is vacuous - has GRANTS_FEATURES been renamed or the versions path moved?"
    )
