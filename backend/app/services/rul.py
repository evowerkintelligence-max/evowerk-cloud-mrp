"""RUL via Weibull survival analysis, calibrated against real breakdown
history (DowntimeIncident where reason == "breakdown"). Refit is lazy,
on the first read each day - this codebase has no job scheduler at all
(confirmed: no APScheduler/celery/cron, and app.main's lifespan only resumes
realtime streams and AutoML bindings), and already has a documented pattern
for exactly this problem: app.services.morning_brief's
_ensure_todays_snapshot. This mirrors that pattern rather than adding a new
scheduling mechanism."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from lifelines import WeibullFitter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import ensure_utc
from app.models.downtime_tracking import DowntimeIncident
from app.models.sensor import WeibullFit

_MIN_FAILURES_PER_MACHINE = 5
_MIN_FAILURES_FLEET = 5
_RUL_HORIZON_DAYS = 30


async def _breakdown_durations(db: AsyncSession, org_id: uuid.UUID, machine: str | None) -> tuple[list[float], list[bool]]:
    """Returns (durations_in_days, event_observed) pairs for lifelines:
    event_observed=True for a completed inter-breakdown interval,
    False (right-censored) for the still-open interval since the last
    breakdown (or since the earliest record if there's been none)."""
    query = select(DowntimeIncident).where(
        DowntimeIncident.org_id == org_id, DowntimeIncident.reason == "breakdown",
    )
    if machine is not None:
        query = query.where(DowntimeIncident.machine == machine)
    incidents = (await db.execute(query.order_by(DowntimeIncident.started_at))).scalars().all()

    if not incidents:
        return [], []

    durations: list[float] = []
    observed: list[bool] = []
    # SQLite (used in tests) drops tzinfo on reload even for a
    # DateTime(timezone=True) column - ensure_utc is this codebase's existing
    # fix (see app.core.time, already used by downtime_tracking.py for the
    # same started_at/ended_at subtraction on this same model).
    prev = ensure_utc(incidents[0].started_at)
    for incident in incidents[1:]:
        started_at = ensure_utc(incident.started_at)
        gap_days = (started_at - prev).total_seconds() / 86400.0
        durations.append(max(gap_days, 0.01))
        observed.append(True)
        prev = started_at

    now = datetime.now(timezone.utc)
    open_gap_days = (now - prev).total_seconds() / 86400.0
    durations.append(max(open_gap_days, 0.01))
    observed.append(False)  # right-censored: still running since the last breakdown

    return durations, observed


def _fit_weibull(durations: list[float], observed: list[bool]) -> tuple[float, float]:
    fitter = WeibullFitter()
    fitter.fit(durations, event_observed=observed)
    return float(fitter.rho_), float(fitter.lambda_)  # rho_ = shape k, lambda_ = scale


async def _compute_fit(db: AsyncSession, org_id: uuid.UUID, machine: str) -> tuple[float | None, float | None, int, int, str]:
    durations, observed = await _breakdown_durations(db, org_id, machine)
    n_failures = sum(observed)
    n_censored = len(observed) - n_failures

    if n_failures >= _MIN_FAILURES_PER_MACHINE:
        shape_k, scale_lambda = _fit_weibull(durations, observed)
        return shape_k, scale_lambda, n_failures, n_censored, "per_machine"

    fleet_durations, fleet_observed = await _breakdown_durations(db, org_id, None)
    fleet_failures = sum(fleet_observed)
    fleet_censored = len(fleet_observed) - fleet_failures
    if fleet_failures >= _MIN_FAILURES_FLEET:
        shape_k, scale_lambda = _fit_weibull(fleet_durations, fleet_observed)
        return shape_k, scale_lambda, fleet_failures, fleet_censored, "fleet_pooled"

    return None, None, n_failures, n_censored, "insufficient_data"


async def days_since_last_breakdown(db: AsyncSession, org_id: uuid.UUID, machine: str) -> float:
    """Elapsed time since this machine's last breakdown - the "current age" t
    that failure_probability_30d must condition survival on. 0.0 (unconditioned)
    when there's no breakdown history yet for this machine to measure age from."""
    result = await db.execute(
        select(DowntimeIncident.started_at)
        .where(
            DowntimeIncident.org_id == org_id,
            DowntimeIncident.machine == machine,
            DowntimeIncident.reason == "breakdown",
        )
        .order_by(DowntimeIncident.started_at.desc())
        .limit(1)
    )
    started_at = result.scalar_one_or_none()
    if started_at is None:
        return 0.0
    started_at = ensure_utc(started_at)
    return max((datetime.now(timezone.utc) - started_at).total_seconds() / 86400.0, 0.0)


async def get_or_refit(db: AsyncSession, org_id: uuid.UUID, machine: str) -> WeibullFit:
    today = datetime.now(timezone.utc).date()
    existing = (
        await db.execute(select(WeibullFit).where(WeibullFit.org_id == org_id, WeibullFit.machine == machine))
    ).scalar_one_or_none()
    if existing is not None and existing.fit_at.date() == today:
        return existing

    shape_k, scale_lambda, n_failures, n_censored, tier = await _compute_fit(db, org_id, machine)

    if existing is not None:
        existing.shape_k = shape_k
        existing.scale_lambda = scale_lambda
        existing.n_failures = n_failures
        existing.n_censored = n_censored
        existing.fit_tier = tier
        existing.fit_at = datetime.now(timezone.utc)
        await db.commit()
        return existing

    fit = WeibullFit(
        org_id=org_id, machine=machine, shape_k=shape_k, scale_lambda=scale_lambda,
        n_failures=n_failures, n_censored=n_censored, fit_tier=tier,
    )
    try:
        async with db.begin_nested():
            db.add(fit)
        await db.commit()
        return fit
    except IntegrityError:
        # A concurrent request refit this machine first - no scheduler
        # serializes these, same race the morning_brief pattern handles.
        await db.rollback()
        return (
            await db.execute(select(WeibullFit).where(WeibullFit.org_id == org_id, WeibullFit.machine == machine))
        ).scalar_one()


def _weibull_survival(t: float, k: float, lam: float) -> float:
    """S(t) = exp(-(t/lam)**k), clamped to 0.0 when the exponent overflows
    float range. That happens for near-uniform observed durations (e.g. a
    machine that breaks down on an almost-fixed cadence), where the MLE
    shape k blows up towards a near-deterministic failure time and Python's
    float ** float raises OverflowError instead of saturating to inf."""
    if lam <= 0:
        return 1.0
    try:
        exponent = (t / lam) ** k
    except OverflowError:
        return 0.0
    try:
        return math.exp(-exponent)
    except OverflowError:
        return 0.0


def failure_probability_30d(fit: WeibullFit, t: float = 0.0) -> float | None:
    """Probability this machine has a breakdown within the next 30 days,
    conditioned on `t` - days already run since its last breakdown (get this
    from days_since_last_breakdown()). None when the fit is insufficient_data
    - never fabricate a number with no calibration behind it."""
    if fit.shape_k is None or fit.scale_lambda is None:
        return None
    k, lam = fit.shape_k, fit.scale_lambda
    survival_now = _weibull_survival(t, k, lam)
    survival_30 = _weibull_survival(t + _RUL_HORIZON_DAYS, k, lam)
    if survival_now <= 0:
        return 1.0
    return round(1.0 - (survival_30 / survival_now), 4)
