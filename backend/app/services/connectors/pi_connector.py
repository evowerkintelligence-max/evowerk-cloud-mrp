"""OSIsoft PI Web API connector — pulls tag data from a PI historian via REST.
Requires PI Web API 2016+ running on the PI server. No extra Python packages
needed (uses stdlib urllib).

PI Web API docs: https://docs.osisoft.com/bundle/pi-web-api-reference/

Only reachable if base_url is a network address this server can reach — PI
servers are typically inside the plant's OT network. Works if the customer
has exposed PI Web API to this server (firewall allow-list, VPN, or reverse
proxy), same caveat as the SQL Server/Oracle connectors.

Config keys:
  base_url     — PI Web API root, e.g. "https://piserver.plant.local/piwebapi"
  username     — Windows/PI identity username
  password     — password
  verify_ssl   — false to skip self-signed cert check (common on plant networks)
  tags         — comma-separated PI tag names, e.g. "sinusoid,CDT158,SINUS60H"
  data_server  — PI Data Server name (default: auto-detect first available)
  start_time   — ISO or PI relative time, e.g. "*-7d" or "2024-01-01T00:00:00"
  end_time     — ISO or PI relative time, e.g. "*" (now)
  max_count    — max recorded values per tag (default: 10000)
  interval     — if set, use interpolated instead of recorded values, e.g. "1h", "15m"
"""
from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from typing import Any

# PI Web API caps values per request (~150k server hard-limit). We page under it.
_PI_PAGE_SIZE = 150_000
_PI_MAX_PAGES = 10_000  # safety guard against runaway loops


def _basic_auth(username: str, password: str) -> str:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {creds}"


