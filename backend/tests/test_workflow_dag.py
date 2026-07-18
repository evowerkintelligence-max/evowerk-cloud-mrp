from __future__ import annotations

import pytest

from app.services.workflow_dag import DagError, StepPosition, validate_dag


def test_validate_dag_preserves_position_when_present():
    steps = validate_dag(
        [{"id": "a", "name": "A", "type": "filter", "config": {}, "position": {"x": 120.5, "y": 40}}]
    )
    assert steps[0].position == StepPosition(x=120.5, y=40)


def test_validate_dag_position_defaults_to_none_when_absent():
    steps = validate_dag([{"id": "a", "name": "A", "type": "filter", "config": {}}])
    assert steps[0].position is None


def test_validate_dag_rejects_non_numeric_position():
    with pytest.raises(DagError):
        validate_dag(
            [{"id": "a", "name": "A", "type": "filter", "config": {}, "position": {"x": "abc", "y": 1}}]
        )
