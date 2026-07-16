"""Quality analytics: defect-rate/DPU summary, worst batches, feature
correlations, trend, and clustering of "what makes a bad batch different".

Runs over an existing uploaded Dataset with user-supplied column mappings.
Ported from the desktop app's backend/quality_engine.py; the file-format
reader is replaced by app.services.datasets.dataframe_for (DuckDB-backed,
consistent with every other dataset consumer in this app).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.analytics import spc_calc
from app.models.dataset import Dataset
from app.services.datasets import dataframe_for

logger = logging.getLogger(__name__)

_HAS_SKLEARN = False
try:
    from sklearn.cluster import KMeans
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    _HAS_SKLEARN = True
except Exception:
    pass


def _sigma_level(dpmo: float) -> float:
    """Six Sigma process-sigma from DPMO, with the conventional 1.5-sigma
    long-term shift baked in."""
    from scipy.stats import norm
    yield_ = max(1e-9, min(1 - 1e-9, 1 - dpmo / 1_000_000))
    return round(float(norm.ppf(yield_)) + 1.5, 2)


def _label_cluster(defect_rate: float, avg_rate: float) -> str:
    if defect_rate > avg_rate * 2:
        return "High-defect"
    if defect_rate > avg_rate * 1.2:
        return "Above-average"
    if defect_rate < avg_rate * 0.5:
        return "Low-defect"
    return "Normal"


def analyze_quality(
    dataset: Dataset,
    defect_column: str,
    batch_column: Optional[str] = None,
    timestamp_column: Optional[str] = None,
    feature_columns: Optional[List[str]] = None,
    n_clusters: int = 3,
    max_rows: int = 500_000,
    opportunities_per_unit: float = 1.0,
    cost_per_defect: Optional[float] = None,
) -> Dict[str, Any]:
    df = dataframe_for(dataset)
    # Sub-analyses that fail (or inputs that look mismapped) are reported here
    # instead of being silently dropped — an empty section must be
    # distinguishable from a failed one.
    warnings: List[str] = []
    rows_total = int(len(df))
    truncated = bool(max_rows and rows_total > max_rows)
    if truncated:
        df = df.head(int(max_rows)).copy()

    if defect_column not in df.columns:
        raise ValueError(f"Defect column not found: '{defect_column}'")

    # Coerce defect column to numeric (0/1 or counts)
    df["_defect"] = pd.to_numeric(df[defect_column], errors="coerce").fillna(0)
    if (df["_defect"] < 0).any():
        warnings.append(
            f"Column '{defect_column}' contains negative values — check that it really holds defect flags or counts."
        )
    is_binary = df["_defect"].dropna().isin([0, 1]).all()

    # ── Batch-level stats ────────────────────────────────────────────────────
    # A batch must have at least this many records before it can be named the
    # "worst" — otherwise a single defective unit reads as a 100% defect rate
    # and a 1-record batch eclipses statistically meaningful ones.
    MIN_BATCH_N = 5
    batch_stats: List[Dict] = []
    worst_batch = "—"
    worst_batch_records = 0

    if batch_column and batch_column in df.columns:
        grp = df.groupby(batch_column)["_defect"]
        if is_binary:
            batch_df = grp.agg(total_records="count", defect_count="sum").reset_index()
            batch_df["defect_rate"] = (batch_df["defect_count"] / batch_df["total_records"] * 100).round(2)
        else:
            batch_df = grp.agg(total_records="count", defect_count="sum", avg_defects="mean").reset_index()
            batch_df["defect_rate"] = batch_df["avg_defects"].round(3)

        batch_df = batch_df.sort_values("defect_rate", ascending=False)

        if len(batch_df):
            qualified = batch_df[batch_df["total_records"] >= MIN_BATCH_N]
            worst_src = qualified if len(qualified) else batch_df
            worst_row = worst_src.iloc[0]
            worst_batch = str(worst_row[batch_column])
            worst_batch_records = int(worst_row["total_records"])

        batch_stats = batch_df.head(100).rename(columns={batch_column: "batch_id"}).to_dict(orient="records")
        for b in batch_stats:
            for k, v in b.items():
                if hasattr(v, "item"):
                    b[k] = v.item()

    # ── Overall summary ──────────────────────────────────────────────────────
    total_records = int(len(df))
    total_defects = int(df["_defect"].sum())

    # The headline metric depends on what the defect column means:
    #   • binary (0/1 flags) → a true defect RATE as a percentage of records
    #   • counts (defects per unit) → defects per record (DPU); multiplying by
    #     100 and calling it a "%" is wrong and can read as e.g. "500%".
    if is_binary:
        metric_type = "rate"
        defect_metric = round(total_defects / total_records * 100, 2) if total_records else 0.0
        defect_rate = defect_metric
        metric_label = "Defect Rate"
        metric_suffix = "%"
    else:
        metric_type = "dpu"
        defect_metric = round(total_defects / total_records, 3) if total_records else 0.0
        defect_rate = None
        metric_label = "Defects / Record"
        metric_suffix = ""

    # DPMO normalizes across products with different defect-opportunity counts
    # per unit — the metric Six Sigma-literate customers expect by name.
    dpmo = (
        round(total_defects / (total_records * opportunities_per_unit) * 1_000_000, 1)
        if total_records and opportunities_per_unit > 0 else None
    )
    sigma_level = _sigma_level(dpmo) if dpmo is not None else None
    total_cost_of_quality = round(total_defects * cost_per_defect, 2) if cost_per_defect is not None else None

    # ── Feature correlations with defect ────────────────────────────────────
    correlations: List[Dict] = []
    feat_cols = feature_columns or []
    feat_cols = [c for c in feat_cols if c in df.columns]

    if not feat_cols:
        exclude = {defect_column, "_defect"}
        if batch_column:
            exclude.add(batch_column)
        if timestamp_column:
            exclude.add(timestamp_column)
        numeric_df = df.apply(pd.to_numeric, errors="coerce")
        feat_cols = [
            c for c in df.columns
            if c not in exclude and numeric_df[c].notna().sum() > 0
        ][:20]

    for col in feat_cols:
        numeric = pd.to_numeric(df[col], errors="coerce")
        if numeric.dropna().empty:
            continue
        try:
            corr = float(numeric.corr(df["_defect"]))
            if np.isnan(corr):
                continue
            correlations.append({
                "feature": col,
                "correlation": round(corr, 4),
                "direction": "positive" if corr > 0 else "negative",
                "strength": "strong" if abs(corr) > 0.5 else ("moderate" if abs(corr) > 0.25 else "weak"),
                "abs_corr": round(abs(corr), 4),
            })
        except Exception as e:
            logger.warning("Correlation for %r failed", col, exc_info=True)
            warnings.append(f"Correlation for '{col}' failed: {e}")

    correlations.sort(key=lambda x: -x["abs_corr"])

    # ── Trend over time ──────────────────────────────────────────────────────
    trend: List[Dict] = []
    valid_resampled = None
    if timestamp_column and timestamp_column in df.columns:
        df["_ts"] = pd.to_datetime(df[timestamp_column], errors="coerce")
        df_t = df.dropna(subset=["_ts"]).copy()
        if len(df_t) > 0:
            df_t = df_t.set_index("_ts").sort_index()
            span_days = (df_t.index.max() - df_t.index.min()).days
            freq = "W" if span_days > 60 else "D"
            try:
                if is_binary:
                    resampled = df_t["_defect"].resample(freq).agg(["sum", "count"])
                    resampled["defect_rate"] = (resampled["sum"] / resampled["count"] * 100).round(2)
                else:
                    resampled = df_t["_defect"].resample(freq).mean().round(3).to_frame("defect_rate")
                valid_resampled = resampled.dropna(subset=["defect_rate"])
                trend = [
                    {"date": str(idx.date()), "defect_rate": float(row["defect_rate"])}
                    for idx, row in valid_resampled.iterrows()
                ]
            except Exception as e:
                logger.warning("Trend analysis failed", exc_info=True)
                warnings.append(f"Trend analysis failed: {e}")

    # Control limits turn the trend into an actual SPC chart instead of a bare
    # line - flags periods where the defect rate moved for a special-cause
    # reason, not just normal period-to-period noise. Binary defect data is a
    # proportion, so it gets a proper p-chart (control limits widen/narrow
    # with each period's sample size) instead of an I-MR chart run over the
    # rate, which assumes one fixed sigma throughout.
    trend_control: Optional[Dict] = None
    if len(trend) >= 2:
        if is_binary and valid_resampled is not None:
            counts = valid_resampled["sum"].astype(int).tolist()
            sizes = valid_resampled["count"].astype(int).tolist()
            chart = spc_calc.p_chart(counts, sizes, [t["date"] for t in trend])
            # ponytail: display line uses the average sample size across
            # periods (a single UCL/LCL to draw); the in_control flags below
            # still come from each period's own sample-size-adjusted limits.
            # Upgrade to a per-point band series if subgroup sizes vary a lot.
            p_bar = chart["centerline"] / 100
            avg_n = sum(sizes) / len(sizes) if sizes else 0
            avg_sigma = (p_bar * (1 - p_bar) / avg_n) ** 0.5 if avg_n and 0 < p_bar < 1 else 0.0
            trend_control = {
                "chart_type": "p",
                "centerline": chart["centerline"],
                "ucl": round(min(100.0, (p_bar + 3 * avg_sigma) * 100), 4),
                "lcl": round(max(0.0, (p_bar - 3 * avg_sigma) * 100), 4),
                "out_of_control_count": chart["out_of_control_count"],
                "low_sample_warning": chart["low_sample_warning"],
                "min_baseline": chart["min_baseline"],
            }
            for t, p in zip(trend, chart["points"]):
                t["in_control"] = p["in_control"]
                t["violations"] = p["violations"]
                t["ucl"] = p["ucl"]
                t["lcl"] = p["lcl"]
        else:
            chart = spc_calc.individuals_chart([t["defect_rate"] for t in trend], [t["date"] for t in trend])
            trend_control = {
                "chart_type": "individuals",
                "centerline": chart["centerline"],
                "ucl": chart["ucl"],
                "lcl": chart["lcl"],
                "out_of_control_count": chart["out_of_control_count"],
                "low_sample_warning": chart["low_sample_warning"],
                "min_baseline": chart["min_baseline"],
            }
            for t, p in zip(trend, chart["points"]):
                t["in_control"] = p["in_control"]
                t["violations"] = p["violations"]

    # ── Clustering ───────────────────────────────────────────────────────────
    clusters: List[Dict] = []
    if feat_cols and len(df) >= max(n_clusters * 3, 9):
        cluster_feats = feat_cols[:15]
        X = df[cluster_feats].apply(pd.to_numeric, errors="coerce")

        if _HAS_SKLEARN:
            try:
                imp = SimpleImputer(strategy="median")
                scaler = StandardScaler()
                Xf = scaler.fit_transform(imp.fit_transform(X))

                k = min(int(n_clusters), len(Xf) // 3, 8)
                km = KMeans(n_clusters=k, random_state=42, n_init=10)
                df["_cluster"] = km.fit_predict(Xf)

                avg_rate = defect_metric
                for cid in range(k):
                    mask = df["_cluster"] == cid
                    sub = df[mask]
                    cr = round(float(sub["_defect"].mean() * (100 if is_binary else 1)), 2)
                    means = sub[cluster_feats].mean().round(3).to_dict()
                    clusters.append({
                        "cluster_id": cid,
                        "size": int(mask.sum()),
                        "defect_rate": cr,
                        "label": _label_cluster(cr, avg_rate),
                        "feature_means": {k: float(v) for k, v in means.items()},
                    })
                clusters.sort(key=lambda c: -c["defect_rate"])
            except Exception as e:
                logger.warning("Clustering failed", exc_info=True)
                warnings.append(f"Clustering failed: {e}")

        if not clusters:
            # Quantile-based fallback (used whenever scikit-learn isn't installed —
            # ponytail: not adding sklearn to requirements just for this, this
            # fallback covers the same feature; add sklearn if clustering quality
            # measurably matters).
            try:
                df["_q"] = pd.qcut(df["_defect"], q=min(n_clusters, 3), labels=False, duplicates="drop")
                avg_rate = defect_metric
                for cid in df["_q"].dropna().unique():
                    mask = df["_q"] == cid
                    sub = df[mask]
                    cr = round(float(sub["_defect"].mean() * (100 if is_binary else 1)), 2)
                    clusters.append({
                        "cluster_id": int(cid),
                        "size": int(mask.sum()),
                        "defect_rate": cr,
                        "label": _label_cluster(cr, avg_rate),
                        "feature_means": {},
                    })
                clusters.sort(key=lambda c: -c["defect_rate"])
            except Exception as e:
                logger.warning("Fallback clustering failed", exc_info=True)
                warnings.append(f"Fallback clustering failed: {e}")

    return {
        "summary": {
            "total_records": total_records,
            "total_defects": total_defects,
            "defect_rate_pct": defect_rate,
            "metric_type": metric_type,
            "defect_metric": defect_metric,
            "metric_label": metric_label,
            "metric_suffix": metric_suffix,
            "dpmo": dpmo,
            "sigma_level": sigma_level,
            "cost_per_defect": cost_per_defect,
            "total_cost_of_quality": total_cost_of_quality,
            "worst_batch": worst_batch,
            "worst_batch_records": worst_batch_records,
            "clusters_found": len(clusters),
            "features_used": feat_cols,
            "sklearn_available": _HAS_SKLEARN,
            "rows_analyzed": total_records,
            "rows_total": rows_total,
            "truncated": truncated,
        },
        "batch_stats": batch_stats,
        "correlations": correlations[:15],
        "clusters": clusters,
        "trend": trend,
        "trend_control": trend_control,
        "warnings": warnings,
    }
