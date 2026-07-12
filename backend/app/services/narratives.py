"""Turn a set of already-computed KPI/compare/forecast results into readable
prose. Ported near-verbatim from the desktop app's narratives_engine.py — pure
string composition, no I/O, no LLM call, so it needed no cloud adaptation.

This is a distinct, complementary feature to app.services.ai_insights: `ask`
takes a free-text question and runs its own intent detection + data fetch;
`build_narrative` here takes results the caller already computed (e.g. a
dashboard that just ran a few /kpis/compute + /kpis/compare + a forecast) and
narrates them - no data fetching of its own, so it's shape-agnostic about
where those results came from (the _extract_* helpers are deliberately
best-effort, same as the original: "existing KPI engines evolve").
"""
from __future__ import annotations

from typing import Any, Literal

Audience = Literal["exec", "analyst", "engineer"]
Style = Literal["bullet", "paragraph", "mixed"]


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _fmt_pct(delta: float) -> str:
    # delta is a ratio (e.g., 0.12 -> 12%)
    pct = delta * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _fmt_num(v: float) -> str:
    # mild formatting without locale deps
    av = abs(v)
    if av >= 1_000_000_000:
        return f"{v/1_000_000_000:.2f}B"
    if av >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if av >= 1_000:
        return f"{v/1_000:.2f}K"
    return f"{v:.2f}"


