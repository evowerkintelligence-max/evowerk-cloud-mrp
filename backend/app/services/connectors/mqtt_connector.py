"""MQTT connector for PLC/SCADA message-broker data. Subscribes, collects
messages for a timeout window, and returns them as rows.

Only reachable if broker_url is a network address this server can reach —
plant-floor MQTT brokers are usually on an isolated OT network with no public
exposure. Works if the broker is internet-reachable (a cloud MQTT broker,
or one the customer has exposed/tunneled).
"""
from __future__ import annotations
import json
import threading
from datetime import datetime, timezone
from typing import Any

try:
    import paho.mqtt.client as mqtt
    _HAS_PAHO = True
except ImportError:
    _HAS_PAHO = False


class MqttConnector:
    """
    Config fields:
      broker_url      — hostname or IP of MQTT broker
      port            — default 1883 (TLS: 8883)
      topics          — comma-separated topic patterns, e.g. "factory/+/temp,factory/+/speed"
      username        — optional
      password        — optional
      collect_seconds — how long to subscribe (default 5)
      message_format  — "json" (parse payload as JSON) or "raw" (string value)
    """

    def __init__(self, config: dict[str, Any]):
        self.broker = str(config.get("broker_url", "")).strip()
        self.port = int(config.get("port", 1883))
        raw_topics = str(config.get("topics", "#")).strip()
        self.topics = [t.strip() for t in raw_topics.split(",") if t.strip()]
        self.username = str(config.get("username", "")).strip() or None
        self.password = str(config.get("password", "")).strip() or None
        self.collect_sec = int(config.get("collect_seconds", 5))
        self.msg_format = str(config.get("message_format", "json")).lower()

        if not self.broker:
            raise ValueError("broker_url is required for MQTT connector")

    def _check_deps(self):
        if not _HAS_PAHO:
            raise RuntimeError("paho-mqtt is required for MQTT. Install with: pip install paho-mqtt")

    def _collect(self, max_msgs: int = 1000) -> list[dict[str, Any]]:
        self._check_deps()
        messages: list[dict] = []
        done = threading.Event()

        def on_connect(client, userdata, flags, rc):
            for topic in self.topics:
                client.subscribe(topic)

        def on_message(client, userdata, msg):
            if len(messages) >= max_msgs:
                done.set()
                return
            ts = datetime.now(timezone.utc).isoformat()
            payload_raw = msg.payload.decode("utf-8", errors="replace")
            if self.msg_format == "json":
                try:
                    payload = json.loads(payload_raw)
                    if isinstance(payload, dict):
                        row = {"topic": msg.topic, "timestamp": ts, **payload}
                    else:
                        row = {"topic": msg.topic, "timestamp": ts, "value": payload}
                except Exception:
                    row = {"topic": msg.topic, "timestamp": ts, "value": payload_raw}
            else:
                row = {"topic": msg.topic, "timestamp": ts, "value": payload_raw}
            messages.append(row)

        client = mqtt.Client()
        client.on_connect = on_connect
        client.on_message = on_message
        if self.username:
            client.username_pw_set(self.username, self.password)

        client.connect(self.broker, self.port, keepalive=10)
        client.loop_start()
        done.wait(timeout=self.collect_sec)
        client.loop_stop()
        client.disconnect()
        return messages

    def test(self) -> dict[str, Any]:
        self._check_deps()
        connected = threading.Event()
        err_holder: list[str] = []

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                connected.set()
            else:
                err_holder.append(f"MQTT connect error code {rc}")

        client = mqtt.Client()
        client.on_connect = on_connect
        if self.username:
            client.username_pw_set(self.username, self.password)
        try:
            client.connect(self.broker, self.port, keepalive=5)
            client.loop_start()
            ok = connected.wait(timeout=8)
            client.loop_stop()
            client.disconnect()
        except Exception as e:
            raise RuntimeError(f"MQTT connect failed: {e}")

        if not ok:
            msg = err_holder[0] if err_holder else (
                f"Timeout connecting to MQTT broker {self.broker}:{self.port} — "
                "check host/port, network/firewall, TLS (8883), and username/password."
            )
            raise RuntimeError(msg)
        return {"ok": True, "message": f"Connected to {self.broker}:{self.port}"}

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        return self._collect(max_msgs=n)[:n]

    def fetch_all(self, max_rows: int = 500_000) -> list[dict[str, Any]]:
        return self._collect(max_msgs=max_rows)
