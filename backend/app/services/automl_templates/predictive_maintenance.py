"""Predictive Maintenance AutoML template: re-platforms predictive.py's
old hardcoded GradientBoostingClassifier onto the shared automl_engine
bake-off, while keeping its RUL/cost/recommendation math and statistical
fallback for machines with too little history to train on.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.services import predictive as legacy
from app.services.automl_templates import ColumnRole, PreprocessResult

_W, _H, _MIN_ROWS = 7, 3, 40


def _days_since(fail_arr: np.ndarray, up_to: int) -> float:
    idxs = np.where(fail_arr[:up_to] == 1)[0]
    return float(up_to - idxs[-1]) if len(idxs) > 0 else 60.0


def build_training_frame(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[float]]]:
    """Same rolling-window featurization as the old _ml_analyze, ported to
    produce a flat DataFrame for automl_engine instead of numpy arrays fed
    directly to a hardcoded model. `daily` must have columns machine, date,
    oee, scrap_rate, dt_rate, is_failure (sorted per-machine by date is not
    required - this function sorts internally)."""
    rows: list[dict[str, float]] = []
    last_x: dict[str, list[float]] = {}

    for machine, mdf in daily.groupby("machine"):
        mdf = mdf.sort_values("date").reset_index(drop=True)
        n = len(mdf)
        if n < _W + _H + 1:
            continue

        oee = mdf["oee"].to_numpy(dtype=float)
        scr = mdf["scrap_rate"].to_numpy(dtype=float)
        dtr = mdf["dt_rate"].to_numpy(dtype=float)
        fail = mdf["is_failure"].to_numpy(dtype=int)

        for i in range(_W, n - _H):
            ow = oee[i - _W:i]
            rows.append({
                "oee_7d": float(np.mean(ow)),
                "oee_slope_7d": legacy._linreg_slope(ow.tolist()),
                "scrap_rate_7d": float(np.mean(scr[i - _W:i])),
                "dt_rate_7d": float(np.mean(dtr[i - _W:i])),
                "fail_count_7d": float(np.sum(fail[i - _W:i])),
                "days_since_fail": _days_since(fail, i),
                "is_failure_next": 1 if np.sum(fail[i:i + _H]) > 0 else 0,
            })

        ow = oee[n - _W:n]
        last_x[str(machine)] = [
            float(np.mean(ow)), legacy._linreg_slope(ow.tolist()),
            float(np.mean(scr[n - _W:n])), float(np.mean(dtr[n - _W:n])),
            float(np.sum(fail[n - _W:n])), _days_since(fail, n),
        ]

    columns = ["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail", "is_failure_next"]
    training_df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    return training_df, last_x


def has_ml_signal(training_df: pd.DataFrame, min_rows: int = _MIN_ROWS) -> bool:
    if len(training_df) < min_rows:
        return False
    pos_rate = float(training_df["is_failure_next"].mean())
    return 0.05 <= pos_rate <= 0.90


ROLES = [
    ColumnRole("date_col", "Date column"),
    ColumnRole("machine_col", "Machine column"),
    ColumnRole("planned_time_col", "Planned time"),
    ColumnRole("operating_time_col", "Operating time"),
    ColumnRole("total_units_col", "Total units"),
    ColumnRole("good_units_col", "Good units"),
    ColumnRole("failure_col", "Failure flag column", required=False),
    ColumnRole("downtime_category_col", "Downtime category", required=False),
]

# Excludes SVC/LinearSVC - automl_engine's SVC has no probability=True, so it
# has no .predict_proba(), which postprocess() needs for a risk score, not
# just a class label.
_SAFE_ALGORITHMS = [
    "LogisticRegression", "RandomForestClassifier", "ExtraTreesClassifier",
    "HistGradientBoostingClassifier", "GradientBoostingClassifier", "KNeighborsClassifier",
    "XGBoostClassifier", "LightGBMClassifier",
]


def preprocess(df: pd.DataFrame, config: dict[str, Any]) -> PreprocessResult:
    cols = config.get("column_map", {})
    required = ["date_col", "machine_col", "planned_time_col", "operating_time_col", "total_units_col", "good_units_col"]
    missing_roles = [r for r in required if not cols.get(r)]
    if missing_roles:
        raise ValueError(f"Missing required column mapping for: {', '.join(missing_roles)}")
    for role_key, col_name in cols.items():
        if col_name and col_name not in df.columns:
            raise ValueError(f"Mapped column '{col_name}' (role '{role_key}') not found in dataset")

    grouped, daily_ml, days_per_period = legacy._build_daily(
        df, date_col=cols["date_col"], machine_col=cols["machine_col"],
        planned_time_col=cols["planned_time_col"], operating_time_col=cols["operating_time_col"],
        total_units_col=cols["total_units_col"], good_units_col=cols["good_units_col"],
        target_rate=config.get("target_rate"), downtime_category_col=cols.get("downtime_category_col"),
    )

    training_df, last_x = build_training_frame(daily_ml)

    # Raw per-row df, coerced/sorted the same way legacy analyze() does before
    # its last_fail_ago lookback (predictive.py:438-439), so postprocess() can
    # reproduce that lookback's failure_col/downtime_category_col preference
    # order exactly - `grouped`/`daily_ml` have already aggregated those raw
    # columns away.
    df_ready = df.copy()
    df_ready[cols["date_col"]] = pd.to_datetime(df_ready[cols["date_col"]], errors="coerce")
    df_ready = df_ready.dropna(subset=[cols["date_col"]]).sort_values(cols["date_col"])

    context = {
        "grouped": grouped, "daily_ml": daily_ml, "days_per_period": days_per_period,
        "last_x": last_x, "column_map": cols, "hourly_value": config.get("hourly_value", 1000.0),
        "n_training_rows": len(training_df), "df": df_ready,
        "failure_col": cols.get("failure_col"), "downtime_category_col": cols.get("downtime_category_col"),
    }

    if not has_ml_signal(training_df):
        return PreprocessResult(
            skip_ml=True,
            notes=[f"Not enough rolling-window history to train a classifier ({len(training_df)} usable "
                   f"windows, need >= {_MIN_ROWS} with a 5-90% failure rate) - using the statistical risk model instead."],
            context=context,
        )

    return PreprocessResult(
        skip_ml=False,
        df=training_df,
        target_col="is_failure_next",
        task="classification",
        feature_cols=["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"],
        algorithms=_SAFE_ALGORITHMS,
        notes=[f"Trained on {len(training_df)} rolling 7-day windows across all machines."],
        context=context,
    )


def _machine_risk_from_prob(
    machine: str, fail_prob: float, ml_powered: bool, drivers: list[dict],
    oee_s: list[float], avail_s: list[float], perf_s: list[float], qual_s: list[float],
    last_fail_ago: float | None, days_per_period: float,
) -> tuple[dict, str]:
    current_oee = oee_s[-1]
    slope = legacy._recent_slope(oee_s)
    avg_a, avg_p, avg_q = sum(avail_s) / len(avail_s), sum(perf_s) / len(perf_s), sum(qual_s) / len(qual_s)
    driver = legacy._top_driver(avg_a, avg_p, avg_q)

    accel = 0.0
    if len(oee_s) >= 14:
        accel = legacy._linreg_slope(oee_s[-7:]) - legacy._linreg_slope(oee_s[-14:-7])
    rul = legacy._rul_days(current_oee, slope, days_per_period, accel=accel)

    if fail_prob >= 0.65 or current_oee < legacy._CRITICAL_OEE:
        risk = "high"
    elif fail_prob >= 0.35 or current_oee < legacy._WARN_OEE:
        risk = "medium"
    else:
        risk = "low"

    return {
        "machine": machine, "current_oee": round(current_oee, 4), "trend_slope": round(slope, 5),
        "failure_probability": round(fail_prob, 3), "risk_level": risk, "top_driver": driver, "rul_days": rul,
        "last_failure_days_ago": last_fail_ago, "ml_powered": ml_powered, "ml_drivers": drivers,
    }, risk


def postprocess(engine_result: dict[str, Any] | None, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    grouped = context["grouped"]
    days_per_period = context["days_per_period"]
    hourly_value = float(context.get("hourly_value") or 1000.0)
    cols = context["column_map"]
    machine_col, date_col = cols["machine_col"], cols["date_col"]

    pipeline = engine_result["pipeline"] if engine_result else None
    model_meta_dict: dict[str, Any] | None = None
    if engine_result:
        best_scores = engine_result["meta"]["scores"]
        model_meta_dict = {
            "algorithm": engine_result["meta"]["model_name"],
            "accuracy_pct": round(float(best_scores.get("accuracy", 0.0)) * 100, 1),
            "n_training_samples": context.get("n_training_rows", 0),
            "top_features": [],  # filled in by Plan 2's explainability layer
        }

    machines_out: list[dict] = []
    recs_out: list[dict] = []

    # Fleet-relative baseline, computed once from every machine's latest OEE -
    # matches legacy analyze() (predictive.py:444-450), which builds this
    # before the per-machine loop and passes the SAME value into every
    # machine's _stat_analyze() call (not each machine's own history).
    fleet_last_oee: list[float] = []
    for _, fmdf in grouped.groupby(machine_col):
        fvals = fmdf.sort_values(date_col)["oee"].tolist()
        if fvals:
            fleet_last_oee.append(fvals[-1])
    fleet_last_oee.sort()
    fleet_oee_median = fleet_last_oee[len(fleet_last_oee) // 2] if fleet_last_oee else 0.0

    raw_df = context["df"]
    failure_col = context.get("failure_col")
    downtime_category_col = context.get("downtime_category_col")

    for machine, mdf in grouped.groupby(machine_col):
        mdf = mdf.sort_values(date_col)
        oee_s, avail_s, perf_s, qual_s = mdf["oee"].tolist(), mdf["avail"].tolist(), mdf["perf"].tolist(), mdf["qual"].tolist()
        if not oee_s:
            continue
        machine_key = str(machine)

        # Dual-source last-failure lookback on the raw per-row df, preferring
        # failure_col over downtime_category_col - matches legacy analyze()
        # exactly (predictive.py:466-477). `grouped`/`daily_ml` aggregate the
        # raw failure_col away, so this must read context["df"], not daily_ml.
        last_fail_ago: float | None = None
        machine_max_date = mdf[date_col].max()
        if failure_col and failure_col in raw_df.columns:
            mfail_df = raw_df[raw_df[machine_col] == machine].copy()
            mfail_df[failure_col] = pd.to_numeric(mfail_df[failure_col], errors="coerce").fillna(0)
            mfail = mfail_df[mfail_df[failure_col] > 0]
            if not mfail.empty:
                last_fail_ago = float((machine_max_date - mfail[date_col].max()).days)
        elif downtime_category_col and downtime_category_col in raw_df.columns:
            mfail = raw_df[(raw_df[machine_col] == machine) & raw_df[downtime_category_col].isin(legacy._UNPLANNED_CATS)]
            if not mfail.empty:
                last_fail_ago = float((machine_max_date - mfail[date_col].max()).days)

        if pipeline is not None and machine_key in context["last_x"]:
            x_row = context["last_x"][machine_key]
            feature_cols = ["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"]
            x_df = pd.DataFrame([dict(zip(feature_cols, x_row))])
            fail_prob = min(float(pipeline.predict_proba(x_df)[0][1]), 0.99)
            ml_powered = True
            drivers: list[dict] = []
        else:
            fail_prob, driver_contribs = legacy._stat_analyze(
                oee_s, avail_s, perf_s, qual_s, last_fail_ago,
                fleet_oee_median=fleet_oee_median,
            )
            ml_powered = False
            drivers = driver_contribs

        machine_dict, risk = _machine_risk_from_prob(
            machine_key, fail_prob, ml_powered, drivers, oee_s, avail_s, perf_s, qual_s, last_fail_ago, days_per_period,
        )
        machines_out.append(machine_dict)

        priority = {"high": "immediate", "medium": "scheduled", "low": "monitor"}[risk]
        action, reason = legacy._ACTIONS.get(machine_dict["top_driver"], ("General inspection recommended", "Multiple metrics declining"))
        pm_hours = legacy._PM_HOURS[priority]
        avoided_h = round(fail_prob * 48.0, 1)
        net_saving = round((avoided_h - pm_hours) * hourly_value, 2)
        recs_out.append({
            "machine": machine_key, "priority": priority, "action": action, "reason": reason,
            "estimated_pm_hours": pm_hours, "avoided_downtime_hours": avoided_h, "net_saving": net_saving,
        })

    risk_order = {"high": 0, "medium": 1, "low": 2}
    machines_out.sort(key=lambda m: (risk_order.get(m["risk_level"], 3), -m["failure_probability"]))
    recs_out.sort(key=lambda r: {"immediate": 0, "scheduled": 1, "monitor": 2}.get(r["priority"], 3))

    at_risk = sum(1 for m in machines_out if m["risk_level"] in ("high", "medium"))
    total_avoided = sum(r["avoided_downtime_hours"] * hourly_value for r in recs_out)
    total_pm = sum(r["estimated_pm_hours"] * hourly_value for r in recs_out)
    net_savings = round(total_avoided - total_pm, 2)
    roi_pct = round((net_savings / total_pm * 100) if total_pm > 0 else 0.0, 1)

    predictive_result = {
        "machines": machines_out,
        "recommendations": recs_out,
        "cost_summary": {
            "total_avoided_cost": round(total_avoided, 2), "total_pm_cost": round(total_pm, 2),
            "net_savings": net_savings, "roi_pct": roi_pct, "machines_at_risk": at_risk,
        },
        "model_meta": model_meta_dict,
    }

    return {
        "predictive_result": predictive_result,
        "pipeline": pipeline,
        "explain_X": engine_result["explain_X"] if engine_result else None,
        "explain_y": engine_result["explain_y"] if engine_result else None,
        "leaderboard": engine_result["leaderboard"] if engine_result else [],
        "best_model_name": engine_result["meta"]["model_name"] if engine_result else None,
        "metric_name": engine_result["meta"]["metric_name"] if engine_result else None,
        "metric_value": engine_result["meta"]["metric_value"] if engine_result else None,
    }
