"""Config-driven registry for guided AutoML use-case templates. Each
template declares the column roles its wizard needs, a preprocess() that
turns a mapped Dataset into automl_engine input (or a "not enough signal"
sentinel), and a postprocess() that turns the engine's output into
domain-language results. automl_engine.py itself is never modified by any
template - every template only changes what it's called with.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd


@dataclass
class ColumnRole:
    key: str
    label: str
    required: bool = True


@dataclass
class PreprocessResult:
    """What a template's preprocess() hands back to automl_runner. When
    skip_ml=True, automl_runner calls postprocess(None, config, context)
    directly without ever calling automl_engine - used when a template
    determines there isn't enough signal to train (see
    predictive_maintenance.py's statistical fallback)."""
    skip_ml: bool = False
    df: pd.DataFrame | None = None
    target_col: str | None = None
    task: str | None = None  # "classification" | "regression" | "forecasting"
    feature_cols: list[str] | None = None
    algorithms: list[str] | None = None
    notes: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecommendationRule:
    driver_pattern: str
    direction: str  # "high" | "low" | "rising" | "falling"
    action_text: str


@dataclass
class AutoMLTemplate:
    id: str
    label: str
    roles: list[ColumnRole]
    preprocess: Callable[[pd.DataFrame, dict[str, Any]], PreprocessResult]
    postprocess: Callable[[dict[str, Any] | None, dict[str, Any], dict[str, Any]], dict[str, Any]]
    recommendation_rules: list[RecommendationRule] = field(default_factory=list)
    is_timeseries: bool = False
    min_positive_examples: int = 20


_REGISTRY: dict[str, AutoMLTemplate] = {}


def register_template(template: AutoMLTemplate) -> None:
    _REGISTRY[template.id] = template


def get_template(template_id: str) -> AutoMLTemplate:
    if template_id not in _REGISTRY:
        raise KeyError(f"Unknown AutoML template '{template_id}'")
    return _REGISTRY[template_id]


def list_templates() -> list[AutoMLTemplate]:
    return list(_REGISTRY.values())


from app.services.automl_templates import predictive_maintenance  # noqa: E402,F401 - registers the template