class PiConnector:
    """Pull recorded or interpolated values from OSIsoft PI via PI Web API."""

    def __init__(self, config: dict[str, Any]):
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.username = str(config.get("username", "")).strip()
        self.password = str(config.get("password", "")).strip()
        self.verify_ssl = bool(config.get("verify_ssl", True))
        raw_tags = str(config.get("tags", "")).strip()
        self.tag_names = [t.strip() for t in raw_tags.split(",") if t.strip()]
        self.data_server = str(config.get("data_server", "")).strip()
        self.start_time = str(config.get("start_time", "*-1d")).strip() or "*-1d"
        self.end_time = str(config.get("end_time", "*")).strip() or "*"
        self.max_count = int(config.get("max_count", 10_000))
        self.interval = str(config.get("interval", "")).strip()

        if not self.base_url:
            raise ValueError("base_url is required for PI Web API connector (e.g. https://piserver/piwebapi)")
        if not self.tag_names:
            raise ValueError("tags is required — comma-separated PI tag names")

    def _ssl_ctx(self) -> ssl.SSLContext | None:
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    def _get(self, url: str) -> Any:
        req = urllib.request.Request(url)
        req.add_header("Authorization", _basic_auth(self.username, self.password))
        req.add_header("Accept", "application/json")
        ctx = self._ssl_ctx()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"PI Web API HTTP {e.code}: {body[:300]}") from e

    def _get_data_server_webid(self) -> str:
        """Return WebId of the target data server."""
        data = self._get(f"{self.base_url}/dataservers")
        items = data.get("Items", [])
        if not items:
            raise RuntimeError("No PI Data Servers found at this PI Web API endpoint")
        if self.data_server:
            for item in items:
                if item.get("Name", "").lower() == self.data_server.lower():
                    return item["WebId"]
            raise RuntimeError(f"Data server '{self.data_server}' not found. Available: {[i['Name'] for i in items]}")
        return items[0]["WebId"]

    def _get_point_webid(self, ds_webid: str, tag_name: str) -> str | None:
        """Resolve a tag name to its WebId."""
        url = f"{self.base_url}/dataservers/{ds_webid}/points?nameFilter={urllib.request.quote(tag_name)}&maxCount=1"
        data = self._get(url)
        items = data.get("Items", [])
        if not items:
            return None
        return items[0]["WebId"]

    def _fetch_one_page(self, webid: str, tag_name: str, start_time: str, page_size: int):
        """Fetch a single page of values starting at start_time. Returns (rows, raw_count)."""
        q = urllib.request.quote
        if self.interval:
            url = (
                f"{self.base_url}/streams/{webid}/interpolated"
                f"?startTime={q(start_time)}&endTime={q(self.end_time)}"
                f"&interval={q(self.interval)}&maxCount={int(page_size)}"
            )
        else:
            url = (
                f"{self.base_url}/streams/{webid}/recorded"
                f"?startTime={q(start_time)}&endTime={q(self.end_time)}&maxCount={int(page_size)}"
            )
        data = self._get(url)
        items = data.get("Items", []) or []
        rows = []
        for item in items:
            rows.append({
                "tag": tag_name,
                "timestamp": item.get("Timestamp", ""),
                "value": item.get("Value", {}).get("Value", item.get("Value")) if isinstance(item.get("Value"), dict) else item.get("Value"),
                "quality": item.get("Value", {}).get("Name", "Good") if isinstance(item.get("Value"), dict) else "Good",
                "units": item.get("UnitsAbbreviation", ""),
            })
        return rows, len(items)

    def _fetch_tag_values(self, webid: str, tag_name: str) -> list[dict[str, Any]]:
        """
        Fetch values for a tag with TIME-BASED CONTINUATION paging: PI Web API caps
        each request at maxCount (server hard-limit ~150k), so this pages by
        advancing startTime to the last timestamp seen (de-duping the boundary
        value) until fewer than a full page comes back or self.max_count (the
        per-tag total cap) is reached.
        """
        total_cap = int(self.max_count) if self.max_count else _PI_PAGE_SIZE
        page_size = min(total_cap, _PI_PAGE_SIZE)
        if page_size <= 0:
            page_size = _PI_PAGE_SIZE

        rows: list[dict[str, Any]] = []
        start = self.start_time
        last_ts = None
        for _ in range(_PI_MAX_PAGES):
            page, raw = self._fetch_one_page(webid, tag_name, start, page_size)
            if last_ts is not None:
                # Drop boundary duplicates (the recorded endpoint includes start_time).
                i = 0
                while i < len(page) and page[i].get("timestamp") == last_ts:
                    i += 1
                page = page[i:]
            if not page:
                break
            rows.extend(page)
            last_ts = page[-1].get("timestamp")
            if len(rows) >= total_cap:
                rows = rows[:total_cap]
                break
            if raw < page_size:  # fewer than a full page → no more data
                break
            if not last_ts:
                break
            start = last_ts  # continue from the boundary timestamp
        return rows

    def test(self) -> dict[str, Any]:
        data = self._get(f"{self.base_url}/system/versions")
        version = data.get("PIWebAPI", {}).get("Version", "unknown")
        ds_webid = self._get_data_server_webid()
        ds_data = self._get(f"{self.base_url}/dataservers/{ds_webid}")
        return {
            "ok": True,
            "message": f"Connected to PI Web API v{version}. Data server: {ds_data.get('Name', 'unknown')}",
        }

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        ds_webid = self._get_data_server_webid()
        rows: list[dict[str, Any]] = []
        for tag in self.tag_names:
            webid = self._get_point_webid(ds_webid, tag)
            if not webid:
                rows.append({"tag": tag, "error": "Tag not found"})
                continue
            tag_rows = self._fetch_tag_values(webid, tag)
            rows.extend(tag_rows[:max(1, n // len(self.tag_names))])
            if len(rows) >= n:
                break
        return rows[:n]

    def fetch_all(self, max_rows: int = 500_000) -> list[dict[str, Any]]:
        ds_webid = self._get_data_server_webid()
        rows: list[dict[str, Any]] = []
        errors: list[str] = []

        for tag in self.tag_names:
            webid = self._get_point_webid(ds_webid, tag)
            if not webid:
                errors.append(f"Tag not found: {tag}")
                continue
            tag_rows = self._fetch_tag_values(webid, tag)
            rows.extend(tag_rows)
            if len(rows) >= max_rows:
                rows = rows[:max_rows]
                break

        if errors and not rows:
            raise RuntimeError("No tags resolved. Errors: " + "; ".join(errors))

        try:
            rows.sort(key=lambda r: r.get("timestamp", ""))
        except Exception:
            pass

        return rows
