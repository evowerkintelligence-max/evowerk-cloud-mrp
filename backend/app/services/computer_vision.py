"""Computer vision: image upload -> Gemini vision analysis for defect
detection, PPE compliance, or object counting, falling back to a
rule-based demo response when no Gemini key is configured. Ported from the
desktop app's computer_vision.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.services import llm

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_TASK_PROMPTS: dict[str, str] = {
    "defect_detection": (
        "You are an industrial quality-control vision system. Analyse this image for manufacturing defects: "
        "scratches, cracks, dents, discolouration, misalignment, contamination, or any surface anomaly. "
        "For each defect found, state: label (e.g. 'Surface crack'), confidence (high/medium/low), "
        "approximate location (e.g. 'top-left edge'), severity (ok/warning/critical). "
        "If no defects: say 'No defects detected - part appears acceptable'. Respond ONLY in this JSON format:\n"
        '{"summary": "...", "findings": [{"label":"...","confidence":"...","location":"...","severity":"..."}]}'
    ),
    "ppe_compliance": (
        "You are a factory safety compliance vision system. Analyse this image for Personal Protective "
        "Equipment (PPE) compliance. Check for: hard hats/helmets, safety vests/hi-vis jackets, safety "
        "glasses/goggles, gloves, steel-toe boots, ear protection, face shields. For each person visible, list "
        "which PPE items are present and which are missing. Calculate an overall compliance percentage. "
        "Respond ONLY in this JSON format:\n"
        '{"summary": "...", "compliance_pct": 85, "findings": [{"label":"...","confidence":"...","location":"person N","severity":"ok|warning|critical"}]}'
    ),
    "counting": (
        "You are an industrial counting vision system. Count the primary objects visible in this image "
        "(e.g., boxes, pallets, parts, workers, vehicles). State what you are counting and the total count. "
        "List any subgroups you notice (e.g. different sizes, types, colours). Respond ONLY in this JSON format:\n"
        '{"summary": "...", "count": 12, "findings": [{"label":"...","confidence":"...","location":"...","severity":"ok"}]}'
    ),
}


@dataclass
class CvFinding:
    label: str
    confidence: str
    location: str | None = None
    severity: str | None = None


@dataclass
class CvResult:
    task: str
    mode: str  # "llm" | "demo"
    summary: str
    findings: list[CvFinding] = field(default_factory=list)
    compliance_pct: float | None = None
    count: int | None = None


_DEMO: dict[str, CvResult] = {
    "defect_detection": CvResult(
        task="defect_detection", mode="demo",
        summary="Demo mode - no Gemini API key configured. In production, vision AI detects surface defects, cracks, and anomalies in real-time.",
        findings=[
            CvFinding(label="Surface scratch (demo)", confidence="high", location="top-right", severity="warning"),
            CvFinding(label="Minor discolouration (demo)", confidence="medium", location="centre", severity="ok"),
        ],
    ),
    "ppe_compliance": CvResult(
        task="ppe_compliance", mode="demo",
        summary="Demo mode - no Gemini API key configured. In production, vision AI checks for hard hats, vests, glasses, gloves per worker.",
        findings=[
            CvFinding(label="Hard hat present (demo)", confidence="high", location="person 1", severity="ok"),
            CvFinding(label="Safety vest MISSING (demo)", confidence="high", location="person 1", severity="critical"),
        ],
        compliance_pct=50.0,
    ),
    "counting": CvResult(
        task="counting", mode="demo",
        summary="Demo mode - no Gemini API key configured. In production, vision AI counts pallets, boxes, parts, or workers.",
        findings=[CvFinding(label="Pallets detected (demo)", confidence="high", location="floor area", severity="ok")],
        count=7,
    ),
}


async def analyze(task: str, image_bytes: bytes, mime_type: str) -> CvResult:
    if task not in _TASK_PROMPTS:
        raise ValueError(f"Unknown task '{task}'. Choose: {list(_TASK_PROMPTS)}")

    if not llm.is_configured():
        return _DEMO[task]

    safe_mime = mime_type if mime_type in _ALLOWED_MIME else "image/jpeg"
    raw = await llm.generate_from_image(image_bytes, safe_mime, _TASK_PROMPTS[task])

    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end]) if start >= 0 else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    findings = [
        CvFinding(
            label=f.get("label", "Unknown"), confidence=f.get("confidence", "medium"),
            location=f.get("location"), severity=f.get("severity", "warning"),
        )
        for f in (data.get("findings") or [])
    ]
    return CvResult(
        task=task, mode="llm", summary=data.get("summary", raw[:300]),
        findings=findings, compliance_pct=data.get("compliance_pct"), count=data.get("count"),
    )
