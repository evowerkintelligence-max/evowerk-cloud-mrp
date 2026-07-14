"""Read a CSV/TSV file. `file_path` is resolved server-side by
app.services.integrations (from the uploaded file stored at integration
creation time) — never client-supplied, so this stays traversal-safe."""
from __future__ import annotations
import csv
from pathlib import Path
from typing import Any


class CsvConnector:
    def __init__(self, config: dict[str, Any]):
        self.file_path = str(config.get("file_path", "")).strip()
        self.delimiter = str(config.get("delimiter", ","))
        if not self.file_path:
            raise ValueError("file_path is required for CSV connector")

    def _path(self) -> Path:
        p = Path(self.file_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")
        return p

    def test(self) -> dict[str, Any]:
        p = self._path()
        return {"ok": True, "message": f"File found ({p.stat().st_size:,} bytes)"}

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        p = self._path()
        delim = "\t" if p.suffix.lower() == ".tsv" else self.delimiter
        rows = []
        with p.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter=delim)
            for row in reader:
                rows.append(dict(row))
                if len(rows) >= n:
                    break
        return rows

    def iter_rows(self, max_rows: int = 0):
        """Stream rows one at a time (constant memory) for large files."""
        p = self._path()
        delim = "\t" if p.suffix.lower() == ".tsv" else self.delimiter
        with p.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter=delim)
            n = 0
            for row in reader:
                yield dict(row)
                n += 1
                if max_rows and n >= max_rows:
                    break

    def fetch_all(self, max_rows: int = 0) -> list[dict[str, Any]]:
        return list(self.iter_rows(max_rows=max_rows))

    @property
    def columns(self) -> list[str]:
        p = self._path()
        with p.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            return list(reader.fieldnames or [])
