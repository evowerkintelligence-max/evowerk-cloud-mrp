"""Anomaly scoring for CBM sensor readings - wraps app.analytics.spc_calc's
existing Individuals/Moving-Range control chart (all 8 Nelson rules) instead
of a new bespoke statistic, so CBM gets the same rigor already shipped on the
SPC analytics page rather than a second, weaker implementation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.analytics.spc_calc import MIN_BASELINE, individuals_chart

WINDOW = 200  # trailing readings considered per (machine, sensor) score


@dataclass
class AnomalyResult:
    in_control: bool
    violations: list[int]
    centerline: float
    ucl: float
    lcl: float
    mr_bar: float
    n: int
    learning: bool  # True while n < MIN_BASELINE - no violation is meaningful yet


def score_reading(values: list[float], timestamps: list[datetime]) -> AnomalyResult:
    """`values`/`timestamps` are the trailing window for one (machine, sensor),
    oldest first, with the newest reading last. All control-limit math is
    delegated to spc_calc.individuals_chart; this just reads off the last
    point and repackages it."""
    n = len(values)
    if n < MIN_BASELINE:
        return AnomalyResult(True, [], 0.0, 0.0, 0.0, 0.0, n, learning=True)

    labels = [ts.isoformat() for ts in timestamps]
    chart = individuals_chart(values, labels)
    last = chart["points"][-1]
    return AnomalyResult(
        in_control=last["in_control"],
        violations=last["violations"],
        centerline=chart["centerline"],
        ucl=chart["ucl"],
        lcl=chart["lcl"],
        mr_bar=chart["range_centerline"],
        n=n,
        learning=False,
    )
