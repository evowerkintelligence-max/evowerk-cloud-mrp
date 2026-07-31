from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset
from app.services import dataset_query
from app.services import datasets as ds_service

# Rules-first NLQ over an uploaded dataset: aggregate-keyword + best-matching
# column name, run through the same ad-hoc query engine BI chart building
# already uses (services.dataset_query). Falls back to a plain dataset
# summary when nothing in the question maps to a column - still a useful
# answer, not an error.


@dataclass
class AiInsightResult:
    question: str
    intents: list[str]
    narrative: str
    warnings: list[str] = field(default_factory=list)


_AGG_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("count", ("how many", "count", "number of")),
    ("sum", ("sum", "total")),
    ("avg", ("average", "avg", "mean")),
    ("max", ("max", "maximum", "highest", "largest")),
    ("min", ("min", "minimum", "lowest", "smallest")),
]


def _detect_agg(q_lower: str) -> str | None:
    return next((agg for agg, keywords in _AGG_KEYWORDS if any(k in q_lower for k in keywords)), None)


def _match_column(dataset: Dataset, q_lower: str) -> dict | None:
    """The longest dataset column name mentioned in the question - a loose
    substring match (spaces/underscores interchangeable) rather than NER;
    good enough for "what's the total order_qty" without a parser."""
    best = None
    for col in dataset.columns:
        name = col["name"]
        if name.lower() in q_lower or name.lower().replace("_", " ") in q_lower:
            if best is None or len(name) > len(best["name"]):
                best = col
    return best


async def answer_question_over_dataset(
    db: AsyncSession, org_id: uuid.UUID, dataset_id: uuid.UUID, question: str
) -> AiInsightResult:
    dataset = await ds_service.get_dataset_or_404(db, org_id, dataset_id)
    q_lower = question.lower()
    agg = _detect_agg(q_lower)

    if agg == "count":
        return AiInsightResult(
            question=question,
            intents=["dataset"],
            narrative=f"'{dataset.name}' has {dataset.row_count:,} row(s).",
            warnings=[],
        )

    column = _match_column(dataset, q_lower)
    if agg and column:
        # CSV/TSV columns are sniffed ALL_VARCHAR (see services.datasets), so the
        # stored dtype can't tell numeric apart - compute_health's TRY_CAST-based
        # inferred_dtype can, and is only worth its per-column scan once we
        # actually have an aggregate + column to act on.
        health = ds_service.compute_health(dataset)
        col_health = next((c for c in health["columns"] if c["name"] == column["name"]), None)
        if col_health is None or col_health["inferred_dtype"] != "numeric":
            numeric_cols = [c["name"] for c in health["columns"] if c["inferred_dtype"] == "numeric"]
            return AiInsightResult(
                question=question,
                intents=["dataset"],
                narrative=f"'{column['name']}' isn't numeric, so {'an' if agg == 'avg' else 'a'} {agg} can't be computed on it.",
                warnings=[
                    f"Numeric columns: {', '.join(numeric_cols)}" if numeric_cols else "This dataset has no numeric columns."
                ],
            )
        req = dataset_query.DatasetQueryRequest(
            select=[dataset_query.SelectField(column=column["name"], agg=agg, alias="result")]
        )
        result = await dataset_query.run_query(db, dataset, org_id, req)
        value = result["rows"][0]["result"] if result["rows"] else None
        value_str = f"{value:,.2f}" if isinstance(value, (int, float)) else "no value"
        return AiInsightResult(
            question=question,
            intents=["dataset"],
            narrative=f"{agg.capitalize()} of '{column['name']}' across {dataset.row_count:,} row(s): {value_str}.",
            warnings=[],
        )

    columns_desc = ", ".join(c["name"] for c in dataset.columns)
    return AiInsightResult(
        question=question,
        intents=["dataset"],
        narrative=(
            f"'{dataset.name}' has {dataset.row_count:,} rows and {len(dataset.columns)} columns. "
            f'Try naming a column directly, e.g. "what is the total of <column>?" or "average <column>".'
        ),
        warnings=[f"Columns: {columns_desc}"],
    )
