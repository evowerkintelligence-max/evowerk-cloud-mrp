"""Read an Excel (.xlsx / .xls) file via openpyxl. `file_path` is resolved
server-side by app.services.integrations, same as csv_connector.py."""
from __future__ import annotations
from pathlib import Path
from typing import Any

try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False


class ExcelConnector:
    def __init__(self, config: dict[str, Any]):
        self.file_path = str(config.get("file_path", "")).strip()
        self.sheet_name: str | None = config.get("sheet_name") or None
        self.header_row: int = int(config.get("header_row", 1))  # 1-indexed
        if not self.file_path:
            raise ValueError("file_path is required for Excel connector")

    def _check(self):
        if not _HAS_OPENPYXL:
            raise RuntimeError("openpyxl is required. Install with: pip install openpyxl")
        p = Path(self.file_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")
        return p

    def _read_rows(self, max_rows: int | None = None) -> list[dict[str, Any]]:
        p = self._check()
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        ws = wb[self.sheet_name] if self.sheet_name else wb.active
        rows_iter = ws.iter_rows(values_only=True)

        headers = None
        for i, row in enumerate(rows_iter, 1):
            if i == self.header_row:
                headers = [str(c) if c is not None else f"col_{i}" for i, c in enumerate(row)]
                break
        if headers is None:
            wb.close()
            return []

        records: list[dict[str, Any]] = []
        for row in rows_iter:
            record = {}
            for col, val in zip(headers, row):
                record[col] = val
            records.append(record)
            if max_rows and len(records) >= max_rows:
                break

        wb.close()
        return records

    def test(self) -> dict[str, Any]:
        p = self._check()
        wb = openpyxl.load_workbook(str(p), read_only=True)
        sheets = wb.sheetnames
        wb.close()
        return {"ok": True, "message": f"Excel file opened. Sheets: {', '.join(sheets)}"}

    def iter_rows(self, max_rows: int = 0):
        """Stream rows one at a time via openpyxl read-only (constant memory)."""
        p = self._check()
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        try:
            ws = wb[self.sheet_name] if self.sheet_name else wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = None
            for i, row in enumerate(rows_iter, 1):
                if i == self.header_row:
                    headers = [str(c) if c is not None else f"col_{j}" for j, c in enumerate(row)]
                    break
            if headers is None:
                return
            n = 0
            for row in rows_iter:
                yield {col: val for col, val in zip(headers, row)}
                n += 1
                if max_rows and n >= max_rows:
                    break
        finally:
            wb.close()

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        return self._read_rows(max_rows=n)

    def fetch_all(self, max_rows: int = 0) -> list[dict[str, Any]]:
        return list(self.iter_rows(max_rows=max_rows))

    @property
    def columns(self) -> list[str]:
        rows = self._read_rows(max_rows=1)
        return list(rows[0].keys()) if rows else []
