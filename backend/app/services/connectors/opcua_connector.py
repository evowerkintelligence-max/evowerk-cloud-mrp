"""OPC-UA connector for PLC/SCADA systems. Uses asyncua (pure Python, no
native libs needed). Reads a set of node values synchronously via asyncio.run().

Only reachable if server_url is a network address this server can reach —
OPC-UA servers usually sit on an isolated OT network. Works if the customer
has exposed/tunneled it (OPC-UA's own secure-channel design makes this more
common than for OPC-DA/PI, but it's still not the default).
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any

try:
    from asyncua import Client as OpcUaClient
    _HAS_ASYNCUA = True
except ImportError:
    _HAS_ASYNCUA = False


class OpcUaConnector:
    """
    Poll OPC-UA nodes and return rows of {node_id, value, timestamp}.

    Config fields:
      server_url   — e.g. "opc.tcp://192.168.1.100:4840"
      node_ids     — comma-separated node IDs, e.g. "ns=2;i=1001,ns=2;i=1002"
      username     — optional
      password     — optional
      poll_rows    — how many snapshots to capture (default 1 = single read)
    """

    def __init__(self, config: dict[str, Any]):
        self.server_url = str(config.get("server_url", "")).strip()
        raw_nodes = str(config.get("node_ids", "")).strip()
        self.node_ids = [n.strip() for n in raw_nodes.split(",") if n.strip()]
        self.username = str(config.get("username", "")).strip() or None
        self.password = str(config.get("password", "")).strip() or None
        self.poll_rows = int(config.get("poll_rows", 1))

        if not self.server_url:
            raise ValueError("server_url is required for OPC-UA connector")
        if not self.node_ids:
            raise ValueError("node_ids is required for OPC-UA connector (comma-separated)")

    def _check_deps(self):
        if not _HAS_ASYNCUA:
            raise RuntimeError("asyncua is required for OPC-UA. Install with: pip install asyncua")

    async def _read_once(self) -> list[dict[str, Any]]:
        ts = datetime.now(timezone.utc).isoformat()
        rows: list[dict[str, Any]] = []

        # Credentials MUST be set before connect(), and we connect exactly once.
        client = OpcUaClient(self.server_url)
        if self.username:
            client.set_user(self.username)
        if self.password:
            client.set_password(self.password)

        await client.connect()
        try:
            for nid in self.node_ids:
                try:
                    node = client.get_node(nid)
                    val = await node.read_value()
                    rows.append({"node_id": nid, "value": val, "timestamp": ts})
                except Exception as e:
                    rows.append({"node_id": nid, "value": None, "error": str(e), "timestamp": ts})
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
        return rows

    def _run(self) -> list[dict[str, Any]]:
        self._check_deps()
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(asyncio.run, self._read_once())
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(self._read_once())
        except Exception:
            return asyncio.run(self._read_once())

    def test(self) -> dict[str, Any]:
        rows = self._run()
        ok_count = sum(1 for r in rows if r.get("value") is not None)
        return {"ok": ok_count > 0, "message": f"Read {ok_count}/{len(rows)} nodes from {self.server_url}"}

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        return self._run()[:n]

    def fetch_all(self, max_rows: int = 500_000) -> list[dict[str, Any]]:
        all_rows: list[dict] = []
        for _ in range(min(self.poll_rows, max_rows)):
            all_rows.extend(self._run())
        return all_rows[:max_rows]
