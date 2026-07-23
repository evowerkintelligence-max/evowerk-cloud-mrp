from __future__ import annotations

import re
from pathlib import Path

from app.core.features import FEATURES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TS_FILE = _REPO_ROOT / "frontend" / "src" / "lib" / "features.ts"

# Matches `key: { label: "...", tier: "core" | "premium" },` lines - good enough
# to catch drift between the two registries without a real TS parser.
_ENTRY_RE = re.compile(r'^\s*(\w+):\s*\{\s*label:\s*"[^"]*",\s*tier:\s*"(core|premium)"\s*\}', re.MULTILINE)


def test_frontend_registry_matches_backend_registry():
    ts_source = _TS_FILE.read_text(encoding="utf-8")
    ts_entries = {key: tier for key, tier in _ENTRY_RE.findall(ts_source)}
    py_entries = {key: tier for key, (_, tier) in FEATURES.items()}
    assert ts_entries == py_entries
