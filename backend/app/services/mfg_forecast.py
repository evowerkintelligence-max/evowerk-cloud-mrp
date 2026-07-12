"""Manufacturing-aware forecasting: auto-engineers 12 time-series features
(lags, rolling stats, trend, calendar/seasonality), trains a TimeSeriesSplit-
validated GradientBoostingRegressor, projects a recursive multi-step forecast
with bootstrap-ensemble confidence intervals that honestly widen with the
horizon, and translates feature importances into plain-English prescriptive
insights. Ported near-verbatim from the desktop app's mfg_forecast.py +
forecast_features.py (dataset-agnostic pandas/numpy/sklearn math).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_percentage_error, r2_score
from sklearn.model_selection import TimeSeriesSplit

from app.models.dataset import Dataset
from app.services import datasets as ds_service

N_LAGS = 14

FEATURE_NAMES: list[str] = [
    "Yesterday's value (lag-1)", "2 days ago (lag-2)", "3 days ago (lag-3)",
    "Same day last week (lag-7)", "Same day 2 weeks ago (lag-14)",
    "7-day rolling average", "14-day rolling average", "7-day volatility (std)",
    "7-day trend slope", "Day of week", "Month of year", "Seasonal component",
]

_PRESCRIPTIVE_MAP = [
    ("Yesterday", "Daily Consistency Is Everything", "positive", "📈",
     "Yesterday's {t} is the single strongest predictor of today's. A single bad shift doesn't just cost "
     "that shift - it pulls down the next day's baseline. Protect daily floors, not just weekly averages."),
    ("last week", "Same-Day-Last-Week Pattern", "positive", "📅",
     "{t} shows strong week-over-week repeat patterns. Standardise procedures for the same shift/day each "
     "week - whatever happens on Mondays tends to repeat on the following Monday."),
    ("7-day rolling average", "7-Day Trajectory Drives Forecast", "positive", "📊",
     "The 7-day rolling average is a key predictor. Sustained performance over a week compounds positively "
     "- a rising average now predicts a higher {t} next week. Avoid scheduling disruptive work mid-streak."),
    ("14-day rolling average", "2-Week Baseline Momentum", "positive", "🏭",
     "The 2-week average baseline is highly predictive. Structural changes - new tooling, operator "
     "training, process updates - show up here first. Improvements take ~2 weeks to fully register."),
    ("volatility", "Stability Reduces Forecast Error", "negative", "⚡",
     "High day-to-day variability in {t} degrades forecast accuracy and suppresses the average. "
     "Reducing variance between best and worst shifts is a high-leverage action."),
    ("trend slope", "Current Trajectory Has Momentum", "positive", "📉",
     "The direction of the last 7 days carries significant forward momentum. A declining trend now "
     "predicts further decline next week - and vice versa. Arrest downward trends early."),
    ("Day of week", "Day-of-Week Effect Detected", "negative", "🗓️",
     "Specific weekdays show systematically different {t}. Investigate shift handoffs and staffing "
     "patterns, and whether Monday startups or Friday end-of-week rushes are driving the gap."),
    ("Month", "Monthly / Seasonal Patterns", "neutral", "🌡️",
     "Month-of-year patterns affect {t} - likely seasonal demand, temperature effects on equipment, or "
     "holiday staffing. Plan preventive maintenance before historically weak months."),
    ("Seasonal", "Intra-Year Seasonality", "neutral", "🌡️",
     "Smooth seasonal cycles affect {t} throughout the year. Plan buffer capacity and maintenance windows "
     "around the seasonal low points the model has detected."),
]

_Z975 = 1.959963984540054  # standard normal 97.5th percentile


@dataclass
class ForecastPoint:
    date: str
    actual: float | None
    predicted: float
    lower: float
    upper: float


@dataclass
class PrescriptiveInsight:
    rank: int
    label: str
    detail: str
    importance_pct: float
    direction: str
    icon: str


@dataclass
class ForecastResult:
    target_col: str
    horizon: int
    mape_pct: float
    r2: float
    n_training_points: int
    series: list[ForecastPoint]
    prescriptive: list[PrescriptiveInsight]
    summary: str
    machines: list[str] = field(default_factory=list)


def _feature_row(values: list[float], i: int, date_str: str) -> list[float] | None:
    if i < N_LAGS:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None

    def roll_mean(w: int) -> float:
        return statistics.mean(values[i - w:i])

    def roll_std(w: int) -> float:
        window = values[i - w:i]
        return statistics.stdev(window) if len(window) > 1 else 0.0

    dow, month = float(dt.weekday()), float(dt.month)
    seasonality = math.sin(2 * math.pi * dt.timetuple().tm_yday / 365.0)
    return [
        values[i - 1], values[i - 2], values[i - 3], values[i - 7], values[i - 14],
        roll_mean(7), roll_mean(14), roll_std(7),
        (values[i - 1] - values[i - 7]) / 7.0,
        dow, month, seasonality,
    ]


def _engineer_single_future(recent: list[float], date_str: str) -> list[float]:
    while len(recent) < N_LAGS:
        recent.insert(0, recent[0] if recent else 0.0)
    vals = recent[-N_LAGS:]
    feat = _feature_row(vals + [0.0], N_LAGS, date_str)
    return feat if feat is not None else [recent[-1]] * 12


def _aggregate_daily(df: pd.DataFrame, date_col: str, target_col: str, aggregate: str) -> pd.DataFrame:
    dates = pd.to_datetime(df[date_col], errors="coerce")
    values = pd.to_numeric(df[target_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    daily = pd.DataFrame({"date": dates, "value": values}).dropna()
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    agg_fn = "sum" if aggregate == "sum" else "mean"
    return daily.groupby("date", as_index=False)["value"].agg(agg_fn).sort_values("date")


def _generate_prescriptive(importances: np.ndarray, target_col: str) -> list[PrescriptiveInsight]:
    total = float(importances.sum()) or 1.0
    ranked = sorted(enumerate(FEATURE_NAMES), key=lambda x: importances[x[0]], reverse=True)

    insights: list[PrescriptiveInsight] = []
    seen_labels: set[str] = set()
    for feat_idx, feat_name in ranked:
        pct = round(float(importances[feat_idx]) / total * 100, 1)
        if pct < 2.0:
            break
        label, direction, icon, detail = "Key Driver", "positive", "🔧", f"This feature strongly influences {target_col} predictions."
        for substr, lbl, dir_, icn, tmpl in _PRESCRIPTIVE_MAP:
            if substr.lower() in feat_name.lower():
                label, direction, icon, detail = lbl, dir_, icn, tmpl.format(t=target_col)
                break
        if label in seen_labels:
            continue
        seen_labels.add(label)
        insights.append(PrescriptiveInsight(rank=len(insights) + 1, label=label, detail=detail, importance_pct=pct, direction=direction, icon=icon))
        if len(insights) >= 5:
            break
    return insights


def run_forecast(
    dataset: Dataset, *, date_col: str, target_col: str, machine_col: str | None,
    machine_filter: str | None, horizon: int, aggregate: str,
) -> ForecastResult:
    ds_service.validate_columns(dataset, [date_col, target_col, machine_col])
    df = ds_service.dataframe_for(dataset)

    machines: list[str] = []
    if machine_col and machine_col in df.columns:
        machines = sorted({str(v).strip() for v in df[machine_col].dropna() if str(v).strip()})
        if machine_filter:
            df = df[df[machine_col].astype(str).str.strip() == machine_filter]

    daily = _aggregate_daily(df, date_col, target_col, aggregate)
    if len(daily) < 30:
        raise ValueError(f"Need at least 30 daily data points after aggregation (got {len(daily)}). Try a different date column or aggregate method.")

    values = daily["value"].tolist()
    dates = daily["date"].tolist()

    X_list, y_list = [], []
    for i in range(len(values)):
        feat = _feature_row(values, i, dates[i])
        if feat is not None:
            X_list.append(feat)
            y_list.append(values[i])
    if len(X_list) < 20:
        raise ValueError("Not enough data after feature engineering (need 20+ points)")

    X, y = np.array(X_list, dtype=float), np.array(y_list, dtype=float)

    tscv = TimeSeriesSplit(n_splits=4)
    cv_preds, cv_actuals = [], []
    for tr_idx, te_idx in tscv.split(X):
        if len(tr_idx) < 10:
            continue
        m = GradientBoostingRegressor(n_estimators=150, max_depth=4, learning_rate=0.08, subsample=0.85, min_samples_leaf=3, random_state=42)
        m.fit(X[tr_idx], y[tr_idx])
        cv_preds.extend(m.predict(X[te_idx]).tolist())
        cv_actuals.extend(y[te_idx].tolist())

    mape = round(mean_absolute_percentage_error(cv_actuals, cv_preds) * 100, 2) if cv_preds else 0.0
    r2 = round(r2_score(cv_actuals, cv_preds), 3) if cv_preds else 0.0
    cv_residual_std = float(np.std(np.array(cv_actuals) - np.array(cv_preds), ddof=1)) if len(cv_preds) >= 2 else (float(np.std(y, ddof=1)) if len(y) >= 2 else 0.0)

    model = GradientBoostingRegressor(n_estimators=200, max_depth=4, learning_rate=0.07, subsample=0.85, min_samples_leaf=3, random_state=42)
    model.fit(X, y)

    boot_models = []
    for seed in range(25):
        rng = np.random.RandomState(seed * 13)
        idx = np.sort(rng.choice(len(X), size=len(X), replace=True))
        bm = GradientBoostingRegressor(n_estimators=100, max_depth=4, learning_rate=0.1, subsample=0.85, min_samples_leaf=3, random_state=seed)
        bm.fit(X[idx], y[idx])
        boot_models.append(bm)

    def _predict_with_ci(x_row: np.ndarray) -> tuple[float, float, float]:
        pred = float(model.predict(x_row)[0])
        return pred, pred - _Z975 * cv_residual_std, pred + _Z975 * cv_residual_std

    series: list[ForecastPoint] = []
    hist_start = max(0, len(dates) - 90)
    x_offset = len(dates) - len(X_list)  # X_list starts at first valid feature row (i >= N_LAGS)
    for i in range(hist_start, len(dates)):
        xi_idx = i - x_offset
        if xi_idx < 0:
            continue
        pred, lo, hi = _predict_with_ci(X[xi_idx].reshape(1, -1))
        series.append(ForecastPoint(date=dates[i], actual=round(float(y[xi_idx]), 3), predicted=round(pred, 3), lower=round(lo, 3), upper=round(hi, 3)))

    main_buf = list(values[-N_LAGS:])
    boot_bufs = [list(values[-N_LAGS:]) for _ in boot_models]
    last_dt = datetime.strptime(dates[-1], "%Y-%m-%d")

    def _roll(buf: list, v: float) -> None:
        buf.append(v)
        if len(buf) > N_LAGS + 5:
            buf.pop(0)

    for h in range(1, horizon + 1):
        future_date = (last_dt + timedelta(days=h)).strftime("%Y-%m-%d")
        pred = float(model.predict(np.array([_engineer_single_future(list(main_buf), future_date)], dtype=float))[0])

        bpred: list[float] = []
        for bm, buf in zip(boot_models, boot_bufs):
            yb = float(bm.predict(np.array([_engineer_single_future(list(buf), future_date)], dtype=float))[0])
            bpred.append(yb)
            _roll(buf, yb)

        lo, hi = float(np.percentile(bpred, 2.5)), float(np.percentile(bpred, 97.5))
        lo, hi = min(lo, pred), max(hi, pred)
        min_half_width = _Z975 * cv_residual_std * math.sqrt(h)
        half_width = max((hi - lo) / 2.0, min_half_width)
        lo, hi = pred - half_width, pred + half_width

        _roll(main_buf, pred)
        series.append(ForecastPoint(date=future_date, actual=None, predicted=round(pred, 3), lower=round(lo, 3), upper=round(hi, 3)))

    prescriptive = _generate_prescriptive(model.feature_importances_, target_col)

    last_actual = round(float(values[-1]), 3)
    future_preds = [p.predicted for p in series if p.actual is None]
    future_avg = round(float(np.mean(future_preds)), 3) if future_preds else last_actual
    direction = "improve" if future_avg > last_actual else "decline slightly"
    top_driver = prescriptive[0].label if prescriptive else "recent trends"
    summary = (
        f"Model trained on {len(y)} daily data points (cross-validated MAPE {mape}%, R² {r2}). "
        f"Over the next {horizon} days, {target_col} is projected to {direction} "
        f"(current {last_actual} → avg forecast {future_avg}). Top driver: {top_driver}."
    )

    return ForecastResult(
        target_col=target_col, horizon=horizon, mape_pct=mape, r2=r2, n_training_points=len(y),
        series=series, prescriptive=prescriptive, summary=summary, machines=machines,
    )
