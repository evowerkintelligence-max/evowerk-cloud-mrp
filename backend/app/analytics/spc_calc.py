# app/analytics/spc_calc.py
#
# SPC control-chart math — ported near-verbatim from the desktop app
# (evowerk-console backend/routes/spc.py). Pure Python over an ordered value
# stream; the service supplies the values (fetched via DuckDB). Returns plain
# dicts matching the SPCResponse shape (the route wraps them).

from __future__ import annotations

from typing import Optional

# Standard Shewhart control-chart constants: subgroup size n -> (A2, D3, D4, d2).
# n=2 doubles as the Individuals/Moving-Range case.
_CONTROL_CONSTANTS: dict[int, tuple[float, float, float, float]] = {
    2:  (1.880, 0.000, 3.267, 1.128),
    3:  (1.023, 0.000, 2.574, 1.693),
    4:  (0.729, 0.000, 2.282, 2.059),
    5:  (0.577, 0.000, 2.114, 2.326),
    6:  (0.483, 0.000, 2.004, 2.534),
    7:  (0.419, 0.076, 1.924, 2.704),
    8:  (0.373, 0.136, 1.864, 2.847),
    9:  (0.337, 0.184, 1.816, 2.970),
    10: (0.308, 0.223, 1.777, 3.078),
}


def control_constants(n: int) -> tuple[float, float, float, float]:
    n = max(2, min(10, int(n)))
    return _CONTROL_CONSTANTS[n]


def std(vals: list[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5


def capability(
    mean: float, sigma_within: float, sigma_overall: float,
    usl: Optional[float], lsl: Optional[float],
) -> dict:
    cp = cpk = pp = ppk = None
    if usl is not None and lsl is not None and usl > lsl:
        if sigma_within > 0:
            cp = round((usl - lsl) / (6 * sigma_within), 3)
            cpk = round(min((usl - mean) / (3 * sigma_within), (mean - lsl) / (3 * sigma_within)), 3)
        if sigma_overall > 0:
            pp = round((usl - lsl) / (6 * sigma_overall), 3)
            ppk = round(min((usl - mean) / (3 * sigma_overall), (mean - lsl) / (3 * sigma_overall)), 3)
    return {
        "mean": round(mean, 4), "sigma_within": round(sigma_within, 4),
        "sigma_overall": round(sigma_overall, 4), "usl": usl, "lsl": lsl,
        "cp": cp, "cpk": cpk, "pp": pp, "ppk": ppk,
    }


def individuals_chart(values: list[float], labels: list[str]) -> dict:
    n = len(values)
    mean = sum(values) / n if n else 0.0
    mrs = [abs(values[i] - values[i - 1]) for i in range(1, n)]
    mr_bar = sum(mrs) / len(mrs) if mrs else 0.0

    _, _, d4, d2 = control_constants(2)
    ucl = mean + 2.66 * mr_bar   # 3/d2 == 2.6595... for n=2
    lcl = mean - 2.66 * mr_bar
    r_ucl = d4 * mr_bar

    points = []
    oob = 0
    for i, (v, lbl) in enumerate(zip(values, labels)):
        in_control = lcl <= v <= ucl
        if not in_control:
            oob += 1
        points.append({
            "label": lbl, "value": round(v, 4),
            "range_value": round(mrs[i - 1], 4) if i > 0 else None,
            "in_control": in_control,
        })

    return {
        "chart_type": "individuals", "subgroup_size": 1,
        "centerline": round(mean, 4), "ucl": round(ucl, 4), "lcl": round(lcl, 4),
        "range_centerline": round(mr_bar, 4), "range_ucl": round(r_ucl, 4), "range_lcl": 0.0,
        "points": points, "out_of_control_count": oob, "row_count": n, "truncated": False,
    }


def xbar_r_chart(subgroups: list[list[float]], labels: list[str]) -> dict:
    sizes = [len(g) for g in subgroups if g]
    n_typ = round(sum(sizes) / len(sizes)) if sizes else 2
    a2, d3, d4, d2 = control_constants(n_typ)

    means = [sum(g) / len(g) for g in subgroups]
    ranges = [max(g) - min(g) for g in subgroups]
    grand_mean = sum(means) / len(means) if means else 0.0
    r_bar = sum(ranges) / len(ranges) if ranges else 0.0

    ucl = grand_mean + a2 * r_bar
    lcl = grand_mean - a2 * r_bar
    r_ucl = d4 * r_bar
    r_lcl = d3 * r_bar

    points = []
    oob = 0
    for lbl, m, r in zip(labels, means, ranges):
        in_control = lcl <= m <= ucl and r_lcl <= r <= r_ucl
        if not in_control:
            oob += 1
        points.append({"label": lbl, "value": round(m, 4), "range_value": round(r, 4), "in_control": in_control})

    return {
        "chart_type": "xbar_r", "subgroup_size": n_typ,
        "centerline": round(grand_mean, 4), "ucl": round(ucl, 4), "lcl": round(lcl, 4),
        "range_centerline": round(r_bar, 4), "range_ucl": round(r_ucl, 4), "range_lcl": round(r_lcl, 4),
        "points": points, "out_of_control_count": oob, "row_count": sum(sizes), "truncated": False,
    }
