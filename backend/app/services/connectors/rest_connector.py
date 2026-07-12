"""Generic REST API connector — covers:
  SAP OData (type: sap_odata)
  ERP REST endpoints (type: erp_rest)
  MES REST endpoints (type: mes_rest)

Fetches JSON from a URL and extracts a list of records via a JSONPath-style key.
The one connector type that works unmodified from a cloud server: outbound
HTTPS to a public vendor API needs no on-prem network reachability.
"""
from __future__ import annotations
import json
from typing import Any


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Shallow-flatten a nested dict one level deep for CSV-friendliness."""
    out: dict[str, Any] = {}
    if not isinstance(obj, dict):
        return {prefix.rstrip("."): obj}
    for k, v in obj.items():
        key = f"{prefix}{k}" if prefix else k
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out[f"{key}.{k2}"] = v2
        else:
            out[key] = v
    return out


class RestConnector:
    """
    Fetch records from a REST JSON endpoint.

    Config fields:
      base_url       — e.g. "https://my-erp.example.com"
      endpoint       — e.g. "/api/v2/production_orders"
                       For SAP OData: "/sap/opu/odata/sap/SERVICE/EntitySet"
      method         — GET (default) or POST
      auth_type      — "none" | "basic" | "bearer" | "api_key"
      auth_value     — for basic: "user:password"; bearer: token; api_key: key value
      api_key_header — header name for api_key auth (default: "X-Api-Key")
      response_path  — dot-separated key path into the JSON to find the records array
                       e.g. "value" (SAP OData), "data.items", "" (root is array)
      extra_headers  — JSON string dict of extra headers
      params         — JSON string dict of query params (appended to URL)
      max_rows       — hard cap on records to pull (default 10000)
    """

    def __init__(self, config: dict[str, Any]):
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.endpoint = str(config.get("endpoint", "")).strip()
        self.method = str(config.get("method", "GET")).upper()
        self.auth_type = str(config.get("auth_type", "none")).lower()
        self.auth_value = str(config.get("auth_value", "")).strip()
        self.api_key_header = str(config.get("api_key_header", "X-Api-Key")).strip()
        self.response_path = str(config.get("response_path", "value")).strip()
        self.max_rows = int(config.get("max_rows", 10_000))

        # ─── Pagination ───────────────────────────────────────────────────────
        # "auto"  : follow OData @odata.nextLink across pages (server-driven). If
        #           there is no nextLink, behaves like a single request.
        # "offset": client-driven ?$skip=&$top= (configurable param names).
        # "page"  : client-driven ?page=&pageSize= (configurable param names).
        # "none"  : single request only (legacy behavior).
        self.pagination = (str(config.get("pagination", "auto")).strip().lower() or "auto")
        self.page_size = int(config.get("page_size", 0) or 0)
        self.offset_param = str(config.get("offset_param", "$skip")).strip() or "$skip"
        self.limit_param = str(config.get("limit_param", "$top")).strip() or "$top"
        self.page_param = str(config.get("page_param", "page")).strip() or "page"
        self.page_size_param = str(config.get("page_size_param", "pageSize")).strip() or "pageSize"
        self.start_page = int(config.get("start_page", 1) or 1)
        self.max_pages = int(config.get("max_pages", 10_000) or 10_000)  # hard loop guard

        try:
            self.extra_headers: dict[str, str] = json.loads(config.get("extra_headers", "{}") or "{}")
        except Exception:
            self.extra_headers = {}

        try:
            self.params: dict[str, str] = json.loads(config.get("params", "{}") or "{}")
        except Exception:
            self.params = {}

        if not self.base_url:
            raise ValueError("base_url is required for REST connector")
        if not self.endpoint:
            raise ValueError("endpoint is required for REST connector")

        # The backend fetches this URL server-side — restrict to http(s) so a
        # config can't point the server at file://, ftp://, etc.
        from urllib.parse import urlparse
        scheme = (urlparse(self.base_url).scheme or "").lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"base_url must use http or https (got '{scheme or 'no scheme'}')")

    def _build_url(self, extra: dict[str, Any] | None = None) -> str:
        from urllib.parse import urlencode
        url = self.base_url + self.endpoint
        params = dict(self.params)
        if extra:
            params.update({k: str(v) for k, v in extra.items()})
        if params:
            url += "?" + urlencode(params)
        return url

    def _build_request(self, url: str):
        import urllib.request
        import base64
        req = urllib.request.Request(url, method=self.method)
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")

        for k, v in self.extra_headers.items():
            req.add_header(k, v)

        if self.auth_type == "basic":
            parts = self.auth_value.split(":", 1)
            user = parts[0]
            pwd = parts[1] if len(parts) > 1 else ""
            token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        elif self.auth_type == "bearer":
            req.add_header("Authorization", f"Bearer {self.auth_value}")
        elif self.auth_type == "api_key":
            req.add_header(self.api_key_header, self.auth_value)

        return req

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        """Traverse response_path keys to find the records array."""
        if not self.response_path:
            data = payload
        else:
            data = payload
            for key in self.response_path.split("."):
                if isinstance(data, dict):
                    data = data.get(key, [])
                else:
                    data = []

        if not isinstance(data, list):
            data = [data] if data else []

        return [_flatten(r) if isinstance(r, dict) else {"value": r} for r in data]

    def _fetch_payload(self, url: str) -> Any:
        import urllib.request
        import urllib.error
        req = self._build_request(url)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {body[:300]}")
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")

        try:
            return json.loads(body)
        except Exception:
            raise RuntimeError(f"Response is not valid JSON: {body[:200]}")

    @staticmethod
    def _next_link(payload: Any) -> str | None:
        """OData server-driven paging marker (case-insensitive variants)."""
        if isinstance(payload, dict):
            for k in ("@odata.nextLink", "@odata.nextlink", "odata.nextLink", "nextLink", "next"):
                v = payload.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return None

    def iter_rows(self, max_rows: int = 0):
        """Stream records across pages (constant memory at the connector level)."""
        from urllib.parse import urljoin

        mode = self.pagination or "auto"
        ps = self.page_size if self.page_size and self.page_size > 0 else None
        yielded = 0

        # ── client-driven offset / page modes ──────────────────────────────────
        if mode in ("offset", "page"):
            skip = 0
            page_idx = self.start_page
            for _ in range(self.max_pages):
                if mode == "offset":
                    extra: dict[str, Any] = {self.offset_param: skip}
                    if ps:
                        extra[self.limit_param] = ps
                else:
                    extra = {self.page_param: page_idx}
                    if ps:
                        extra[self.page_size_param] = ps

                recs = self._extract_records(self._fetch_payload(self._build_url(extra)))
                if not recs:
                    break
                for r in recs:
                    yield r
                    yielded += 1
                    if max_rows and yielded >= max_rows:
                        return
                if ps is None or len(recs) < ps:  # short page → last page
                    break
                skip += len(recs)
                page_idx += 1
            return

        # ── auto / odata (server-driven nextLink) / none ───────────────────────
        first_extra: dict[str, Any] = {}
        if ps:
            first_extra[self.limit_param] = ps
        url = self._build_url(first_extra)
        seen: set = set()
        for _ in range(self.max_pages):
            payload = self._fetch_payload(url)
            for r in self._extract_records(payload):
                yield r
                yielded += 1
                if max_rows and yielded >= max_rows:
                    return
            if mode == "none":
                break
            nl = self._next_link(payload)
            if not nl:
                break
            # Resolve relative nextLink against the URL we just fetched (per OData spec).
            url = nl if nl.lower().startswith("http") else urljoin(url, nl)
            if url in seen:  # loop guard
                break
            seen.add(url)

    def test(self) -> dict[str, Any]:
        records = list(self.iter_rows(max_rows=1))
        return {"ok": True, "message": f"Connected — got {len(records)} sample record(s)"}

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        return list(self.iter_rows(max_rows=n))[:n]

    def fetch_all(self, max_rows: int = 500_000) -> list[dict[str, Any]]:
        cap = min(self.max_rows, max_rows)
        return list(self.iter_rows(max_rows=cap))
