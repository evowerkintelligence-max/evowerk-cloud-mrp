"""Process mining: discover the actual process flow from an event log —
activity durations/waits, transitions, bottlenecks, conformance checking
against the majority path, rework loops, and duration outlier cases.

Runs over an existing uploaded Dataset with user-supplied column mappings
(case/activity/timestamp) — no separate shop-floor "production run" domain
needed. Ported from the desktop app's backend/process_mining_engine.py.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.models.dataset import Dataset
from app.services.datasets import dataframe_for


def _severity(val_min: float, p50: float, p90: float) -> str:
    if val_min >= p90:
        return "high"
    if val_min >= p50:
        return "medium"
    return "low"


def _empty_result(
    *, case_column: str, activity_column: str, timestamp_column: str, warnings: List[str]
) -> Dict[str, Any]:
    return {
        "activities": [],
        "transitions": [],
        "bottlenecks": [],
        "delays": [],
        "conformance": {"reference_sequence": [], "conformance_pct": 0.0, "conforming_cases": 0, "deviating_cases": 0},
        "deviations": [],
        "rework_loops": [],
        "outlier_cases": [],
        "summary": {
            "total_cases": 0,
            "total_activities": 0,
            "avg_case_duration_min": 0.0,
            "longest_bottleneck": "—",
            "columns_used": {"case": case_column, "activity": activity_column, "timestamp": timestamp_column},
        },
        "warnings": warnings,
    }


def _classify_deviation(trace: List[str], reference: List[str]) -> tuple[str, List[str], List[str]]:
    """Compare one case's activity sequence against the reference sequence.
    Presence-based (not position-by-position) — good enough to tell an analyst
    WHAT kind of deviation happened without a full edit-distance alignment."""
    ref_set, trace_set = set(reference), set(trace)
    missing = [a for a in reference if a not in trace_set]
    extra = [a for a in trace if a not in ref_set]
    if not missing and not extra:
        return ("out_of_order", missing, extra)
    if missing and not extra:
        return ("skip", missing, extra)
    if extra and not missing:
        return ("rework", missing, extra)
    return ("mixed", missing, extra)


def analyze_process(
    dataset: Dataset,
    case_column: str,
    activity_column: str,
    timestamp_column: str,
    max_rows: int = 500_000,
    reference_sequence: Optional[List[str]] = None,
) -> Dict[str, Any]:
    warnings: List[str] = []

    df = dataframe_for(dataset)

    for col in (case_column, activity_column, timestamp_column):
        if col not in df.columns:
            raise ValueError(f"Column not found in dataset: '{col}'")

    total_rows_before = len(df)
    if max_rows and total_rows_before > max_rows:
        df = df.head(int(max_rows)).copy()
        warnings.append(
            f"Dataset has {total_rows_before:,} rows; analysis is limited to the first "
            f"{max_rows:,} (by file order, not grouped by case) — case histories that "
            f"extend past this cutoff may be incomplete."
        )

    df = df[[case_column, activity_column, timestamp_column]].copy()
    df.columns = ["case_id", "activity", "ts"]

    df["ts"] = pd.to_datetime(df["ts"], utc=False, errors="coerce")
    n_bad_ts = int(df["ts"].isna().sum())
    if n_bad_ts:
        df = df.dropna(subset=["ts"]).copy()
        warnings.append(f"{n_bad_ts:,} row(s) had a timestamp that could not be parsed and were excluded.")

    # Drop rows with missing case_id/activity BEFORE casting to str — astype(str)
    # turns NaN into the literal string "nan", which would otherwise silently
    # merge every caseless row into one fake shared case.
    n_before = len(df)
    df = df.dropna(subset=["case_id", "activity"]).copy()
    df["activity"] = df["activity"].astype(str).str.strip()
    df["case_id"] = df["case_id"].astype(str).str.strip()
    df = df[(df["activity"] != "") & (df["case_id"] != "")].copy()
    n_missing_id = n_before - len(df)
    if n_missing_id:
        warnings.append(f"{n_missing_id:,} row(s) had a missing/blank case ID or activity and were excluded.")

    if df.empty:
        return _empty_result(
            case_column=case_column, activity_column=activity_column, timestamp_column=timestamp_column,
            warnings=warnings + ["No usable rows remained after cleaning — check the selected columns."],
        )

    df = df.sort_values(["case_id", "ts"], kind="stable").reset_index(drop=True)

    # ── Per-case traces ──────────────────────────────────────
    activity_durations: Dict[str, List[float]] = defaultdict(list)
    activity_wait_times: Dict[str, List[float]] = defaultdict(list)
    transition_counts: Dict[tuple, int] = defaultdict(int)
    transition_gaps: Dict[tuple, List[float]] = defaultdict(list)
    case_durations: List[float] = []
    case_traces: Dict[str, List[str]] = {}
    case_id_durations: List[tuple] = []
    rework_counts: Dict[str, int] = defaultdict(int)

    for case_id, grp in df.groupby("case_id", sort=False):
        events = grp.sort_values("ts", kind="stable").reset_index(drop=True)
        if len(events) < 1:
            continue

        trace = events["activity"].tolist()
        case_traces[str(case_id)] = trace
        seen_in_case: set = set()
        for act in trace:
            if act in seen_in_case:
                rework_counts[act] += 1
            seen_in_case.add(act)

        if len(events) > 1:
            span = (events["ts"].iloc[-1] - events["ts"].iloc[0]).total_seconds() / 60.0
            span = max(0.0, span)
            case_durations.append(span)
            case_id_durations.append((str(case_id), span))

        for i, row in events.iterrows():
            act = row["activity"]
            if i + 1 < len(events):
                next_row = events.iloc[i + 1]
                dur_min = (next_row["ts"] - row["ts"]).total_seconds() / 60.0
                dur_min = max(0.0, dur_min)
                activity_durations[act].append(dur_min)
                key = (act, next_row["activity"])
                transition_counts[key] += 1
                transition_gaps[key].append(dur_min)
            if i > 0:
                prev_row = events.iloc[i - 1]
                wait_min = (row["ts"] - prev_row["ts"]).total_seconds() / 60.0
                activity_wait_times[act].append(max(0.0, wait_min))

    # ── Activity stats ───────────────────────────────────────
    all_acts = sorted(set(df["activity"].unique()), key=lambda a: -len(activity_durations.get(a, [])))
    act_case_counts = df.groupby("activity")["case_id"].nunique().to_dict()
    act_exec_counts = df["activity"].value_counts().to_dict()

    activities = []
    for act in all_acts:
        durs = activity_durations.get(act, [])
        waits = activity_wait_times.get(act, [])
        activities.append({
            "name": act,
            "cases": int(act_case_counts.get(act, 0)),
            "executions": int(act_exec_counts.get(act, 0)),
            "avg_duration_min": round(float(np.mean(durs)), 2) if durs else 0.0,
            "median_duration_min": round(float(np.median(durs)), 2) if durs else 0.0,
            "avg_wait_min": round(float(np.mean(waits)), 2) if waits else 0.0,
        })

    # ── Transitions ──────────────────────────────────────────
    total_trans = sum(transition_counts.values()) or 1
    transitions = []
    for (frm, to), cnt in sorted(transition_counts.items(), key=lambda x: -x[1]):
        gaps = transition_gaps[(frm, to)]
        transitions.append({
            "from": frm, "to": to, "count": int(cnt),
            "pct": round(cnt / total_trans * 100, 1),
            "avg_gap_min": round(float(np.mean(gaps)), 2) if gaps else 0.0,
            "max_gap_min": round(float(np.max(gaps)), 2) if gaps else 0.0,
        })

    # ── Bottlenecks (activities with highest avg wait time) ──
    wait_vals = [a["avg_wait_min"] for a in activities if a["avg_wait_min"] > 0]
    p50 = float(np.percentile(wait_vals, 50)) if wait_vals else 0.0
    p90 = float(np.percentile(wait_vals, 90)) if wait_vals else 0.0

    bottlenecks = sorted([a for a in activities if a["avg_wait_min"] > 0], key=lambda a: -a["avg_wait_min"])[:10]
    for b in bottlenecks:
        b["severity"] = _severity(b["avg_wait_min"], p50, p90)

    # ── Delays (slowest transitions) ─────────────────────────
    delays = sorted(transitions, key=lambda t: -t["avg_gap_min"])[:10]

    # ── Conformance checking ──────────────────────────────────
    # Reference model: explicit override, else the most common full trace
    # (majority path) — auto-derived by default.
    if reference_sequence:
        reference = list(reference_sequence)
    elif case_traces:
        trace_counts = Counter(tuple(t) for t in case_traces.values())
        reference = list(trace_counts.most_common(1)[0][0])
    else:
        reference = []

    conforming = 0
    deviations: List[Dict[str, Any]] = []
    for case_id, trace in case_traces.items():
        if trace == reference:
            conforming += 1
            continue
        dtype, missing, extra = _classify_deviation(trace, reference)
        deviations.append({
            "case_id": case_id, "deviation_type": dtype,
            "missing_activities": missing, "extra_activities": extra,
        })
    total_traced = len(case_traces) or 1
    conformance = {
        "reference_sequence": reference,
        "conformance_pct": round(conforming / total_traced * 100, 1),
        "conforming_cases": conforming,
        "deviating_cases": len(deviations),
    }
    # Cap the detail list — the aggregate deviating_cases count already tells
    # the whole story; a dataset with thousands of non-conforming cases
    # doesn't need thousands of rows shipped to the frontend.
    deviations_out = deviations[:200]

    # ── Rework loops (activities that recur within the same case) ─
    rework_loops = [
        {"activity": act, "recurrence_count": cnt}
        for act, cnt in sorted(rework_counts.items(), key=lambda x: -x[1])[:10]
    ]

    # ── Outlier cases (case duration far from the mean) ──────
    outlier_cases: List[Dict[str, Any]] = []
    if len(case_id_durations) >= 3:
        durs = np.array([d for _, d in case_id_durations], dtype=float)
        mean_d, std_d = float(np.mean(durs)), float(np.std(durs, ddof=1))
        if std_d > 0:
            for case_id, d in case_id_durations:
                z = (d - mean_d) / std_d
                if abs(z) > 2:
                    outlier_cases.append({
                        "case_id": case_id, "duration_min": round(d, 2),
                        "direction": "slow" if z > 0 else "fast", "z_score": round(z, 2),
                    })
            outlier_cases.sort(key=lambda o: -abs(o["z_score"]))
            outlier_cases = outlier_cases[:20]

    # ── Summary ──────────────────────────────────────────────
    total_cases = int(df["case_id"].nunique())
    avg_case_dur = round(float(np.mean(case_durations)), 2) if case_durations else 0.0
    longest_bn = bottlenecks[0]["name"] if bottlenecks else "—"

    return {
        "activities": activities,
        "transitions": transitions,
        "bottlenecks": bottlenecks,
        "delays": delays,
        "conformance": conformance,
        "deviations": deviations_out,
        "rework_loops": rework_loops,
        "outlier_cases": outlier_cases,
        "summary": {
            "total_cases": total_cases,
            "total_activities": len(all_acts),
            "avg_case_duration_min": avg_case_dur,
            "longest_bottleneck": longest_bn,
            "columns_used": {"case": case_column, "activity": activity_column, "timestamp": timestamp_column},
        },
        "warnings": warnings,
    }