def _extract_kpi_table(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Accepts a variety of KPI result shapes and returns a uniform mapping:
      { kpi_key: { "value": float, "meta": {...} } }
    Best-effort, because KPI result shapes vary by caller.
    """
    out: dict[str, Any] = {}

    if not isinstance(payload, dict):
        return out

    # Shape A: {"kpis": {"k1": {"value": 123}, "k2": 5}}
    kpis = payload.get("kpis")
    if isinstance(kpis, dict):
        for k, v in kpis.items():
            if isinstance(v, dict):
                vv = _safe_float(v.get("value"))
                if vv is None:
                    continue
                out[str(k)] = {"value": vv, "meta": {kk: vv2 for kk, vv2 in v.items() if kk != "value"}}
            else:
                vv = _safe_float(v)
                if vv is None:
                    continue
                out[str(k)] = {"value": vv, "meta": {}}

    # Shape B: {"results": [{"kpi_id": "...", "value": 123, ...}, ...]}
    # (matches app.services.kpis.KpiComputeItem, serialized)
    results = payload.get("results")
    if isinstance(results, list):
        for r in results:
            if not isinstance(r, dict):
                continue
            key = r.get("kpi_id") or r.get("kpi_key") or r.get("name")
            vv = _safe_float(r.get("value"))
            if not key or vv is None:
                continue
            meta = {k: v for k, v in r.items() if k not in ("kpi_id", "kpi_key", "name", "value")}
            out[str(key)] = {"value": vv, "meta": meta}

    return out


def _extract_compare_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Compare payload best-effort normalization. Expected to optionally include:
      - "kpi_deltas": {kpi_key: {"a":..., "b":..., "delta":...}}
      - "summary": "..."
    """
    if not isinstance(payload, dict):
        return {}
    return payload


def _extract_forecast_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    """
    Normalizes forecast output. Expected shape:
      { "kpi_key": "...", "points": [{"index":"step_1","yhat":...}, ...], "horizon":..., "method_used":... }
    """
    if not isinstance(payload, dict):
        return None
    k = payload.get("kpi_key")
    pts = payload.get("points")
    if not k or not isinstance(pts, list) or not pts:
        return None
    first = pts[0] if isinstance(pts[0], dict) else None
    last = pts[-1] if isinstance(pts[-1], dict) else None
    if not first or not last:
        return None
    return {
        "kpi_key": str(k),
        "yhat_first": _safe_float(first.get("yhat")),
        "yhat_last": _safe_float(last.get("yhat")),
        "horizon": payload.get("horizon"),
        "method_used": payload.get("method_used"),
    }


def build_narrative(
    *,
    question: str,
    results_by_action_id: dict[str, dict[str, Any]],
    audience: Audience = "exec",
    style: Style = "mixed",
    source_action_ids: list[str] | None = None,
) -> str:
    """
    Create a narrative from structured action results.

    results_by_action_id maps action_id -> {"type": "kpi_run"|"compare_runs"|"forecast_kpi",
    "payload": {...}, "ok": bool, "error": "..."}.
    """
    if source_action_ids:
        results = {aid: results_by_action_id[aid] for aid in source_action_ids if aid in results_by_action_id}
    else:
        results = results_by_action_id

    bullets: list[str] = []
    paragraphs: list[str] = []

    # 1) High-level opener
    if audience == "exec":
        paragraphs.append(f"Here's what matters based on your question: {question.strip()}")
    elif audience == "analyst":
        paragraphs.append(f"Summary for: {question.strip()}")
    else:
        paragraphs.append(f"Findings for: {question.strip()}")

    # 2) Pull KPI runs
    kpi_blocks: list[tuple[str, dict[str, Any]]] = []
    compare_blocks: list[tuple[str, dict[str, Any]]] = []
    forecast_blocks: list[tuple[str, dict[str, Any]]] = []

    for aid, r in results.items():
        if not isinstance(r, dict):
            continue
        rtype = str(r.get("type") or "")
        ok = bool(r.get("ok", True))
        payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}

        if not ok:
            err = r.get("error") or "Action failed"
            bullets.append(f"[{aid}] {rtype}: {err}")
            continue

        if rtype == "kpi_run":
            kpi_blocks.append((aid, payload))
        elif rtype == "compare_runs":
            compare_blocks.append((aid, payload))
        elif rtype == "forecast_kpi":
            forecast_blocks.append((aid, payload))

    # 3) KPI highlights
    if kpi_blocks:
        merged: dict[str, Any] = {}
        for _, p in kpi_blocks:
            merged.update(_extract_kpi_table(p))

        if merged:
            # Rank by absolute magnitude (simple heuristic)
            ranked = sorted(merged.items(), key=lambda kv: abs(_safe_float(kv[1].get("value")) or 0.0), reverse=True)
            top = ranked[:5]
            if top:
                bullets.append("Top KPI readings:")
                for k, info in top:
                    v = _safe_float(info.get("value"))
                    if v is None:
                        continue
                    bullets.append(f"- {k}: {_fmt_num(v)}")

    # 4) Comparisons / deltas
    if compare_blocks:
        for _aid, p in compare_blocks:
            c = _extract_compare_summary(p)
            kd = c.get("kpi_deltas")
            if isinstance(kd, dict) and kd:
                bullets.append("Key changes vs baseline:")
                shown = 0
                for k, dv in kd.items():
                    if shown >= 6:
                        break
                    if not isinstance(dv, dict):
                        continue
                    a = _safe_float(dv.get("a"))
                    b = _safe_float(dv.get("b"))
                    if a is None or b is None:
                        continue
                    if a == 0:
                        bullets.append(f"- {k}: {_fmt_num(b)} (was {_fmt_num(a)})")
                    else:
                        ratio = (b - a) / a
                        bullets.append(f"- {k}: {_fmt_num(b)} ({_fmt_pct(ratio)} vs {_fmt_num(a)})")
                    shown += 1
            else:
                s = c.get("summary")
                if isinstance(s, str) and s.strip():
                    bullets.append(f"Comparison summary: {s.strip()}")

    # 5) Forecast summaries
    if forecast_blocks:
        for _aid, p in forecast_blocks:
            fs = _extract_forecast_summary(p)
            if not fs:
                continue
            k = fs.get("kpi_key")
            y0 = fs.get("yhat_first")
            yN = fs.get("yhat_last")
            hz = fs.get("horizon")
            method = fs.get("method_used")

            if y0 is None or yN is None:
                continue

            if y0 == 0:
                trend = "up" if yN > 0 else "down" if yN < 0 else "flat"
            else:
                trend_ratio = (yN - y0) / abs(y0)
                if abs(trend_ratio) < 0.02:
                    trend = "roughly flat"
                elif trend_ratio > 0:
                    trend = "up"
                else:
                    trend = "down"

            bullets.append(
                f"Forecast ({k}): {trend} over {hz} steps "
                f"(start {_fmt_num(float(y0))} → end {_fmt_num(float(yN))}, method={method})."
            )

    # 6) Compose output by style
    if style == "paragraph":
        if bullets:
            paragraphs.append(" ".join(bullets))
        return "\n\n".join(paragraphs).strip()

    if style == "bullet":
        lines = [p.strip() for p in paragraphs if p.strip()]
        lines.extend(bullets)
        return "\n".join(lines).strip()

    # mixed
    out = [p for p in paragraphs if p.strip()]
    if bullets:
        out.append("")  # spacer
        out.extend(bullets)
    return "\n".join(out).strip()
