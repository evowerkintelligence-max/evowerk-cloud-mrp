"""Live-polling collector engine, ported from the desktop app's
realtime/engine.py. A "stream" polls one Integration's connector on an
interval, rolls results into a bounded in-memory buffer, computes derived
manufacturing metrics (throughput/scrap/OEE/downtime) over that buffer, and
persists each tick to a JSONL history file.

Kept as an in-process background-thread registry, same as the desktop
version (the connectors are all blocking sync I/O, so a plain thread is the
right primitive - matches app.services.integrations' asyncio.to_thread usage
for the same reason). What's different for a server that can restart:
RealtimeStream (app.models.realtime) persists each stream's definition and
`desired_status`; resume_all_streams() is called from app.main's startup
hook to re-spin any stream still marked "running" after a restart, so a
redeploy doesn't silently and permanently lose it. The live snapshot/status
this module tracks is still ephemeral and only reflects this process.
"""
from __future__ import annotations

import json
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeStream
from app.services.connectors import get_connector
from app.services.integrations import decrypt_config


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _history_dir() -> Path:
    settings = get_settings()
    root = Path(settings.data_dir)
    root = root if root.is_absolute() else (Path.cwd() / root).resolve()
    d = root / "realtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def history_path(stream_id: str) -> Path:
    # stream_id is a RealtimeStream UUID - safe as a bare filename.
    return _history_dir() / f"{stream_id}.jsonl"


def read_history(stream_id: str, max_points: int = 2000) -> list[dict[str, Any]]:
    """Bounded read of a stream's persisted tick history — survives a backend
    restart, unlike the in-memory deque. Returns the most recent `max_points`
    ticks (oldest first)."""
    path = history_path(stream_id)
    if not path.exists():
        return []
    max_points = max(1, min(20_000, int(max_points)))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-max_points:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def delete_history(stream_id: str) -> None:
    try:
        history_path(stream_id).unlink()
    except FileNotFoundError:
        pass


