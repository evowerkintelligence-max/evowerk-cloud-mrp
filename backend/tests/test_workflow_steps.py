from __future__ import annotations

from app.services.workflow_steps import _aggregate_py


def test_aggregate_py_groups_and_sums():
    rows = [
        {"sku": "A-1", "qty": "10", "region": "east"},
        {"sku": "A-2", "qty": "5", "region": "west"},
        {"sku": "A-1", "qty": "3", "region": "west"},
    ]
    select = [
        {"column": "qty", "agg": "sum", "alias": "total_qty"},
        {"column": "sku", "agg": "count", "alias": "n"},
    ]
    out = {r["region"]: r for r in _aggregate_py(rows, ["region"], select)}
    assert out == {
        "east": {"region": "east", "total_qty": 10, "n": 1},
        "west": {"region": "west", "total_qty": 8, "n": 2},
    }


def test_aggregate_py_sum_of_all_non_numeric_group_is_none():
    rows = [{"tag": "x", "value": "not-a-number"}]
    out = _aggregate_py(rows, ["tag"], [{"column": "value", "agg": "sum", "alias": "total"}])
    assert out == [{"tag": "x", "total": None}]


def test_aggregate_py_no_group_by_aggregates_whole_table():
    rows = [{"qty": "10"}, {"qty": "5"}, {"qty": "3"}]
    out = _aggregate_py(rows, [], [{"column": "qty", "agg": "sum", "alias": "total_qty"}])
    assert out == [{"total_qty": 18}]


def test_aggregate_py_empty_rows_returns_empty():
    assert _aggregate_py([], ["region"], [{"column": "qty", "agg": "sum"}]) == []
