"""RCA case analytics: evidence engine (candidate causes), Pareto, timeline and
before/after verification, computed live from a case's Dataset. Split out of
rca_cases.py, which re-exports every name here."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.analytics.oee_calc import compute_components_ideal, inv_expr, oee_grouped, oee_overall, qi, val_expr
from app.models.dataset import Dataset
from app.services import datasets as ds_service

_MAX_CANDIDATES = 25
_SEVERITY_ANCHOR_PTS = 50.0
_SUPPORT_ANCHOR_PCT = 20.0


# ─── Evidence engine ────────────────────────────────────────────────────────

@dataclass
class RcaCandidateCause:
    dimension: str
    label: str
    oee: float
    delta_oee: float
    row_count: int
    total_units: float
    support_pct: float
    confidence: float


@dataclass
class RcaEvidenceResult:
    overall_oee: float
    overall_availability: float
    overall_performance: float
    overall_quality: float
    candidates: list[RcaCandidateCause] = field(default_factory=list)
    candidates_total: int = 0


def _confidence(delta_oee: float, support_pct: float) -> float:
    """Composite ranking score, 0-100: favors candidates that are BOTH bad
    (large negative delta_oee) AND represent a meaningful share of volume
    (support_pct) - a huge delta from 2 rows out of 20,000 is noise."""
    severity_score = min(1.0, abs(delta_oee) / _SEVERITY_ANCHOR_PTS)
    support_score = min(1.0, max(0.0, support_pct) / _SUPPORT_ANCHOR_PCT)
    return round(severity_score * support_score * 100.0, 1)


def compute_evidence(
    dataset: Dataset, *, planned_col: str, operating_col: str, total_col: str, good_col: str,
    dimension_cols: list[str], target_rate: float | None, min_support_rows: int,
) -> RcaEvidenceResult:
    ds_service.validate_columns(dataset, [planned_col, operating_col, total_col, good_col, *dimension_cols])
    rel = ds_service.dataset_relation(dataset)
    has_rate = bool(target_rate and target_rate > 0)

    def _ideal_and_oee(g: dict) -> tuple[float, float, float, float, float]:
        # A flat target_rate has no per-row grouping here (no machine
        # dimension), so ideal units = operating minutes * the one rate - the
        # same shape as oee_calc's ideal_units_expr collapsed to a constant.
        ideal = g["operating"] * float(target_rate) if has_rate else g["total"]
        return compute_components_ideal(g["planned"], g["operating"], g["total"], g["good"], ideal, not has_rate)

    con = ds_service.open_con()
    try:
        overall = oee_overall(con, rel, planned_col, operating_col, total_col, good_col)
        overall_avail, overall_perf, overall_qual, overall_oee, _ = _ideal_and_oee(overall)

        total_planned = overall["planned"]
        candidates: list[RcaCandidateCause] = []
        for dim_col in dimension_cols:
            grouped = oee_grouped(con, rel, planned_col, operating_col, total_col, good_col, dim_col, with_count=True)
            for label, grp in grouped:
                row_count = int(grp.get("count", 0))
                if row_count < min_support_rows:
                    continue
                _, _, _, oee_val, _ = _ideal_and_oee(grp)
                delta = round(oee_val - overall_oee, 2)
                if delta >= 0:
                    continue  # only surface candidates WORSE than overall - evidence of a problem
                support_pct = round((grp["planned"] / total_planned) * 100.0, 2) if total_planned > 0 else 0.0
                candidates.append(RcaCandidateCause(
                    dimension=dim_col, label=label, oee=oee_val, delta_oee=delta, row_count=row_count,
                    total_units=grp["total"], support_pct=support_pct, confidence=_confidence(delta, support_pct),
                ))
    finally:
        con.close()

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    candidates_total = len(candidates)
    return RcaEvidenceResult(
        overall_oee=overall_oee, overall_availability=overall_avail,
        overall_performance=overall_perf, overall_quality=overall_qual,
        candidates=candidates[:_MAX_CANDIDATES], candidates_total=candidates_total,
    )


# ─── Pareto ─────────────────────────────────────────────────────────────────

@dataclass
class RcaParetoBucket:
    label: str
    value: float
    pct_of_total: float
    cumulative_pct: float


@dataclass
class RcaParetoResult:
    total: float
    buckets: list[RcaParetoBucket] = field(default_factory=list)
    buckets_total: int = 0


def compute_pareto(dataset: Dataset, *, category_col: str, value_col: str, top_n: int) -> RcaParetoResult:
    """Classic 80/20: sum value_col per category_col, sorted descending, with a
    running cumulative %. Categories beyond top_n fold into one "Other"
    bucket so cumulative_pct still reaches 100%."""
    ds_service.validate_columns(dataset, [category_col, value_col])
    rel = ds_service.dataset_relation(dataset)
    cat_q, val_e, inv_e = qi(category_col), val_expr(value_col), inv_expr(value_col)
    con = ds_service.open_con()
    try:
        sql = (
            f"SELECT CAST({cat_q} AS VARCHAR), SUM({val_e}), SUM({inv_e}) FROM {rel} "  # nosec B608 - cat_q/val_e/inv_e are qi()/validate_columns-checked
            f"WHERE NULLIF(TRIM(CAST({cat_q} AS VARCHAR)), '') IS NOT NULL GROUP BY 1"
        )
        rows = con.execute(sql).fetchall()
    finally:
        con.close()

    buckets_all = sorted(
        ((str(r[0]), float(r[1] or 0.0)) for r in rows), key=lambda b: b[1], reverse=True
    )
    total = sum(v for _, v in buckets_all)
    top, rest = buckets_all[:top_n], buckets_all[top_n:]

    result: list[RcaParetoBucket] = []
    cum = 0.0
    for label, value in top:
        cum += value
        result.append(RcaParetoBucket(
            label=label, value=round(value, 2),
            pct_of_total=round(value / total * 100.0, 2) if total > 0 else 0.0,
            cumulative_pct=round(cum / total * 100.0, 2) if total > 0 else 0.0,
        ))
    if rest:
        rest_sum = sum(v for _, v in rest)
        cum += rest_sum
        result.append(RcaParetoBucket(
            label=f"Other ({len(rest)})", value=round(rest_sum, 2),
            pct_of_total=round(rest_sum / total * 100.0, 2) if total > 0 else 0.0,
            cumulative_pct=round(cum / total * 100.0, 2) if total > 0 else 0.0,
        ))
    return RcaParetoResult(total=round(total, 2), buckets=result, buckets_total=len(buckets_all))


# ─── Timeline ───────────────────────────────────────────────────────────────

@dataclass
class RcaTimelineEvent:
    date: str
    oee: float
    delta_oee: float
    row_count: int
    is_center: bool = False


@dataclass
class RcaTimelineResult:
    overall_oee: float
    center_date: str
    window_days: int
    events: list[RcaTimelineEvent] = field(default_factory=list)


def compute_timeline(
    dataset: Dataset, *, date_col: str, planned_col: str, operating_col: str, total_col: str, good_col: str,
    window_days: int, center_date: str | None,
) -> RcaTimelineResult:
    """Group by day, then show a window of days around whichever day is worst
    (or an investigator-chosen center_date)."""
    ds_service.validate_columns(dataset, [date_col, planned_col, operating_col, total_col, good_col])
    rel = ds_service.dataset_relation(dataset)
    con = ds_service.open_con()
    try:
        overall = oee_overall(con, rel, planned_col, operating_col, total_col, good_col)
        _, _, _, overall_oee, _ = compute_components_ideal(
            overall["planned"], overall["operating"], overall["total"], overall["good"], overall["total"], True
        )
        day_rows = oee_grouped(con, rel, planned_col, operating_col, total_col, good_col, date_col, with_count=True)
    finally:
        con.close()

    daily = []
    for day_key, g in day_rows:
        _, _, _, oee_val, _ = compute_components_ideal(g["planned"], g["operating"], g["total"], g["good"], g["total"], True)
        daily.append({"date": day_key[:10], "oee": oee_val, "row_count": int(g.get("count", 0))})
    daily.sort(key=lambda d: d["date"])

    if not daily:
        return RcaTimelineResult(overall_oee=overall_oee, center_date=center_date or "", window_days=window_days)

    center = center_date or min(daily, key=lambda d: d["oee"])["date"]
    try:
        center_d = date.fromisoformat(center)
    except ValueError:
        center_d = date.fromisoformat(daily[0]["date"])
        center = daily[0]["date"]
    start_d, end_d = center_d - timedelta(days=window_days), center_d + timedelta(days=window_days)

    events = [
        RcaTimelineEvent(
            date=d["date"], oee=d["oee"], delta_oee=round(d["oee"] - overall_oee, 2),
            row_count=d["row_count"], is_center=(d["date"] == center),
        )
        for d in daily
        if start_d <= date.fromisoformat(d["date"]) <= end_d
    ]
    return RcaTimelineResult(overall_oee=overall_oee, center_date=center, window_days=window_days, events=events)


# ─── Action effectiveness verification ─────────────────────────────────────

@dataclass
class RcaVerificationResult:
    before_oee: float
    after_oee: float
    delta_oee: float
    before_row_count: int
    after_row_count: int
    date_col: str
    split_at: str


def compute_verification(
    dataset: Dataset, *, planned_col: str, operating_col: str, total_col: str, good_col: str,
    date_col: str, split_at: datetime,
) -> RcaVerificationResult:
    ds_service.validate_columns(dataset, [planned_col, operating_col, total_col, good_col, date_col])
    rel = ds_service.dataset_relation(dataset)
    date_expr = f"TRY_CAST({qi(date_col)} AS TIMESTAMP)"
    split_iso = split_at.isoformat()
    con = ds_service.open_con()
    try:
        before = oee_overall(con, rel, planned_col, operating_col, total_col, good_col, where_sql=f"{date_expr} < TIMESTAMP '{split_iso}'")
        after = oee_overall(con, rel, planned_col, operating_col, total_col, good_col, where_sql=f"{date_expr} >= TIMESTAMP '{split_iso}'")
    finally:
        con.close()

    _, _, _, before_oee, _ = compute_components_ideal(before["planned"], before["operating"], before["total"], before["good"], before["total"], True)
    _, _, _, after_oee, _ = compute_components_ideal(after["planned"], after["operating"], after["total"], after["good"], after["total"], True)

    return RcaVerificationResult(
        before_oee=before_oee, after_oee=after_oee, delta_oee=round(after_oee - before_oee, 2),
        before_row_count=before["row_count"], after_row_count=after["row_count"],
        date_col=date_col, split_at=split_iso,
    )
