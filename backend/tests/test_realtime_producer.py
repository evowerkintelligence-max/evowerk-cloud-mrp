from __future__ import annotations

import time
from unittest.mock import patch

from app.services.realtime_producer import TickProducer


class _StubConnector:
    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self, max_rows: int = 0):
        return self._rows


def test_producer_publishes_one_tick_message_per_row():
    published = []

    def fake_get_connector(conn_type, config):
        return _StubConnector([{"units": 10}, {"units": 5}])

    with patch("app.services.realtime_producer.get_connector", side_effect=fake_get_connector):
        producer = TickProducer(
            stream_id="s1", org_id="o1", name="Line 1",
            integration_type="erp_rest", integration_config={},
            column_map={"units_col": "units"}, interval_seconds=100,
            publish=lambda topic, msg: published.append((topic, msg)),
        )
        deadline = time.time() + 2
        while len(published) < 2 and time.time() < deadline:
            time.sleep(0.01)
        producer.stop()

    assert len(published) == 2
    assert {topic for topic, _msg in published} == {"realtime.ticks"}
    assert {msg.row["units"] for _t, msg in published} == {10, 5}
    assert all(msg.status == "running" and msg.name == "Line 1" for _t, msg in published)


def test_producer_marks_status_error_and_publishes_error_message_on_connector_failure():
    published = []

    def fake_get_connector(conn_type, config):
        class _Boom:
            def fetch_all(self, max_rows: int = 0):
                raise RuntimeError("connection refused")
        return _Boom()

    with patch("app.services.realtime_producer.get_connector", side_effect=fake_get_connector):
        producer = TickProducer(
            stream_id="s1", org_id="o1", name="Line 1",
            integration_type="erp_rest", integration_config={},
            column_map={}, interval_seconds=100,
            publish=lambda topic, msg: published.append((topic, msg)),
        )
        deadline = time.time() + 2
        while not published and time.time() < deadline:
            time.sleep(0.01)
        producer.stop()

    assert producer.status == "error"
    assert "connection refused" in producer.error
    assert "connection refused" in published[0][1].error


def test_producer_marks_status_error_when_connector_init_fails():
    def fake_get_connector(conn_type, config):
        raise ValueError("bad config")

    with patch("app.services.realtime_producer.get_connector", side_effect=fake_get_connector):
        producer = TickProducer(
            stream_id="s1", org_id="o1", name="Line 1",
            integration_type="erp_rest", integration_config={},
            column_map={}, interval_seconds=100,
            publish=lambda topic, msg: None,
        )
        deadline = time.time() + 2
        while producer.status != "error" and time.time() < deadline:
            time.sleep(0.01)
        producer.stop()

    assert producer.status == "error"
    assert "bad config" in producer.error
