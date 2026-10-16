"""Every environment variable the code reads must be documented in an
.env.example, so a fresh clone can be configured without reading source."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = [REPO / ".env.example", REPO / "backend" / ".env.example"]

# Direct reads outside pydantic Settings.
PY_ENV = re.compile(r"""os\.(?:environ\.get|getenv|environ\.setdefault)\(\s*["']([A-Z][A-Z0-9_]+)["']|os\.environ\[["']([A-Z][A-Z0-9_]+)["']\]""")
JS_ENV = re.compile(r"(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]+)")
# Set by tooling or the runtime, never by an operator.
IGNORED = {"NODE_ENV", "MODE", "DEV", "PROD", "SSR", "BASE_URL"}


def _documented() -> set[str]:
    names: set[str] = set()
    for path in EXAMPLES:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                names.add(line.split("=", 1)[0].strip())
    return names


def _referenced() -> set[str]:
    names = {field.upper() for field in Settings.model_fields}
    backend = REPO / "backend"
    for path in [*(backend / "app").rglob("*.py"), *(backend / "scripts").rglob("*.py"), backend / "dev_server.py"]:
        for match in PY_ENV.finditer(path.read_text(encoding="utf-8")):
            names.add(match.group(1) or match.group(2))
    frontend = REPO / "frontend"
    for path in [*(frontend / "src").rglob("*.ts"), *(frontend / "src").rglob("*.tsx"), frontend / "vite.config.ts"]:
        names.update(JS_ENV.findall(path.read_text(encoding="utf-8")))
    return names - IGNORED


def test_every_referenced_env_var_is_in_an_env_example():
    missing = sorted(_referenced() - _documented())
    assert not missing, f"add to .env.example or backend/.env.example: {missing}"
