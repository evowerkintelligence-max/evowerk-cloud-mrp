from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.services.anomaly import score_reading


def test_score_reading_learning_period_before_min_baseline():
    base = datetime(2026, 1, 1)
    values = [10.0] * 5
    timestamps = [base + timedelta(hours=i) for i in range(len(values))]

    result = score_reading(values, timestamps)

    assert result.learning is True
    assert result.in_control is True
    assert result.violations == []


def test_score_reading_flags_out_of_control_spike():
    base = datetime(2026, 1, 1)
    values = [10.0] * 25 + [50.0]  # stable baseline, then a huge spike
    timestamps = [base + timedelta(hours=i) for i in range(len(values))]

    result = score_reading(values, timestamps)

    assert result.learning is False
    assert result.in_control is False
    assert 1 in result.violations  # Nelson rule 1: point beyond 3-sigma


def test_score_reading_in_control_for_stable_series():
    random.seed(42)
    base = datetime(2026, 1, 1)
    values = [round(10.0 + random.gauss(0, 0.15), 3) for _ in range(25)]
    timestamps = [base + timedelta(hours=i) for i in range(len(values))]

    result = score_reading(values, timestamps)

    assert result.learning is False
    assert result.in_control is True
    assert result.violations == []
