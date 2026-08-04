"""Pure metric-computation formulas for scheduled-sync batches. Relocated
unchanged from backend/streaming/metrics.py (the old Flink job's shared
formula module) - see docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md.
No behavior change: same formulas, now called once per scheduled sync batch
(app.services.sync_scheduler) instead of once per Flink 5s tumbling window."""
from __future__ import annotations

from typing import Any


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_window_metrics(rows: list[dict[str, Any]], column_map: dict[str, str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    cm = column_map

    uc = cm.get("units_col")
    if uc:
        vals = [_safe_float(r.get(uc, 0)) for r in rows]
        total_units = sum(vals)
        metrics["throughput"] = round(total_units, 1)

    sc = cm.get("scrap_col")
    tuc = cm.get("total_units_col") or uc
    if sc and tuc:
        total = sum(_safe_float(r.get(tuc, 0)) for r in rows)
        scrap = sum(_safe_float(r.get(sc, 0)) for r in rows)
        if total > 0:
            scrap_pct = round(scrap / total * 100, 2)
            metrics["scrap_rate_pct"] = scrap_pct
            metrics["fpy_pct"] = round(100 - scrap_pct, 2)

    dc = cm.get("downtime_col")
    if dc:
        metrics["total_downtime_min"] = round(sum(_safe_float(r.get(dc, 0)) for r in rows), 1)

    pt = cm.get("planned_time_col")
    ot = cm.get("operating_time_col")
    guc = cm.get("good_units_col")
    tpc = cm.get("target_rate_col")
    if pt and ot and guc and uc:
        tp_total = sum(_safe_float(r.get(pt, 0)) for r in rows)
        op_total = sum(_safe_float(r.get(ot, 0)) for r in rows)
        u_total = sum(_safe_float(r.get(uc, 0)) for r in rows)
        g_total = sum(_safe_float(r.get(guc, 0)) for r in rows)
        avail = (op_total / tp_total * 100) if tp_total > 0 else 0.0
        quality = (g_total / u_total * 100) if u_total > 0 else 0.0
        perf = 100.0
        if tpc:
            target_total = sum(_safe_float(r.get(tpc, 0)) for r in rows)
            if target_total > 0:
                perf = u_total / target_total * 100
        oee = avail * (perf / 100) * (quality / 100)
        metrics["availability_pct"] = round(avail, 1)
        metrics["quality_pct"] = round(quality, 1)
        metrics["performance_pct"] = round(perf, 1)
        metrics["oee_pct"] = round(oee, 1)

    if not uc:
        metrics.setdefault("throughput", 0.0)

    return metrics
