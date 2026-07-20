from __future__ import annotations

import pytest

from app.services.registry_matching import normalized_similarity

pytestmark = pytest.mark.asyncio


def test_normalized_similarity_exact_after_casefold_and_trim():
    assert normalized_similarity(" CNC-01 ", "cnc-01") == 1.0


def test_normalized_similarity_near_duplicate_above_threshold():
    assert normalized_similarity("CNC-01", "CNC01") >= 0.8


def test_normalized_similarity_unrelated_below_threshold():
    assert normalized_similarity("CNC-01", "Assembly Line 3") < 0.8
