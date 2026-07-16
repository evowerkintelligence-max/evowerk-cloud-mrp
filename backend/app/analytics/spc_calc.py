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

# Below this many points/subgroups, control limits are statistically unreliable
# (20-25 is the textbook minimum baseline for establishing limits).
MIN_BASELINE = 20

RULE_DESCRIPTIONS: dict[int, str] = {
    1: "Point beyond 3σ (outside control limits)",
    2: "9 points in a row on the same side of the centerline",
    3: "6 points in a row steadily increasing or decreasing",
    4: "14 points in a row alternating up and down",
    5: "2 of 3 points beyond 2σ on the same side",
    6: "4 of 5 points beyond 1σ on the same side",
    7: "15 points in a row within 1σ of the centerline (stratification)",
    8: "8 points in a row beyond 1σ with none within 1σ (mixture)",
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


def nelson_violations(z: list[float]) -> list[list[int]]:
    """All 8 Nelson rules over a standardized (mean=0, sigma=1) series.

    Each rule flags the points that make up the offending run/pattern, not
    just its last point — matches how Minitab/JMP mark violations.
    """
    n = len(z)
    out: list[list[int]] = [[] for _ in range(n)]

    def flag(i0: int, i1: int, rule: int) -> None:
        for k in range(i0, i1 + 1):
            if rule not in out[k]:
                out[k].append(rule)

    # Rule 1: single point beyond 3 sigma
    for i, zi in enumerate(z):
        if abs(zi) > 3:
            flag(i, i, 1)

    # Rule 2: 9 points in a row on the same side of the centerline
    run, side = 0, 0
    for i, zi in enumerate(z):
        s = 1 if zi > 0 else (-1 if zi < 0 else 0)
        run = run + 1 if (s != 0 and s == side) else (1 if s != 0 else 0)
        side = s
        if run >= 9:
            flag(i - 8, i, 2)

    # Rule 3: 6 points in a row steadily increasing or decreasing
    run, direction = 1, 0
    for i in range(1, n):
        d = 1 if z[i] > z[i - 1] else (-1 if z[i] < z[i - 1] else 0)
        run = run + 1 if (d != 0 and d == direction) else (2 if d != 0 else 1)
        direction = d
        if run >= 6:
            flag(i - 5, i, 3)

    # Rule 4: 14 points in a row alternating up and down
    run = 1
    for i in range(2, n):
        d = 1 if z[i] > z[i - 1] else (-1 if z[i] < z[i - 1] else 0)
        pd_ = 1 if z[i - 1] > z[i - 2] else (-1 if z[i - 1] < z[i - 2] else 0)
        run = run + 1 if (d != 0 and pd_ != 0 and d == -pd_) else 1
        if run >= 14:
            flag(i - 13, i, 4)

    # Rule 5: 2 of 3 points beyond 2 sigma on the same side
    for i in range(2, n):
        window = z[i - 2:i + 1]
        if any(sum(1 for w in window if w * sign > 2) >= 2 for sign in (1, -1)):
            flag(i, i, 5)

    # Rule 6: 4 of 5 points beyond 1 sigma on the same side
    for i in range(4, n):
        window = z[i - 4:i + 1]
        if any(sum(1 for w in window if w * sign > 1) >= 4 for sign in (1, -1)):
            flag(i, i, 6)

    # Rule 7: 15 points in a row within 1 sigma of the centerline (stratification)
    run = 0
    for i, zi in enumerate(z):
        run = run + 1 if abs(zi) < 1 else 0
        if run >= 15:
            flag(i - 14, i, 7)

    # Rule 8: 8 points in a row beyond 1 sigma, none within 1 sigma (mixture)
    run = 0
    for i, zi in enumerate(z):
        run = run + 1 if abs(zi) > 1 else 0
        if run >= 8:
            flag(i - 7, i, 8)

    return out


def normality_check(values: list[float]) -> dict:
    """Shapiro-Wilk normality test — capability indices (Cp/Cpk/Pp/Ppk) assume
    normal data, so flag when that assumption looks shaky.
    """
    n = len(values)
    if n < 8:
        return {"tested": False, "reason": "Need at least 8 points to test normality.", "is_normal": None}
    from scipy.stats import shapiro
    # ponytail: Shapiro-Wilk gets over-sensitive past ~5000 points; cap the
    # sample rather than adding a separate large-sample normality test.
    sample = values if n <= 5000 else values[:5000]
    stat, p = shapiro(sample)
    return {"tested": True, "statistic": round(float(stat), 4), "p_value": round(float(p), 4), "is_normal": bool(p > 0.05)}


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
    sigma = mr_bar / d2 if d2 else 0.0
    ucl = mean + 3 * sigma
    lcl = mean - 3 * sigma
    r_ucl = d4 * mr_bar

    z = [(v - mean) / sigma if sigma else 0.0 for v in values]
    violations = nelson_violations(z)

    points = []
    oob = 0
    for i, (v, lbl) in enumerate(zip(values, labels)):
        viol = violations[i]
        in_control = not viol
        if not in_control:
            oob += 1
        points.append({
            "label": lbl, "value": round(v, 4),
            "range_value": round(mrs[i - 1], 4) if i > 0 else None,
            "in_control": in_control, "violations": viol,
        })

    return {
        "chart_type": "individuals", "subgroup_size": 1,
        "centerline": round(mean, 4), "ucl": round(ucl, 4), "lcl": round(lcl, 4),
        "range_centerline": round(mr_bar, 4), "range_ucl": round(r_ucl, 4), "range_lcl": 0.0,
        "points": points, "out_of_control_count": oob, "row_count": n, "truncated": False,
        "low_sample_warning": n < MIN_BASELINE, "min_baseline": MIN_BASELINE,
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
    sigma_xbar = (a2 * r_bar) / 3 if r_bar else 0.0

    z = [(m - grand_mean) / sigma_xbar if sigma_xbar else 0.0 for m in means]
    violations = nelson_violations(z)

    points = []
    oob = 0
    for i, (lbl, m, r) in enumerate(zip(labels, means, ranges)):
        range_in_control = r_lcl <= r <= r_ucl
        viol = violations[i]
        in_control = (not viol) and range_in_control
        if not in_control:
            oob += 1
        points.append({
            "label": lbl, "value": round(m, 4), "range_value": round(r, 4),
            "in_control": in_control, "violations": viol,
        })

    return {
        "chart_type": "xbar_r", "subgroup_size": n_typ,
        "centerline": round(grand_mean, 4), "ucl": round(ucl, 4), "lcl": round(lcl, 4),
        "range_centerline": round(r_bar, 4), "range_ucl": round(r_ucl, 4), "range_lcl": round(r_lcl, 4),
        "points": points, "out_of_control_count": oob, "row_count": sum(sizes), "truncated": False,
        "low_sample_warning": len(subgroups) < MIN_BASELINE, "min_baseline": MIN_BASELINE,
    }


def p_chart(defect_counts: list[int], sample_sizes: list[int], labels: list[str]) -> dict:
    """Proportion (p) chart — control limits widen/narrow per point with its
    own sample size (sigma_i = sqrt(p_bar*(1-p_bar)/n_i)), unlike an I-MR
    chart run over a rate series which assumes one fixed sigma throughout.
    """
    total_defects = sum(defect_counts)
    total_n = sum(sample_sizes)
    p_bar = total_defects / total_n if total_n else 0.0

    rates = [d / ni if ni else 0.0 for d, ni in zip(defect_counts, sample_sizes)]
    sigmas = [
        (p_bar * (1 - p_bar) / ni) ** 0.5 if ni and 0 < p_bar < 1 else 0.0
        for ni in sample_sizes
    ]
    z = [(p - p_bar) / s if s else 0.0 for p, s in zip(rates, sigmas)]
    violations = nelson_violations(z)

    points = []
    oob = 0
    for i, (lbl, p, s) in enumerate(zip(labels, rates, sigmas)):
        ucl_i = min(1.0, p_bar + 3 * s)
        lcl_i = max(0.0, p_bar - 3 * s)
        viol = violations[i]
        in_control = not viol
        if not in_control:
            oob += 1
        points.append({
            "label": lbl, "value": round(p * 100, 4),
            "ucl": round(ucl_i * 100, 4), "lcl": round(lcl_i * 100, 4),
            "in_control": in_control, "violations": viol,
        })

    return {
        "chart_type": "p", "subgroup_size": None,
        "centerline": round(p_bar * 100, 4),
        "points": points, "out_of_control_count": oob, "row_count": total_n,
        "low_sample_warning": len(defect_counts) < MIN_BASELINE, "min_baseline": MIN_BASELINE,
    }
