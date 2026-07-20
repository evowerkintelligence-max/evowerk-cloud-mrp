"""Maintenance schedule CRUD - the recommendation/action queue used by both
the CBM fleet dashboard (app.services.cbm) and the AutoML predictive-
maintenance template. Kept generic: it doesn't care how a recommendation's
priority/action was computed.

The OEE-based risk-scoring helpers below (_build_daily, _stat_analyze,
_rul_days, _top_driver, ...) are no longer exposed through a routes/
predictive.py endpoint directly, but are still imported as `legacy` by
app.services.automl_templates.predictive_maintenance, which reuses them as
building blocks for its preprocess()/postprocess() - do not delete."""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.maintenance_schedule import MaintenanceSchedule

_CRITICAL_OEE = 0.40
_WARN_OEE = 0.60
_RUL_MAX_DAYS = 365.0
_TREND_WINDOW_DAYS = 21

_ML_FEATURES = ["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"]
_ML_FEAT_LABELS = {
    "oee_7d": "OEE (7-day avg)",
    "oee_slope_7d": "OEE trend (7-day)",
    "scrap_rate_7d": "Scrap rate (7-day)",
    "dt_rate_7d": "Downtime rate (7-day)",
    "fail_count_7d": "Failures in last 7 days",
    "days_since_fail": "Days since last failure",
}
_UNPLANNED_CATS = {"Mechanical Failure", "Electrical Fault"}

_ACTIONS = {
    "availability": (
        "Inspect mechanical components - lubrication, alignment, bearings",
        "Availability is the leading degradation driver; unplanned stoppages are increasing",
    ),
    "performance": (
        "Check feed rates, tooling wear and process parameter drift",
        "Performance ratio declining - machine is running below rated speed",
    ),
    "quality": (
        "Inspect tooling, calibrate sensors and review incoming material quality",
        "Quality losses trending up - reject rate rising, possible tooling or calibration issue",
    ),
}
_PM_HOURS = {"immediate": 16.0, "scheduled": 8.0, "monitor": 2.0}


@dataclass
class MachineRisk:
    machine: str
    current_oee: float
    trend_slope: float
    failure_probability: float
    risk_level: str
    top_driver: str
    rul_days: float | None
    last_failure_days_ago: float | None
    ml_powered: bool
    ml_drivers: list[dict] = field(default_factory=list)


@dataclass
class ModelMeta:
    algorithm: str
    accuracy_pct: float
    n_training_samples: int
    top_features: list[dict]


@dataclass
class MaintenanceRec:
    machine: str
    priority: str
    action: str
    reason: str
    estimated_pm_hours: float
    avoided_downtime_hours: float
    net_saving: float


@dataclass
class CostSummary:
    total_avoided_cost: float
    total_pm_cost: float
    net_savings: float
    roi_pct: float
    machines_at_risk: int


@dataclass
class PredictiveResult:
    machines: list[MachineRisk]
    recommendations: list[MaintenanceRec]
    cost_summary: CostSummary
    model_meta: ModelMeta | None


def _linreg_slope(y: list[float]) -> float:
    n = len(y)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(y) / n
    num = sum((i - mx) * (y[i] - my) for i in range(n))
    den = sum((i - mx) ** 2 for i in range(n))
    return num / den if den else 0.0


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


def _recent_slope(vals: list[float], window: int = _TREND_WINDOW_DAYS) -> float:
    """OEE trend over the last `window` days rather than the machine's entire
    lifetime - a long healthy history would otherwise dilute a real recent
    degradation almost flat."""
    return _linreg_slope(vals[-min(window, len(vals)):])


