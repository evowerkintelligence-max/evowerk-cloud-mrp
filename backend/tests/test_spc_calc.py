"""Pure-Python unit tests for app/analytics/spc_calc.py — no DB/API needed."""
from __future__ import annotations

from app.analytics import spc_calc


def test_nelson_rule1_single_point_beyond_3_sigma():
    z = [0.1, -0.2, 0.3, -0.1, 0.2, 3.5, -0.1, 0.2]
    violations = spc_calc.nelson_violations(z)
    assert violations[5] == [1]
    assert all(not v for i, v in enumerate(violations) if i != 5)


def test_nelson_rule2_nine_in_a_row_same_side():
    z = [0.5] * 9 + [-0.5] * 3
    violations = spc_calc.nelson_violations(z)
    assert all(2 in violations[i] for i in range(9))
    assert all(2 not in violations[i] for i in range(9, 12))


def test_individuals_chart_flags_out_of_control_spike():
    values = [10.0] * 7 + [50.0]
    chart = spc_calc.individuals_chart(values, [str(i) for i in range(8)])
    assert chart["out_of_control_count"] == 1
    outlier = next(p for p in chart["points"] if p["value"] == 50.0)
    assert outlier["in_control"] is False
    assert 1 in outlier["violations"]


def test_individuals_chart_low_sample_warning():
    small = spc_calc.individuals_chart([10.0, 11.0, 10.5], ["a", "b", "c"])
    assert small["low_sample_warning"] is True

    big_values = [10.0 + (i % 3) * 0.1 for i in range(25)]
    big = spc_calc.individuals_chart(big_values, [str(i) for i in range(25)])
    assert big["low_sample_warning"] is False


def test_p_chart_sample_size_adjusted_limits():
    # Same 25% defect rate, but a period with a tiny sample (n=4) should get
    # much wider limits than a period with a big sample (n=400) — the whole
    # point of a p-chart over an I-MR chart run on the rate.
    counts = [1, 100]
    sizes = [4, 400]
    chart = spc_calc.p_chart(counts, sizes, ["small", "big"])
    small_pt, big_pt = chart["points"]
    small_width = small_pt["ucl"] - small_pt["lcl"]
    big_width = big_pt["ucl"] - big_pt["lcl"]
    assert small_width > big_width


def test_p_chart_flags_spike_out_of_proportion_to_sample_size():
    # 7 periods at 25% (n=20, under the 9-in-a-row rule-2 threshold), one
    # period spiking to 100% (n=20) - only the spike should trip rule 1.
    counts = [5] * 7 + [20]
    sizes = [20] * 8
    chart = spc_calc.p_chart(counts, sizes, [str(i) for i in range(8)])
    assert chart["out_of_control_count"] == 1
    assert chart["points"][7]["in_control"] is False


def test_normality_check_too_few_points():
    result = spc_calc.normality_check([1.0, 2.0, 3.0])
    assert result["tested"] is False
    assert result["is_normal"] is None


def test_normality_check_flags_skewed_data():
    skewed = [1.0] * 8 + [100.0]
    result = spc_calc.normality_check(skewed)
    assert result["tested"] is True
    assert result["is_normal"] is False


def test_normality_check_accepts_roughly_symmetric_data():
    symmetric = [10, 11, 9, 10, 12, 8, 10, 11, 9, 10]
    result = spc_calc.normality_check([float(v) for v in symmetric])
    assert result["tested"] is True
    assert result["is_normal"] is True
