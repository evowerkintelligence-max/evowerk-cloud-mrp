"""Data integration hub — CRUD orchestration for Integration configs plus
test/preview/sync, ported from the desktop app's routes/integrations.py.

Two adaptations for a multi-tenant cloud deployment (the desktop app is
single-tenant with local file access and stored secrets "as-is"):
  - Secret config fields (password, auth_value) are Fernet-encrypted at rest
    via app.core.security, not stored plaintext.
  - CSV/Excel connectors read a server-resolved file path instead of an
    arbitrary client `file_path` — see save_integration_source_file, used by
    the /integrations/{id}/upload route.
"""
from __future__ import annotations

import asyncio
import csv
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models.dataset import Dataset
from app.models.integration import Integration
from app.services import datasets as ds_service
from app.services.connectors import get_connector

SECRET_KEYS = ("password", "auth_value")
SECRET_MASK = "•" * 8


# ---------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------

def merge_secrets(new_cfg: dict[str, Any], old_cfg: dict[str, Any]) -> dict[str, Any]:
    """Encrypt incoming secret fields; preserve the existing (already-encrypted)
    value when the incoming value is absent or is just the mask placeholder, so
    editing any other field doesn't silently wipe the stored credential."""
    merged = dict(new_cfg or {})
    old_cfg = old_cfg or {}
    for key in SECRET_KEYS:
        incoming = merged.get(key)
        if incoming in (None, "", SECRET_MASK):
            existing = old_cfg.get(key)
            if existing:
                merged[key] = existing
            else:
                merged.pop(key, None)
        else:
            merged[key] = encrypt_secret(str(incoming))
    return merged


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    out = dict(config or {})
    for key in SECRET_KEYS:
        if out.get(key):
            out[key] = SECRET_MASK
    return out


def decrypt_config(config: dict[str, Any]) -> dict[str, Any]:
    out = dict(config or {})
    for key in SECRET_KEYS:
        v = out.get(key)
        if v:
            try:
                out[key] = decrypt_secret(str(v))
            except Exception:
                pass  # tolerate a value that was never encrypted (shouldn't happen post-merge_secrets)
    return out


# ---------------------------------------------------------------------
# Uploaded source file storage (CSV/Excel connectors)
# ---------------------------------------------------------------------

def _integration_source_root(org_id: uuid.UUID) -> Path:
    settings = get_settings()
    root = Path(settings.data_dir)
    root = root if root.is_absolute() else (Path.cwd() / root).resolve()
    d = root / "integration_sources" / str(org_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def integration_source_path(org_id: uuid.UUID, integration_id: uuid.UUID, ext: str) -> Path:
    return _integration_source_root(org_id) / f"{integration_id}.{ext.lstrip('.')}"


def save_integration_source_file(org_id: uuid.UUID, integration_id: uuid.UUID, ext: str, data: bytes) -> Path:
    path = integration_source_path(org_id, integration_id, ext)
    path.write_bytes(data)
    return path


def delete_integration_source_file(org_id: uuid.UUID, integration_id: uuid.UUID, ext: str) -> None:
    try:
        integration_source_path(org_id, integration_id, ext).unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------
# Connector invocation
# ---------------------------------------------------------------------

async def _run_connector_method(item: Integration, method: str, /, **kwargs: Any) -> Any:
    """Every connector method is blocking I/O (DB drivers, urllib, MQTT/OPC
    clients) - run it off the event loop the same way app.services.sql_query
    runs blocking DuckDB calls."""
    config = decrypt_config(item.config)
    connector = get_connector(item.type, config)  # may raise ValueError on bad config - not I/O, run inline
    fn = getattr(connector, method)
    return await asyncio.to_thread(fn, **kwargs)


async def test_integration(item: Integration) -> dict[str, Any]:
    try:
        result = await _run_connector_method(item, "test")
        return {"ok": bool(result.get("ok")), "message": str(result.get("message", ""))}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def preview_integration(item: Integration, n: int) -> dict[str, Any]:
    try:
        rows = await _run_connector_method(item, "preview", n=min(max(n, 1), 50))
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    columns = list(rows[0].keys()) if rows else []
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def _rows_to_csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Column union across all rows (heterogeneous sources), matching the
    desktop's _stream_rows_to_csv sniffing behavior at import-into-memory
    scale - app.services.datasets.create_dataset already loads the full
    upload into memory, so there's no separate streaming-to-disk step here."""
    cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


async def sync_integration(
    db: AsyncSession,
    item: Integration,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    dataset_name: str | None,
    max_rows: int,
) -> dict[str, Any]:
    try:
        rows = await _run_connector_method(item, "fetch_all", max_rows=max_rows)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Fetch failed: {e}") from e

    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No data returned from source")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = dataset_name or f"{item.name} — {ts[:8]}"
    csv_bytes = _rows_to_csv_bytes(rows)

    dataset: Dataset = await ds_service.create_dataset(
        db, org_id=org_id, created_by=created_by, name=name, filename=f"{name}.csv", data=csv_bytes
    )

    return {"rows_synced": len(rows), "dataset": {"id": str(dataset.id), "name": dataset.name}}
