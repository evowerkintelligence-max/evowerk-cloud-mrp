"""Thin poll-loop producer that publishes raw connector rows to the message
bus instead of computing metrics in-process - the collector half of the
Redpanda+Flink slice. Additive/new file; app/services/realtime.py still owns
StreamRunner until Task 8 cuts it over. See
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, Thread
from typing import Any, Callable

from app.services.connectors import get_connector
from streaming.schemas import TickMessage

TICKS_TOPIC = "realtime.ticks"


class TickProducer:
    def __init__(
        self,
        *,
        stream_id: str,
        org_id: str,
        name: str,
        integration_type: str,
        integration_config: dict[str, Any],
        column_map: dict[str, str],
        interval_seconds: int,
        publish: Callable[[str, TickMessage], None],
        accumulate: bool = True,
        # accumulate is accepted only for start_runner() call-site compatibility;
        # it is unused - Flink's per-window aggregation replaces the old
        # accumulate-vs-clear-buffer distinction entirely.
    ):
        self.stream_id = stream_id
        self.org_id = org_id
        self.name = name
        self.column_map = column_map
        self.interval_seconds = max(1, interval_seconds)
        self._publish = publish
        self.status = "running"
        self.error: str | None = None

        self._stop = Event()
        self._thread = Thread(
            target=self._run, args=(integration_type, integration_config),
            daemon=True, name=f"tick-producer-{stream_id[:8]}",
        )
        self._thread.start()

    def _tick(self, row: dict[str, Any]) -> TickMessage:
        return TickMessage(
            stream_id=self.stream_id, org_id=self.org_id, name=self.name,
            column_map=self.column_map, row=row, status=self.status, error=self.error,
            collected_at=datetime.now(timezone.utc).isoformat(),
        )

    def _run(self, integration_type: str, integration_config: dict[str, Any]) -> None:
        try:
            conn = get_connector(integration_type, integration_config)
        except Exception as exc:
            self.status = "error"
            self.error = f"Connector init failed: {exc}"
            return

        while not self._stop.is_set():
            try:
                rows: list[dict] = conn.fetch_all()
                self.status = "running"
                self.error = None
                for row in rows:
                    self._publish(TICKS_TOPIC, self._tick(row))
            except Exception as exc:
                self.status = "error"
                self.error = str(exc)
                self._publish(TICKS_TOPIC, self._tick({}))

            if integration_type != "mqtt":
                self._stop.wait(timeout=self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