class StreamRunner:
    def __init__(
        self,
        stream_id: str,
        name: str,
        integration_type: str,
        integration_config: dict[str, Any],
        column_map: dict[str, str],
        interval_seconds: int,
        accumulate: bool,
        max_rows: int = 10_000,
    ):
        self.stream_id = stream_id
        self.name = name
        self.integration_type = integration_type
        self.integration_config = integration_config
        self.column_map = column_map
        self.interval_seconds = max(1, interval_seconds)
        self.accumulate = accumulate
        self.status = "running"
        self.error: str | None = None
        self.rows_collected = 0
        self.started_at = datetime.now(timezone.utc)
        self.last_tick: datetime | None = None

        self._buffer: deque = deque(maxlen=max_rows)
        self._lock = Lock()
        # Externally-computed metrics (currently: app.services.automl_realtime's
        # live risk score) merged into snapshot()'s own derived OEE/scrap metrics.
        # A plain dict behind the same lock as the buffer - this is the "small
        # extension point" the manufacturing-AutoML spec calls for, kept minimal
        # on purpose: no pub/sub, no per-key TTL, just "last value wins".
        self._external_metrics: dict[str, Any] = {}
        self._history_path = history_path(stream_id)
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True, name=f"stream-{stream_id[:8]}")
        self._thread.start()

    def _run(self) -> None:
        try:
            conn = get_connector(self.integration_type, self.integration_config)
        except Exception as exc:
            self.status = "error"
            self.error = f"Connector init failed: {exc}"
            return

        while not self._stop.is_set():
            try:
                rows: list[dict] = conn.fetch_all()
                if rows:
                    with self._lock:
                        if not self.accumulate:
                            self._buffer.clear()
                        # cap per-fetch append to avoid millions of rows from large SQL pulls
                        self._buffer.extend(rows[-1_000:])
                        self.rows_collected += len(rows)
                    self.last_tick = datetime.now(timezone.utc)
                    self._append_history()
                self.error = None
                if self.status == "error":
                    self.status = "running"
            except Exception as exc:
                self.error = str(exc)
                self.status = "error"

            # MQTT fetch_all already blocks for collect_seconds; others need explicit wait.
            if self.integration_type != "mqtt":
                self._stop.wait(timeout=self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def set_external_metric(self, name: str, value: Any) -> None:
        """Inject a named value into the metrics dict every snapshot() call
        returns from now on, alongside this runner's own derived OEE/scrap
        metrics - e.g. app.services.automl_realtime publishing a live risk
        score so the existing stream_metric alert path (eval_stream_metric)
        can alert on it without knowing anything about AutoML."""
        with self._lock:
            self._external_metrics[name] = value

    def recent_rows(self, n: int = 500) -> list[dict[str, Any]]:
        """Last `n` raw rows from the in-memory buffer (not the derived
        metrics), for callers that need actual feature values - e.g.
        app.services.automl_realtime building the row(s) a trained pipeline
        expects. Unlike read_history(), which only persists this runner's
        derived metrics per tick, this reflects the real ingested columns."""
        with self._lock:
            rows = list(self._buffer)
        return rows[-max(1, int(n)) :]

    def _append_history(self) -> None:
        # History persistence is a nice-to-have for replay/restart-survival, not
        # the collector's core job — a disk hiccup here must never take down the
        # background thread that's actually pulling live data.
        try:
            snap = self.snapshot()
            with self._history_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": snap["ts"], "metrics": snap["metrics"]}) + "\n")
        except Exception:
            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rows = list(self._buffer)
            external_metrics = dict(self._external_metrics)

        now = datetime.now(timezone.utc)
        age_s = (now - self.started_at).total_seconds()
        age_min = age_s / 60

        metrics: dict[str, Any] = {}
        cm = self.column_map

        uc = cm.get("units_col")
        if uc:
            vals = [_safe_float(r.get(uc, 0)) for r in rows]
            total_units = sum(vals)
            metrics["throughput"] = round(total_units, 1)
            if age_min > 0:
                metrics["throughput_rate"] = round(total_units / age_min, 2)

        sc = cm.get("scrap_col")
        tuc = cm.get("total_units_col") or uc
        if sc and tuc:
            total = sum(_safe_float(r.get(tuc, 0)) for r in rows)
            scrap = sum(_safe_float(r.get(sc, 0)) for r in rows)
            if total > 0:
                scrap_pct = round(scrap / total * 100, 2)
                metrics["scrap_rate_pct"] = scrap_pct
                metrics["fpy_pct"] = round(100 - scrap_pct, 2)

        dc = cm.get("downtime_col")
        if dc:
            metrics["total_downtime_min"] = round(sum(_safe_float(r.get(dc, 0)) for r in rows), 1)

        pt = cm.get("planned_time_col")
        ot = cm.get("operating_time_col")
        guc = cm.get("good_units_col")
        tpc = cm.get("target_rate_col")
        if pt and ot and guc and uc:
            tp_total = sum(_safe_float(r.get(pt, 0)) for r in rows)
            op_total = sum(_safe_float(r.get(ot, 0)) for r in rows)
            u_total = sum(_safe_float(r.get(uc, 0)) for r in rows)
            g_total = sum(_safe_float(r.get(guc, 0)) for r in rows)
            avail = (op_total / tp_total * 100) if tp_total > 0 else 0.0
            quality = (g_total / u_total * 100) if u_total > 0 else 0.0
            perf = 100.0
            if tpc:
                target_total = sum(_safe_float(r.get(tpc, 0)) for r in rows)
                if target_total > 0:
                    perf = u_total / target_total * 100
            oee = avail * (perf / 100) * (quality / 100)
            metrics["availability_pct"] = round(avail, 1)
            metrics["quality_pct"] = round(quality, 1)
            metrics["performance_pct"] = round(perf, 1)
            metrics["oee_pct"] = round(oee, 1)

        metrics.update(external_metrics)

        dur_s = int(age_s)
        h, rem = divmod(dur_s, 3600)
        m, s = divmod(rem, 60)
        duration_str = f"{h:02d}:{m:02d}:{s:02d}"

        last_tick_s: float | None = None
        if self.last_tick:
            last_tick_s = round((now - self.last_tick).total_seconds(), 1)

        return {
            "ts": now.isoformat(),
            "stream_id": self.stream_id,
            "name": self.name,
            "status": self.status,
            "error": self.error,
            "rows_in_window": len(rows),
            "rows_collected": self.rows_collected,
            "duration": duration_str,
            "duration_s": dur_s,
            "last_tick_s": last_tick_s,
            "metrics": metrics,
        }


# ── Global registry (this process only - see module docstring) ──────────────

_runners: dict[str, StreamRunner] = {}
_registry_lock = Lock()


def start_runner(
    *,
    stream_id: uuid.UUID,
    name: str,
    integration_type: str,
    integration_config: dict[str, Any],
    column_map: dict[str, str],
    interval_seconds: int,
    accumulate: bool,
) -> StreamRunner:
    """(Re)start the in-memory collector for an existing stream_id. Stopping
    any prior runner for the same id first makes this idempotent, needed both
    for an explicit "start" call on an already-defined stream and for
    resume_all_streams() re-adopting a stream_id that never actually died in
    the registry (defensive; shouldn't happen in practice)."""
    sid = str(stream_id)
    with _registry_lock:
        existing = _runners.pop(sid, None)
    if existing is not None:
        existing.stop()

    runner = StreamRunner(
        stream_id=sid,
        name=name,
        integration_type=integration_type,
        integration_config=integration_config,
        column_map=column_map,
        interval_seconds=interval_seconds,
        accumulate=accumulate,
    )
    with _registry_lock:
        _runners[sid] = runner
    return runner


def get_runner(stream_id: uuid.UUID) -> StreamRunner | None:
    return _runners.get(str(stream_id))


def stop_runner(stream_id: uuid.UUID) -> bool:
    sid = str(stream_id)
    with _registry_lock:
        runner = _runners.pop(sid, None)
    if runner is None:
        return False
    runner.stop()
    return True


def stop_all_runners() -> None:
    """Called from app.main's shutdown hook so background threads don't get
    silently orphaned when the event loop exits."""
    with _registry_lock:
        runners = list(_runners.values())
        _runners.clear()
    for r in runners:
        r.stop()


async def resume_all_streams() -> None:
    """Called from app.main's startup hook. Re-spins the in-memory collector
    for every RealtimeStream still marked desired_status="running" across all
    orgs - a redeploy/restart stops collection, but the definition survives
    and picks back up here rather than requiring the user to notice and
    restart it manually."""
    async with db_session.async_session_factory() as db:
        rows = (
            await db.execute(
                select(RealtimeStream, Integration)
                .join(Integration, Integration.id == RealtimeStream.integration_id)
                .where(RealtimeStream.desired_status == "running")
            )
        ).all()

    for stream, integration in rows:
        try:
            start_runner(
                stream_id=stream.id,
                name=stream.name,
                integration_type=integration.type,
                integration_config=decrypt_config(integration.config),
                column_map=stream.column_map,
                interval_seconds=stream.interval_seconds,
                accumulate=stream.accumulate,
            )
        except Exception:
            continue  # best-effort resume; a bad integration shouldn't block the others or app startup
