"""Quality analytics: defect-rate/DPU summary, worst batches, feature
correlations, trend, and clustering of "what makes a bad batch different".

Runs over an existing uploaded Dataset with user-supplied column mappings.
Ported from the desktop app's backend/quality_engine.py; the file-format
reader is replaced by app.services.datasets.dataframe_for (DuckDB-backed,
consistent with every other dataset consumer in this app).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.models.dataset import Dataset
from app.services.datasets import dataframe_for

_HAS_SKLEARN = False
try:
    from sklearn.cluster import KMeans
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    _HAS_SKLEARN = True
except Exception:
    pass


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
) -> Dict[str, Any]:
    df = dataframe_for(dataset)
    rows_total = int(len(df))
    truncated = bool(max_rows and rows_total > max_rows)
    if truncated:
        df = df.head(int(max_rows)).copy()

    if defect_column not in df.columns:
        raise ValueError(f"Defect column not found: '{defect_column}'")

    # Coerce defect column to numeric (0/1 or counts)
    df["_defect"] = pd.to_numeric(df[defect_column], errors="coerce").fillna(0)
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
        except Exception:
            pass

    correlations.sort(key=lambda x: -x["abs_corr"])

    # ── Trend over time ──────────────────────────────────────────────────────
    trend: List[Dict] = []
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
                trend = [
                    {"date": str(idx.date()), "defect_rate": float(row["defect_rate"])}
                    for idx, row in resampled.iterrows()
                    if not np.isnan(row["defect_rate"])
                ]
            except Exception:
                pass

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
            except Exception:
                pass

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
            except Exception:
                pass

    return {
        "summary": {
            "total_records": total_records,
            "total_defects": total_defects,
            "defect_rate_pct": defect_rate,
            "metric_type": metric_type,
            "defect_metric": defect_metric,
            "metric_label": metric_label,
            "metric_suffix": metric_suffix,
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
    }