def _stat_analyze(
    oee_s: list[float], avail_s: list[float], perf_s: list[float], qual_s: list[float],
    last_fail_days: float | None, fleet_oee_median: float,
) -> tuple[float, list[dict]]:
    """Multi-signal statistical failure probability combining 8 signals via
    logistic aggregation - the fallback when there isn't enough data for ML."""
    n = len(oee_s)
    if n == 0:
        return 0.5, []

    current_oee = oee_s[-1]
    slope = _recent_slope(oee_s)

    level_signal = 1.0 / (1.0 + math.exp(12.0 * (current_oee - 0.65)))
    trend_signal = min(1.0, max(0.0, -slope * 15.0))

    accel_signal = 0.0
    if n >= 14:
        slope_recent = _linreg_slope(oee_s[-7:])
        slope_prior = _linreg_slope(oee_s[-14:-7])
        accel = slope_recent - slope_prior
        accel_signal = min(1.0, max(0.0, -accel * 25.0))
    elif n >= 7:
        slope_recent = _linreg_slope(oee_s[-7:])
        accel_signal = min(1.0, max(0.0, -slope_recent * 20.0))

    recent_window = oee_s[-min(14, n):]
    vol = _std(recent_window)
    vol_signal = min(1.0, vol / 0.15)

    recent_a = avail_s[-min(7, len(avail_s)):]
    avg_a_recent = sum(recent_a) / len(recent_a)
    avail_signal = 1.0 / (1.0 + math.exp(10.0 * (avg_a_recent - 0.80)))

    recent_q = qual_s[-min(7, len(qual_s)):]
    scrap_rates = [1.0 - q for q in recent_q]
    avg_scrap = sum(scrap_rates) / len(scrap_rates)
    qual_signal = min(1.0, avg_scrap / 0.10)

    fail_signal = 0.0
    if last_fail_days is not None:
        fail_signal = math.exp(-last_fail_days / 15.0)

    fleet_signal = 0.0
    if fleet_oee_median > 0:
        gap = fleet_oee_median - current_oee
        fleet_signal = min(1.0, max(0.0, gap / 0.20))

    weights = {
        "OEE level": (0.22, level_signal), "OEE trend": (0.18, trend_signal),
        "Degradation accel.": (0.10, accel_signal), "OEE volatility": (0.08, vol_signal),
        "Availability crisis": (0.15, avail_signal), "Quality erosion": (0.08, qual_signal),
        "Failure recency": (0.12, fail_signal), "Fleet-relative perf": (0.07, fleet_signal),
    }

    logit = sum(w * s for w, s in weights.values())
    prob = round(min(0.99, max(0.01, logit)), 3)

    contributions = []
    for label, (w, s) in weights.items():
        contrib = w * s
        if contrib < 0.005:
            continue
        contributions.append({
            "feature": label.lower().replace(" ", "_").replace(".", ""), "label": label,
            "importance": round(contrib, 3), "elevated": s > 0.4,
        })
    contributions.sort(key=lambda d: d["importance"], reverse=True)

    return prob, contributions[:3]


def _rul_days(current_oee: float, slope: float, days_per_period: float, accel: float = 0.0) -> float | None:
    if slope >= 0 or current_oee <= _CRITICAL_OEE:
        return None

    def _cap(days: float | None) -> float | None:
        if days is None or days <= 0 or days > _RUL_MAX_DAYS:
            return None
        return round(days, 1)

    linear_days = _cap((current_oee - _CRITICAL_OEE) / abs(slope))
    if linear_days is None:
        return None

    if accel < -1e-6:
        a, b, c = 0.5 * accel, slope, current_oee - _CRITICAL_OEE
        disc = b * b - 4.0 * a * c
        if disc >= 0:
            t1 = (-b - math.sqrt(disc)) / (2.0 * a)
            t2 = (-b + math.sqrt(disc)) / (2.0 * a)
            t = min(t for t in (t1, t2) if t > 0) if any(t > 0 for t in (t1, t2)) else None
            if t is not None:
                accel_days = _cap(t * days_per_period)
                if accel_days is not None:
                    return min(linear_days, accel_days)
    return linear_days


def _top_driver(avg_a: float, avg_p: float, avg_q: float) -> str:
    components = {"availability": avg_a, "performance": avg_p, "quality": avg_q}
    return min(components, key=components.get)


