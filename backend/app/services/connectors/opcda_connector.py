"""OPC-DA connector for legacy PLCs / SCADA systems (pre-UA standard).
Requires Windows + OPC Core Components + one of:
  pip install OpenOPC-Python3x   (preferred, wraps COM via Graybox OPC DA Auto)
  pip install pywin32            (fallback, raw COM)

OPC-DA is COM-only and only runs on Windows — this codebase's Docker image is
Linux, so this connector will always raise until run on a Windows host (kept
for parity with the desktop app / a future Windows-hosted deployment, not
because it's reachable from this backend today).
"""
from __future__ import annotations

import importlib.util
import platform
import time
from typing import Any

_HAS_OPENOPC = False

try:
    import OpenOPC  # type: ignore
    _HAS_OPENOPC = True
except ImportError:
    pass

# pywin32 (fallback raw-COM path, unused by this module directly - OpenOPC is
# the only client actually invoked below) - just detected, not imported, to
# avoid pulling in an unused Windows-only module. find_spec() on a submodule
# raises ModuleNotFoundError (rather than returning None) if the parent
# package itself isn't installed, so the parent is checked first.
_HAS_WIN32 = importlib.util.find_spec("win32com") is not None and importlib.util.find_spec("win32com.client") is not None


def _check_platform():
    if platform.system() != "Windows":
        raise RuntimeError("OPC-DA is a Windows-only protocol (COM-based). Run on Windows.")


class OpcDaConnector:
    """
    Pull tag values from an OPC-DA server (classic OPC, pre-UA).

    Config keys:
      server      — OPC server ProgID, e.g. "Kepware.KEPServerEX.V6"
                    or "Matrikon.OPC.Simulation.1"
      host        — hostname/IP of OPC server machine (default: localhost)
      tags        — comma-separated tag paths, e.g. "Channel1.Device1.Tag1,Channel1.Device1.Tag2"
                    OR "*" to browse all available tags (may be slow on large servers)
      history_minutes — how many minutes of history to pull (default: 60)
                    Note: requires OPC-HDA capable server; DA-only servers return current values only
      poll_count  — for DA-only: number of poll snapshots to take (default: 1)
      poll_interval_s — seconds between polls when poll_count > 1 (default: 5)
    """

    def __init__(self, config: dict[str, Any]):
        _check_platform()
        self.server = str(config.get("server", "")).strip()
        self.host = str(config.get("host", "localhost")).strip() or "localhost"
        raw_tags = str(config.get("tags", "")).strip()
        self.tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags and raw_tags != "*" else []
        self.browse_all = (raw_tags == "*" or not self.tags)
        self.history_minutes = int(config.get("history_minutes", 60))
        self.poll_count = max(1, int(config.get("poll_count", 1)))
        self.poll_interval_s = max(1, int(config.get("poll_interval_s", 5)))

        if not self.server:
            raise ValueError("server (OPC ProgID) is required for OPC-DA connector")

    def _require_openopc(self):
        if not _HAS_OPENOPC:
            raise RuntimeError(
                "OpenOPC-Python3x is required for OPC-DA.\n"
                "Install: pip install OpenOPC-Python3x\n"
                "Also install Graybox OPC DA Auto Wrapper from https://gray-box.net/daawrapper.php"
            )

    def _connect(self):
        self._require_openopc()
        try:
            opc = OpenOPC.client()
            opc.connect(self.server, self.host)
            return opc
        except Exception as e:
            raise RuntimeError(
                f"Could not connect to OPC-DA server '{self.server}' on host '{self.host}': {e}. "
                "OPC-DA is Windows/COM-only and requires the Graybox OPC DA Auto Wrapper installed "
                "on this machine (https://gray-box.net/daawrapper.php). Also verify the OPC server "
                "is running and DCOM permissions allow access."
            )

    def test(self) -> dict[str, Any]:
        self._require_openopc()
        opc = self._connect()
        try:
            servers = opc.servers(self.host)
            opc.close()
            return {
                "ok": True,
                "message": f"Connected to OPC-DA server '{self.server}' on {self.host}. "
                           f"Available servers: {', '.join(servers[:5])}",
            }
        except Exception:
            try:
                opc.close()
            except Exception:
                pass
            raise

    def _browse_tags(self, opc) -> list[str]:
        """Recursively browse all leaf tags on the server."""
        try:
            return [item for item in opc.list("*", recursive=True)]
        except Exception:
            return opc.list()

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        opc = self._connect()
        try:
            tags = self.tags if not self.browse_all else self._browse_tags(opc)[:n]
            tags = tags[:n]
            rows = []
            for tag, value, quality, timestamp in opc.read(tags, group="preview"):
                rows.append({"tag": tag, "value": value, "quality": quality, "timestamp": str(timestamp)})
            return rows
        finally:
            opc.close()

    def fetch_all(self, max_rows: int = 500_000) -> list[dict[str, Any]]:
        """
        Poll the OPC-DA server poll_count times, returning one row per (tag, poll).
        For current-value snapshots set poll_count=1.
        For a mini time-series set poll_count > 1 with poll_interval_s.
        """
        opc = self._connect()
        try:
            tags = self.tags if not self.browse_all else self._browse_tags(opc)
            rows: list[dict[str, Any]] = []

            for poll_idx in range(self.poll_count):
                if poll_idx > 0:
                    time.sleep(self.poll_interval_s)

                read_results = opc.read(tags, group=f"poll_{poll_idx}")
                for tag, value, quality, timestamp in read_results:
                    rows.append({
                        "poll": poll_idx + 1, "tag": tag, "value": value,
                        "quality": quality, "timestamp": str(timestamp),
                    })
                    if len(rows) >= max_rows:
                        return rows

            return rows
        finally:
            opc.close()
