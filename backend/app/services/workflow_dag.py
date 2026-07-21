"""DAG validation and scheduling for workflows - ported from the desktop app's
execution/types.py + engine.py (topo-sort, cycle detection, failure policy),
adapted to this app's 8-step registry (see workflow_steps.py). Arbitrary
code-execution step types (python/shell) and dl_infer were dropped on the way
over - see the workflow port plan for why.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

FailurePolicy = Literal["fail_fast", "continue", "skip_dependents"]

STEP_TYPES = (
    "sql",
    "filter",
    "aggregate",
    "load_dataset",
    "export_result",
    "import_csv",
    "import_excel",
    "import_json",
)


class StepPosition(BaseModel):
    """Canvas coordinates for a step - UI metadata only, never read by
    validate_dag/topo_layers/descendants below."""

    x: float
    y: float


class WorkflowStep(BaseModel):
    id: str
    name: str
    type: str
    depends_on: list[str] = []
    config: dict = {}
    position: StepPosition | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in STEP_TYPES:
            raise ValueError(f"Unknown step type: {v!r}. Must be one of {STEP_TYPES}")
        return v


class DagError(ValueError):
    """Raised on a malformed DAG - callers turn this into an HTTP 400."""


def topo_layers(steps: list[WorkflowStep]) -> list[list[WorkflowStep]]:
    """Kahn's algorithm, grouped into layers: every step in a layer depends
    only on steps in strictly earlier layers, so a layer's steps can run
    concurrently."""
    by_id = {s.id: s for s in steps}
    remaining_deps = {s.id: set(s.depends_on) for s in steps}
    layers: list[list[WorkflowStep]] = []
    done: set[str] = set()
    while len(done) < len(steps):
        ready = sorted(sid for sid, deps in remaining_deps.items() if sid not in done and deps <= done)
        if not ready:
            raise DagError("Workflow has a cycle")
        layers.append([by_id[sid] for sid in ready])
        done.update(ready)
    return layers


def validate_dag(raw_steps: list[dict]) -> list[WorkflowStep]:
    """Parse+validate a workflow's steps: known types, unique ids, depends_on
    referencing real steps, no cycles."""
    try:
        steps = [WorkflowStep.model_validate(s) for s in raw_steps]
    except Exception as exc:  # noqa: BLE001 - re-raised as a client-safe DagError below
        raise DagError(f"Invalid step definition: {exc}") from exc

    ids = [s.id for s in steps]
    if len(ids) != len(set(ids)):
        raise DagError("Duplicate step id")
    known = set(ids)
    for s in steps:
        unknown = set(s.depends_on) - known
        if unknown:
            raise DagError(f"Step '{s.id}' depends on unknown step(s): {sorted(unknown)}")

    topo_layers(steps)  # raises DagError on cycle
    return steps


def descendants(steps: list[WorkflowStep], failed_ids: set[str]) -> set[str]:
    """Transitive closure of every step that (directly or indirectly) depends
    on any step in failed_ids - used by the skip_dependents failure policy."""
    result: set[str] = set()
    changed = True
    while changed:
        changed = False
        for s in steps:
            if s.id in result or s.id in failed_ids:
                continue
            if set(s.depends_on) & (failed_ids | result):
                result.add(s.id)
                changed = True
    return result