def _build_daily(
    df: pd.DataFrame, *, date_col: str, machine_col: str, planned_time_col: str, operating_time_col: str,
    total_units_col: str, good_units_col: str, target_rate: float | None, downtime_category_col: str | None,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Per-machine daily OEE aggregation, extracted from the old analyze()
    unchanged - shared by the AutoML template's preprocess()/postprocess()
    (app/services/automl_templates/predictive_maintenance.py). Returns
    (grouped, daily_ml, days_per_period): `grouped` keeps the original
    machine_col/date_col names (for postprocess()'s per-machine loop),
    `daily_ml` is renamed to machine/date (for build_training_frame)."""
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).sort_values(date_col)
    for col in (planned_time_col, operating_time_col, total_units_col, good_units_col):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    grouped = (
        df.groupby([machine_col, pd.Grouper(key=date_col, freq="D")])
        .agg(planned=(planned_time_col, "sum"), operating=(operating_time_col, "sum"),
             total=(total_units_col, "sum"), good=(good_units_col, "sum"))
        .reset_index()
    )
    grouped = grouped[grouped["planned"] > 0]

    if target_rate and target_rate > 0:
        rate = target_rate
    else:
        rates = grouped.apply(lambda r: r["total"] / r["operating"] if r["operating"] > 0 else 0, axis=1)
        rate = float(rates.quantile(0.95)) or 1.0

    def _oee_row(r):
        avail = min(r["operating"] / r["planned"], 1.0) if r["planned"] > 0 else 0.0
        perf = min(r["total"] / (r["operating"] * rate), 1.0) if r["operating"] > 0 else 0.0
        qual = min(r["good"] / r["total"], 1.0) if r["total"] > 0 else 0.0
        return avail, perf, qual, avail * perf * qual

    grouped[["avail", "perf", "qual", "oee"]] = grouped.apply(lambda r: pd.Series(_oee_row(r)), axis=1)

    date_range_days = max((df[date_col].max() - df[date_col].min()).days, 1)
    n_periods = max(grouped[date_col].nunique(), 1)
    days_per_period = date_range_days / n_periods

    daily_ml = grouped.rename(columns={machine_col: "machine", date_col: "date"}).copy()
    daily_ml["scrap_rate"] = (1 - daily_ml["qual"]).clip(0, 1)
    daily_ml["dt_rate"] = (1 - daily_ml["avail"]).clip(0, 1)

    if downtime_category_col and downtime_category_col in df.columns:
        fail_agg = (
            df.assign(_f=df[downtime_category_col].isin(_UNPLANNED_CATS).astype(int))
            .groupby([machine_col, pd.Grouper(key=date_col, freq="D")])["_f"]
            .max().reset_index().rename(columns={machine_col: "machine", date_col: "date", "_f": "is_failure"})
        )
        daily_ml = daily_ml.merge(fail_agg, on=["machine", "date"], how="left")
        daily_ml["is_failure"] = daily_ml["is_failure"].fillna(0).astype(int)
    else:
        daily_ml["is_failure"] = 0

    return grouped, daily_ml, days_per_period


# ─── Maintenance schedule CRUD ──────────────────────────────────────────────

async def list_schedules(db: AsyncSession, org_id: uuid.UUID) -> list[MaintenanceSchedule]:
    result = await db.execute(
        select(MaintenanceSchedule).where(MaintenanceSchedule.org_id == org_id).order_by(MaintenanceSchedule.created_at.desc())
    )
    return list(result.scalars().all())


async def create_schedule(db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, **fields) -> MaintenanceSchedule:
    item = MaintenanceSchedule(org_id=org_id, created_by=created_by, **fields)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def bulk_create_schedules(db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, items: list[dict]) -> list[MaintenanceSchedule]:
    rows = [MaintenanceSchedule(org_id=org_id, created_by=created_by, **item) for item in items]
    db.add_all(rows)
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows


async def get_schedule_or_404(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> MaintenanceSchedule:
    result = await db.execute(
        select(MaintenanceSchedule).where(MaintenanceSchedule.org_id == org_id, MaintenanceSchedule.id == schedule_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise ValueError("Maintenance schedule item not found")
    return item


async def update_schedule(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID, patch: dict) -> MaintenanceSchedule:
    item = await get_schedule_or_404(db, org_id, schedule_id)
    for key, value in patch.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


async def delete_schedule(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> None:
    item = await get_schedule_or_404(db, org_id, schedule_id)
    await db.delete(item)
    await db.commit()
