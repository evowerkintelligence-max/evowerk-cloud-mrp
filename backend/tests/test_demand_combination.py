from __future__ import annotations

import uuid
from decimal import Decimal

from app.services.demand import combine_demand

A = uuid.uuid4()
B = uuid.uuid4()


def test_consume_plans_the_larger_of_the_two():
    result = combine_demand({A: Decimal(100)}, {A: Decimal(60)}, "consume")
    assert result == {A: Decimal(100)}


def test_consume_follows_the_orders_once_they_exceed_the_estimate():
    result = combine_demand({A: Decimal(100)}, {A: Decimal(130)}, "consume")
    assert result == {A: Decimal(130)}


def test_consume_at_the_boundary_where_they_are_equal():
    result = combine_demand({A: Decimal(100)}, {A: Decimal(100)}, "consume")
    assert result == {A: Decimal(100)}


def test_add_sums_them():
    result = combine_demand({A: Decimal(100)}, {A: Decimal(60)}, "add")
    assert result == {A: Decimal(160)}


def test_an_item_present_in_only_one_source_still_appears():
    result = combine_demand({A: Decimal(10)}, {B: Decimal(5)}, "consume")
    assert result == {A: Decimal(10), B: Decimal(5)}


def test_empty_on_both_sides_is_empty():
    assert combine_demand({}, {}, "consume") == {}


def test_committed_only_survives_under_both_policies():
    assert combine_demand({}, {A: Decimal(7)}, "consume") == {A: Decimal(7)}
    assert combine_demand({}, {A: Decimal(7)}, "add") == {A: Decimal(7)}
