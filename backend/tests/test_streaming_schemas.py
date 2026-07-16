from __future__ import annotations

from streaming.schemas import MetricMessage, TickMessage


def test_tick_message_round_trips_through_json():
    msg = TickMessage(
        stream_id="11111111-1111-1111-1111-111111111111",
        org_id="22222222-2222-2222-2222-222222222222",
        name="Line 3 feed",
        column_map={"units_col": "units"},
        row={"units": 10, "scrap": 1},
        status="running",
        error=None,
        collected_at="2026-07-16T12:00:00+00:00",
    )
    restored = TickMessage.from_json(msg.to_json())
    assert restored == msg


def test_metric_message_round_trips_through_json():
    msg = MetricMessage(
        stream_id="11111111-1111-1111-1111-111111111111",
        name="Line 3 feed",
        status="running",
        error=None,
        rows_in_window=42,
        rows_collected=420,
        duration_s=300,
        last_tick_s=1.2,
        metrics={"throughput": 42.0, "oee_pct": 87.5},
        ts="2026-07-16T12:00:05+00:00",
    )
    restored = MetricMessage.from_json(msg.to_json())
    assert restored == msg


def test_metric_message_error_and_last_tick_s_survive_none():
    msg = MetricMessage(
        stream_id="s1", name="n", status="error", error="boom",
        rows_in_window=0, rows_collected=0, duration_s=0, last_tick_s=None,
        metrics={}, ts="2026-07-16T12:00:00+00:00",
    )
    restored = MetricMessage.from_json(msg.to_json())
    assert restored.error == "boom"
    assert restored.last_tick_s is None
