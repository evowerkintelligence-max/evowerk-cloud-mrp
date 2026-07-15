# Report Builder Capture-Based Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any result-producing page in the app ("Send to report") drop a live reference into a per-org inbox, and let Report Builder pick, order, and assemble those captured items into a report — replacing hand-typed dataset UUIDs and raw JSON as the only way to build a report.

**Architecture:** A new `ReportInboxItem` (org-scoped, holds a label + a `ReportSectionSpec`-shaped payload) plus three new `ReportSectionSpec` types (`engine`, `kind_report`, `rca_case`) and one discovered-during-planning addition (`sql`, for the SQL Editor's raw-SQL results, which don't fit the existing structured `table` type). Resolution changes from "one spec produces one `Section`" to "one spec produces a list of `Section`s," reusing existing compute code directly (the 7 analytics-engine route handlers, the existing `build_*_report` functions, the RCA-case service) with zero duplicated business logic. A generic flattener converts any analytics engine's Pydantic response into report tables without per-engine code.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + Pydantic on the backend; React + TanStack Query + Tailwind on the frontend. No new dependencies.

## Global Constraints

- Every new/changed backend endpoint follows the existing org-scoping convention in `backend/app/api/routes/reports.py`: filter by `current.org_id`, return 404 (not 403) for cross-org access, mirroring `get_saved_report_or_404`.
- Deleting an inbox item is allowed for its creator (`current.id == item.created_by`) or any user with `current.role == "owner"` — narrower than the plain `require_role("owner")` used elsewhere in this codebase for chart/dashboard/dataset/saved-report deletion (see the design spec's rationale).
- No new frontend dependencies and no new test runner — this repo has none (`frontend/package.json` has only `dev`/`build`/`preview`/`lint`); frontend changes are verified manually in the browser, per the precedent set in `docs/superpowers/specs/2026-07-15-sql-editor-autocomplete-design.md`.
- Backend tests follow the existing `pytest` conventions in `backend/tests/test_report_builder.py` and `backend/tests/test_bi.py` (async, `httpx`-style `client` fixture, `_register_and_login` helper).
- Design spec: `docs/superpowers/specs/2026-07-15-report-builder-capture-design.md`. One deviation from it, discovered while planning (documented at Task 3 and Task 5): the `dataset` report kind is folded into the `kind_report` resolver (reusing the relocated `build_report_by_kind` uniformly for all 5 kinds) instead of the spec's original suggestion to special-case it as a `table` capture; a new `sql` section type is added for the SQL Editor, which produces raw SQL text rather than a structured `DatasetQueryRequest` and so cannot reuse `table`.

---

## Task 1: `ReportInboxItem` model + migration

**Files:**
- Modify: `backend/app/models/report_spec.py`
- Create: `backend/alembic/versions/<new_revision>_report_inbox_items.py`
- Test: `backend/tests/test_report_builder.py` (model exercised indirectly via Task 7's API tests — this task has no standalone test; SQLAlchemy models aren't tested in isolation anywhere else in this codebase)

**Interfaces:**
- Produces: `app.models.report_spec.ReportInboxItem` — columns `id: UUID`, `org_id: UUID`, `created_by: UUID`, `created_at: datetime`, `label: str`, `spec: dict` (JSON). Used by Task 7.

- [ ] **Step 1: Add the model**

Append to `backend/app/models/report_spec.py` (after `SavedReport`):

```python
class ReportInboxItem(Base):
    """A live reference to some result a user captured with "Send to report" -
    an org-scoped scratch item, not a finished report. `spec` is one
    ReportSectionSpec-shaped dict (see app.services.reports); resolved fresh
    whenever it's added to a report and that report is rendered, same as
    every other section - nothing about the underlying result is stored.
    """

    __tablename__ = "report_inbox_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 2: Verify the model imports cleanly**

Run: `cd backend && python -c "from app.models.report_spec import ReportInboxItem; print(ReportInboxItem.__tablename__)"`
Expected: `report_inbox_items`

- [ ] **Step 3: Write the migration**

Find the current head first:

Run: `cd backend && python -m alembic heads`
Expected: one revision id printed, e.g. `d47b6e19a3c8 (head)` — use that exact id as `down_revision` below, and generate a new 12-hex-char id of your own for `revision` (any unique lowercase hex string not already used under `alembic/versions/`).

Create `backend/alembic/versions/<new_revision>_report_inbox_items.py`:

```python
"""report inbox items (send to report capture)

Revision ID: <new_revision>
Revises: <head_from_step_3>
Create Date: 2026-07-15

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<new_revision>"  # pragma: allowlist secret
down_revision = "<head_from_step_3>"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_inbox_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_inbox_items_org_id", "report_inbox_items", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_report_inbox_items_org_id", table_name="report_inbox_items")
    op.drop_table("report_inbox_items")
```

- [ ] **Step 4: Verify the migration chain is valid**

Run: `cd backend && python -m alembic heads`
Expected: `<new_revision> (head)` — confirms your migration is now the sole head (no branching).

Note: tests and `dev_server.py` both create tables via `Base.metadata.create_all` (see `backend/tests/conftest.py:57` and `backend/dev_server.py:72`), not Alembic — so this migration isn't exercised by the test suite, only by real Postgres deployments. That's why this task has no test step beyond the import/heads checks above.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/report_spec.py backend/alembic/versions/
git commit -m "feat: add ReportInboxItem model and migration for report capture inbox"
```

---

## Task 2: Relocate kind-report dispatch into the service layer

**Files:**
- Modify: `backend/app/services/reports.py`
- Modify: `backend/app/api/routes/reports.py:1-115` (imports + `_parse_lead_times` + `_build`)
- Test: `backend/tests/test_report_builder.py`, `backend/tests/test_bi.py` — no new tests; this is a pure refactor and the existing kind-report tests (`test_narrate_report_kind`, plus every `/reports/{kind}` test) must keep passing unchanged.

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.services.reports.build_report_by_kind(db, org_id, kind, *, horizon_start, horizon_end, dimension, lead_times, dataset_id) -> Report`. Used by Task 5's `kind_report` resolver and by the two routes this task relocates from.

Why: `routes/reports.py` already has a private `_build(...)` dispatcher (lines 86-114) that does exactly what the `kind_report` capture type needs - it just lives in the wrong layer (routes, not services) for the service-layer resolver in Task 5 to call it without an inverted import (services importing from routes would be backwards; the reverse - routes importing from services - is how every other part of this codebase is layered).

- [ ] **Step 1: Read the current `_build` and `_parse_lead_times` in `routes/reports.py`**

They're at `backend/app/api/routes/reports.py:44-83` (`_parse_lead_times`) and `:86-114` (`_build`). Both are pasted whole into Step 2 below, unchanged except `_build` is renamed to a public `build_report_by_kind` and both move files.

- [ ] **Step 2: Add the relocated functions to `services/reports.py`**

`uuid`, `date`, `HTTPException`, and `status` are already imported at the top of `backend/app/services/reports.py` (lines 6, 8, 12) - nothing to add for those. Two import lines need extending, and one is new:

Change line 25 from:

```python
from app.services.mrp import compute_mrp_plan
```

to:

```python
from app.services.mrp import compute_mrp_plan, resolve_horizon
```

Change line 26 from:

```python
from app.services.rca import RcaResult, compute_rca
```

to:

```python
from app.services.rca import DIMENSIONS, RcaResult, compute_rca
```

Add this new import line (e.g. right after line 17's `from app.models.dashboard import Chart`):

```python
from app.models.bom import Item
```

Then append this to `backend/app/services/reports.py`, right after `build_dataset_report` (after line 478, before the "Custom report builder" comment block that currently starts at line 481):

```python
# --------------------------------------------------------------------------- #
# Kind-report dispatch - shared by the standalone /reports/{kind} routes and
# the custom-report-builder "kind_report" capture type (see ReportSectionSpec
# below): both need "given a kind name and its params, build the Report."
# --------------------------------------------------------------------------- #
async def _parse_lead_times(
    db: AsyncSession, org_id: uuid.UUID, raw: str | None
) -> dict[uuid.UUID, int]:
    """`lead_times=<item_id>:<days>,<item_id>:<days>` - the scenario report's
    overrides live in the query string so a report is one GET and downloads
    from a plain link, like every other kind."""
    if not raw or not raw.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "scenario reports need lead_times=<item_id>:<days>[,<item_id>:<days>]",
        )

    overrides: dict[uuid.UUID, int] = {}
    for pair in raw.split(","):
        item_raw, sep, days_raw = pair.strip().partition(":")
        if not sep:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Malformed lead_times entry: {pair}")
        try:
            item_id = uuid.UUID(item_raw)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid item_id: {item_raw}") from None
        try:
            days = int(days_raw)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid lead time: {days_raw}") from None
        if days < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"lead_time_days must be non-negative (item {item_raw})"
            )
        overrides[item_id] = days

    found = (
        await db.execute(select(Item.id).where(Item.org_id == org_id, Item.id.in_(overrides.keys())))
    ).scalars().all()
    missing = overrides.keys() - set(found)
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown item(s) for this org: {', '.join(str(m) for m in missing)}"
        )
    return overrides


async def build_report_by_kind(
    db: AsyncSession,
    org_id: uuid.UUID,
    kind: str,
    *,
    horizon_start: date | None,
    horizon_end: date | None,
    dimension: str,
    lead_times: str | None,
    dataset_id: str | None,
) -> Report:
    if kind == "mrp-plan":
        hs, he = resolve_horizon(horizon_start, horizon_end)
        return await build_mrp_plan_report(db, org_id, hs, he)
    if kind == "inventory-analytics":
        return await build_inventory_analytics_report(db, org_id)
    if kind == "rca":
        if dimension not in DIMENSIONS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"dimension must be one of {sorted(DIMENSIONS)}"
            )
        return await build_rca_report(db, org_id, dimension)
    if kind == "dataset":
        if not dataset_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "dataset reports need dataset_id")
        dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
        return await build_dataset_report(db, org_id, dataset)
    # kind == "scenario" - validated by the caller against KINDS.
    return await build_scenario_report(db, org_id, await _parse_lead_times(db, org_id, lead_times))
```

- [ ] **Step 3: Update `routes/reports.py` to delegate instead of defining these itself**

In `backend/app/api/routes/reports.py`, delete the whole `_parse_lead_times` function (lines 44-83) and the whole `_build` function (lines 86-114).

Change the import block (lines 19-36) - add `build_report_by_kind` to the existing `from app.services.reports import (...)` list, and remove now-unused imports that only existed for the deleted functions:

```python
from app.services.reports import (
    FORMATS,
    KINDS,
    NARRATION_STYLES,
    Report,
    ReportNarration,
    ReportSectionSpec,
    build_custom_report,
    build_dataset_report,
    build_inventory_analytics_report,
    build_mrp_plan_report,
    build_rca_report,
    build_report_by_kind,
    build_scenario_report,
    get_saved_report_or_404,
    list_saved_reports,
    narrate_report,
    render,
)
```

Also remove these now-unused imports from the top of the file (they were only used by the deleted `_build`/`_parse_lead_times`): `from app.models.bom import Item`, `from app.services.mrp import resolve_horizon`, `from app.services.rca import DIMENSIONS`. Keep everything else as-is.

Update the two call sites. In `get_report` (was calling `_build(...)`, now calls `build_report_by_kind(...)` - same arguments, same order, just the new name):

```python
    report = await build_report_by_kind(
        db,
        current.org_id,
        kind,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        dimension=dimension,
        lead_times=lead_times,
        dataset_id=dataset_id,
    )
```

Same change in `narrate_report_kind`.

- [ ] **Step 4: Run the existing test suite to confirm no regression**

Run: `cd backend && python -m pytest tests/test_report_builder.py tests/test_bi.py -q`
Expected: all tests pass (same count as before this task - this is a pure relocation, no behavior change).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reports.py backend/app/api/routes/reports.py
git commit -m "refactor: move kind-report dispatch into services.reports as build_report_by_kind"
```

---

## Task 3: Broaden `ReportSectionSpec`; generalize resolution to `list[Section]`; add `sql` type

**Files:**
- Modify: `backend/app/services/reports.py`
- Modify: `backend/app/api/routes/reports.py` (two call sites of `build_custom_report`)
- Test: `backend/tests/test_report_builder.py`

**Interfaces:**
- Consumes: `app.services.sql_query.run_sql(dataset, sql, *, timeout_seconds=10.0, row_limit=500) -> dict` (existing, returns `{"columns": [...], "rows": [{...}]}` - confirmed in `backend/app/services/sql_query.py:98-141`).
- Produces: `app.services.reports.ReportSectionSpec` now accepts `type: Literal[..., "engine", "kind_report", "rca_case", "sql"]` with new optional fields `engine_path`, `engine_body`, `report_kind`, `report_params`, `case_id`, `sql_text`. `_resolve_sections(db, current, spec) -> list[Section]` replaces `_resolve_section(db, org_id, spec) -> Section`. `build_custom_report(db, current, name, sections) -> Report` now takes `current: CurrentUser` instead of `org_id`. Tasks 4-6 add the `engine`/`kind_report`/`rca_case` branches inside `_resolve_sections`; this task adds the `sql` branch and the plumbing all four new branches share.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_report_builder.py` (near the other `table` tests, after `test_table_section_escapes_formula_injection_in_csv_and_xlsx`):

```python
async def test_sql_section_renders_in_report(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "SQL section",
            "sections": [{
                "type": "sql", "title": "Raw query", "dataset_id": dataset_id,
                "sql_text": "SELECT sku, qty FROM dataset ORDER BY sku",
            }],
        },
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Raw query" in res.content
    assert b"A-1" in res.content


async def test_sql_section_requires_dataset_id_and_sql_text(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Bad SQL section", "sections": [{"type": "sql", "title": "Missing fields"}]},
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_report_builder.py::test_sql_section_renders_in_report tests/test_report_builder.py::test_sql_section_requires_dataset_id_and_sql_text -v`
Expected: FAIL - `type` literal doesn't accept `"sql"` yet (Pydantic validation error) / no `sql` branch exists yet.

- [ ] **Step 3: Broaden `ReportSectionSpec` and add the `sql_query` import**

In `backend/app/services/reports.py`, add near the other service imports (line 21-27 area):

```python
from app.services import sql_query as sql_query_service
from app.api.deps import CurrentUser
```

Replace the `ReportSectionSpec` class (currently lines 497-507):

```python
class ReportSectionSpec(BaseModel):
    type: Literal[
        "title", "exec_summary", "table", "bi_chart",
        "findings", "risks", "recommendations", "caveats", "text",
        "engine", "kind_report", "rca_case", "sql",
    ]
    title: str | None = None
    text: str | None = None
    items: list[str] | None = None
    dataset_id: uuid.UUID | None = None
    query: query_service.DatasetQueryRequest | None = None
    chart_id: uuid.UUID | None = None
    # engine - one of the 7 analytics-engine pages (all share EngineScreen)
    engine_path: str | None = None
    engine_body: dict | None = None
    # kind_report - mrp-plan / inventory-analytics / rca / scenario / dataset
    report_kind: str | None = None
    report_params: dict | None = None
    # rca_case - the fishbone case-management tool
    case_id: uuid.UUID | None = None
    # sql - SQL Editor's raw-SQL results (not a structured DatasetQueryRequest,
    # so it can't reuse "table")
    sql_text: str | None = None
```

- [ ] **Step 4: Generalize `_resolve_section` to `_resolve_sections`, threading `current` through**

Replace `_resolve_section` (currently lines 510-556) with:

```python
async def _resolve_sections(db: AsyncSession, current: CurrentUser, spec: ReportSectionSpec) -> list[Section]:
    org_id = current.org_id

    if spec.type in ("title", "exec_summary", "text"):
        lines = [line for line in (spec.text or "").splitlines() if line.strip()]
        return [Section(
            title=spec.title or spec.type.replace("_", " ").title(),
            columns=["Text"],
            rows=[[line] for line in lines] or [[EM_DASH]],
        )]

    if spec.type in _BULLET_LABELS:
        return [Section(
            title=spec.title or spec.type.title(),
            columns=[_BULLET_LABELS[spec.type]],
            rows=[[item] for item in (spec.items or [])] or [[EM_DASH]],
        )]

    if spec.type == "table":
        if spec.dataset_id is None or spec.query is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{spec.type}' section needs dataset_id and query")
        dataset = await ds_service.get_dataset_or_404(db, org_id, spec.dataset_id)
        result = await query_service.run_query(db, dataset, org_id, spec.query)
        columns = result["columns"]
        return [Section(
            title=spec.title or dataset.name,
            columns=columns,
            rows=[[EM_DASH if r.get(c) is None else str(r.get(c)) for c in columns] for r in result["rows"]],
        )]

    if spec.type == "sql":
        if spec.dataset_id is None or not spec.sql_text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "'sql' section needs dataset_id and sql_text")
        dataset = await ds_service.get_dataset_or_404(db, org_id, spec.dataset_id)
        result = await sql_query_service.run_sql(dataset, spec.sql_text)
        columns = result["columns"]
        return [Section(
            title=spec.title or dataset.name,
            columns=columns,
            rows=[[EM_DASH if r.get(c) is None else str(r.get(c)) for c in columns] for r in result["rows"]],
        )]

    if spec.type == "bi_chart":
        # replay a saved Chart's query, same as GET /bi/charts/{id}/data - a
        # table is the correct rendering here too, since a PDF/CSV export
        # can't embed a live chart anyway.
        if spec.chart_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "'bi_chart' section needs chart_id")
        chart = (
            await db.execute(select(Chart).where(Chart.id == spec.chart_id, Chart.org_id == org_id))
        ).scalar_one_or_none()
        if chart is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Chart not found: {spec.chart_id}")
        dataset = await ds_service.get_dataset_or_404(db, org_id, chart.dataset_id)
        query = query_service.DatasetQueryRequest.model_validate(chart.query_config)
        result = await query_service.run_query(db, dataset, org_id, query)
        columns = result["columns"]
        return [Section(
            title=spec.title or chart.name,
            columns=columns,
            rows=[[EM_DASH if r.get(c) is None else str(r.get(c)) for c in columns] for r in result["rows"]],
        )]

    if spec.type == "engine":
        return await _resolve_engine_sections(db, current, spec)

    if spec.type == "kind_report":
        return await _resolve_kind_report_sections(db, org_id, spec)

    # spec.type == "rca_case"
    return await _resolve_rca_case_sections(db, org_id, spec)
```

(`_resolve_engine_sections`, `_resolve_kind_report_sections`, and `_resolve_rca_case_sections` don't exist yet - Tasks 4, 5, 6 add them. This task's `sql` test passes without them since it never reaches those branches; leave them undefined for now, which is fine because Python doesn't resolve names inside a function body until the branch actually runs.)

- [ ] **Step 5: Update `build_custom_report` to flatten and take `current`**

Replace `build_custom_report` (currently lines 559-570):

```python
async def build_custom_report(
    db: AsyncSession, current: CurrentUser, name: str, sections: list[ReportSectionSpec]
) -> Report:
    resolved: list[Section] = []
    for s in sections:
        resolved.extend(await _resolve_sections(db, current, s))
    return Report(
        slug="custom",
        title=name,
        subtitle=f"{len(resolved)} section(s)",
        org_name=await _org_name(db, current.org_id),
        generated_at=_now(),
        sections=resolved,
    )
```

- [ ] **Step 6: Update the two call sites in `routes/reports.py`**

In `render_custom_report` and `narrate_custom_report` (both in `backend/app/api/routes/reports.py`), change:

```python
    report = await build_custom_report(db, current.org_id, saved.name, sections)
```

to:

```python
    report = await build_custom_report(db, current, saved.name, sections)
```

(Both call sites already have `current: CurrentUser` in scope as a route parameter - no new imports needed there.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_report_builder.py -q`
Expected: all tests pass, including the two new ones from Step 1.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/reports.py backend/app/api/routes/reports.py backend/tests/test_report_builder.py
git commit -m "feat: generalize report section resolution to many-sections-per-spec; add sql section type"
```

---

## Task 4: `engine` section type - generic analytics-engine capture

**Files:**
- Modify: `backend/app/services/reports.py`
- Test: `backend/tests/test_report_builder.py`

**Interfaces:**
- Consumes: the 7 analytics route handlers, called directly as plain async functions (not over HTTP): `oee_compute(req, current, db)`, `analyze_downtime(req, current, db)`, `compute_throughput(req, current, db)`, `compute_scrap(req, current, db)`, `analyze_shifts(req, current, db)`, `analyze_spc(req, current, db)`, `plant_summary(req, current, db)` - all in `app.api.routes.{oee,downtime,throughput,scrap,shifts,spc,plant_summary}`, all confirmed to have no import back to `app.services.reports` or `app.api.routes.reports` (no circular import risk).
- Produces: `_resolve_engine_sections(db, current, spec) -> list[Section]`, called from Task 3's `_resolve_sections`. `_flatten_response_to_sections(title, data) -> list[Section]` (also directly testable).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_report_builder.py`:

```python
async def test_engine_section_resolves_oee_into_summary_and_breakdown_tables(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(
        client, token,
        content=b"planned,operating,total,good,machine\n480,420,1000,950,M1\n480,400,900,850,M2\n",
        filename="oee.csv",
    )

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "OEE capture",
            "sections": [{
                "type": "engine", "title": "OEE",
                "engine_path": "/analytics/oee",
                "engine_body": {
                    "dataset_id": dataset_id,
                    "planned_time_col": "planned", "operating_time_col": "operating",
                    "total_units_col": "total", "good_units_col": "good",
                    "machine_col": "machine",
                },
            }],
        },
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    # a "Summary" table (scalar KPI fields) and a "Machines" table (the
    # list[OeeMachineResult] field) should both have rendered as sections
    assert b"OEE" in res.content
    assert b"M1" in res.content
    assert b"M2" in res.content


async def test_engine_section_rejects_unknown_path(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Bad engine",
            "sections": [{"type": "engine", "engine_path": "/analytics/not-a-real-engine", "engine_body": {}}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 400


async def test_flatten_response_to_sections_scalars_and_lists():
    from app.services.reports import _flatten_response_to_sections

    sections = _flatten_response_to_sections("Test", {
        "oee": 72.5,
        "performance_estimated": False,
        "machines": [{"machine": "M1", "oee": 70.0}, {"machine": "M2", "oee": 75.0}],
        "revenue_impact": {"unit_price": 2.5, "total_lost": 100.0, "per_machine": [{"machine": "M1", "total_lost": 50.0}]},
        "trend": [],
    })

    titles = [s.title for s in sections]
    assert "Test — Summary" in titles
    assert "Test — Machines" in titles
    assert "Test — Revenue Impact — Per Machine" in titles
    # "trend" was an empty list - no section for it
    assert not any("Trend" in t for t in titles)

    summary = next(s for s in sections if s.title == "Test — Summary")
    summary_labels = [row[0] for row in summary.rows]
    assert "Oee" in summary_labels
    assert "Performance Estimated" in summary_labels
    # a nested dict's own scalar fields join the summary, prefixed
    assert any("Revenue Impact" in label for label in summary_labels)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_report_builder.py::test_engine_section_resolves_oee_into_summary_and_breakdown_tables tests/test_report_builder.py::test_engine_section_rejects_unknown_path tests/test_report_builder.py::test_flatten_response_to_sections_scalars_and_lists -v`
Expected: FAIL - `_resolve_engine_sections`/`_flatten_response_to_sections` don't exist yet.

- [ ] **Step 3: Add the imports**

Add to the import block in `backend/app/services/reports.py`:

```python
from typing import Any, Callable

from pydantic import ValidationError

from app.api.routes.downtime import DowntimeAnalyzeRequest, analyze_downtime
from app.api.routes.oee import OeeComputeRequest, oee_compute
from app.api.routes.plant_summary import PlantSummaryRequest, plant_summary
from app.api.routes.scrap import ScrapComputeRequest, compute_scrap
from app.api.routes.shifts import ShiftAnalyzeRequest, analyze_shifts
from app.api.routes.spc import SPCRequest, analyze_spc
from app.api.routes.throughput import ThroughputComputeRequest, compute_throughput
```

(`Literal` is already imported from `typing` at line 10 - extend that line to `from typing import Any, Callable, Literal`. `BaseModel` is already imported from `pydantic` at line 13 - extend that line to `from pydantic import BaseModel, ValidationError`.)

- [ ] **Step 4: Add the flattener and the engine dispatch table + resolver**

Add this above `_resolve_sections` (which Task 3 just added) in `backend/app/services/reports.py`:

```python
def _cell(v: Any) -> str:
    if v is None:
        return EM_DASH
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        return _num(v)
    return str(v)


def _flatten_response_to_sections(title: str, data: dict[str, Any]) -> list[Section]:
    """Turn one analytics-engine response (already .model_dump()-ed) into
    report Sections, with no per-engine field names hardcoded: scalar fields
    join one "Summary" table; each list[dict] field becomes its own named
    table; a nested dict (e.g. OEE's revenue_impact/teep/six_big_losses) is
    flattened one level deep - its own scalars join the parent Summary under
    a prefixed label, its own list[dict] fields become their own tables.
    Empty/None fields are skipped so the Summary table isn't padded with
    "not applicable for this dataset" rows."""
    summary_rows: list[list[str]] = []
    sections: list[Section] = []

    def label(field: str) -> str:
        return field.replace("_", " ").title()

    def add_list(section_title: str, items: list) -> None:
        if not items or not isinstance(items[0], dict):
            return
        columns = list(items[0].keys())
        sections.append(Section(
            title=section_title,
            columns=[label(c) for c in columns],
            rows=[[_cell(row.get(c)) for c in columns] for row in items],
        ))

    for field, value in data.items():
        if value is None:
            continue
        if isinstance(value, list):
            add_list(f"{title} — {label(field)}", value)
        elif isinstance(value, dict):
            for sub_field, sub_value in value.items():
                if sub_value is None:
                    continue
                if isinstance(sub_value, list):
                    add_list(f"{title} — {label(field)} — {label(sub_field)}", sub_value)
                elif not isinstance(sub_value, dict):
                    summary_rows.append([f"{label(field)}: {label(sub_field)}", _cell(sub_value)])
        else:
            summary_rows.append([label(field), _cell(value)])

    if summary_rows:
        sections.insert(0, Section(title=f"{title} — Summary", columns=["Metric", "Value"], rows=summary_rows))
    return sections


_ENGINE_HANDLERS: dict[str, tuple[type[BaseModel], Callable]] = {
    "/analytics/oee": (OeeComputeRequest, oee_compute),
    "/analytics/downtime": (DowntimeAnalyzeRequest, analyze_downtime),
    "/analytics/throughput": (ThroughputComputeRequest, compute_throughput),
    "/analytics/scrap": (ScrapComputeRequest, compute_scrap),
    "/analytics/shifts": (ShiftAnalyzeRequest, analyze_shifts),
    "/analytics/spc": (SPCRequest, analyze_spc),
    "/analytics/plant-summary": (PlantSummaryRequest, plant_summary),
}


async def _resolve_engine_sections(db: AsyncSession, current: CurrentUser, spec: ReportSectionSpec) -> list[Section]:
    if spec.engine_path not in _ENGINE_HANDLERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown engine_path: {spec.engine_path}")
    request_model, handler = _ENGINE_HANDLERS[spec.engine_path]
    try:
        req = request_model.model_validate(spec.engine_body or {})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid engine_body: {exc}") from exc
    response = await handler(req, current, db)
    title = spec.title or spec.engine_path.rsplit("/", 1)[-1].replace("-", " ").title()
    return _flatten_response_to_sections(title, response.model_dump())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_report_builder.py -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/reports.py backend/tests/test_report_builder.py
git commit -m "feat: add engine section type - generic capture for all 7 analytics-engine pages"
```

---

## Task 5: `kind_report` section type

**Files:**
- Modify: `backend/app/services/reports.py`
- Test: `backend/tests/test_report_builder.py`

**Interfaces:**
- Consumes: `build_report_by_kind` from Task 2.
- Produces: `_resolve_kind_report_sections(db, org_id, spec) -> list[Section]`, called from Task 3's `_resolve_sections`.

Deviation from the design spec, made here: the spec suggested the `dataset` kind reuse the `table` capture type instead of `kind_report`. Since Task 2 already relocated a dispatcher that handles `dataset` identically to the other 4 kinds (it just needs a `dataset_id` param, already part of `report_params`), special-casing it would be more code, not less - `kind_report` supports all 5 of `KINDS` uniformly.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_report_builder.py`:

```python
async def test_kind_report_section_mrp_plan(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "MRP capture",
            "sections": [{
                "type": "kind_report", "report_kind": "mrp-plan",
                "report_params": {"horizon_start": "2026-01-01", "horizon_end": "2026-04-01"},
            }],
        },
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Suggested actions" in res.content
    assert b"Full plan" in res.content


async def test_kind_report_section_inventory_analytics_needs_no_params(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Inventory capture",
            "sections": [{"type": "kind_report", "report_kind": "inventory-analytics"}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Item positions" in res.content


async def test_kind_report_section_rejects_unknown_kind(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Bad kind", "sections": [{"type": "kind_report", "report_kind": "not-a-kind"}]},
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_report_builder.py::test_kind_report_section_mrp_plan tests/test_report_builder.py::test_kind_report_section_inventory_analytics_needs_no_params tests/test_report_builder.py::test_kind_report_section_rejects_unknown_kind -v`
Expected: FAIL - `_resolve_kind_report_sections` doesn't exist yet.

- [ ] **Step 3: Add the resolver**

Add to `backend/app/services/reports.py`, next to `_resolve_engine_sections`:

```python
async def _resolve_kind_report_sections(db: AsyncSession, org_id: uuid.UUID, spec: ReportSectionSpec) -> list[Section]:
    if spec.report_kind not in KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"report_kind must be one of {sorted(KINDS)}")
    params = spec.report_params or {}
    horizon_start = date.fromisoformat(params["horizon_start"]) if params.get("horizon_start") else None
    horizon_end = date.fromisoformat(params["horizon_end"]) if params.get("horizon_end") else None
    report = await build_report_by_kind(
        db, org_id, spec.report_kind,
        horizon_start=horizon_start, horizon_end=horizon_end,
        dimension=params.get("dimension", "supplier"),
        lead_times=params.get("lead_times"),
        dataset_id=params.get("dataset_id"),
    )
    return report.sections
```

Wire it into `_resolve_sections`'s `kind_report` branch (already added in Task 3, currently calling this same name - no change needed there, this step just makes the name resolve).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_report_builder.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reports.py backend/tests/test_report_builder.py
git commit -m "feat: add kind_report section type - reuses existing build_*_report functions"
```

---

## Task 6: `rca_case` section type

**Files:**
- Modify: `backend/app/services/reports.py`
- Test: `backend/tests/test_report_builder.py`

**Interfaces:**
- Consumes: `app.services.rca_cases.get_case_or_404(db, org_id, case_id) -> RcaCase` (raises `ValueError` if missing, per `backend/app/services/rca_cases.py:59-64`), `list_nodes(db, org_id, case_id) -> list[RcaNode]`, `list_actions(db, org_id, case_id) -> list[RcaAction]`.
- Produces: `_resolve_rca_case_sections(db, org_id, spec) -> list[Section]`, called from Task 3's `_resolve_sections`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_report_builder.py`:

```python
async def test_rca_case_section_renders_causes_and_actions(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    case = await client.post(
        "/rca-cases/cases", headers=headers,
        json={"dataset_id": dataset_id, "title": "Line 3 OEE drop", "problem_statement": "OEE fell 15pts"},
    )
    assert case.status_code == 201
    case_id = case.json()["id"]

    node = await client.post(
        f"/rca-cases/cases/{case_id}/nodes", headers=headers,
        json={"text": "Changeover took too long", "node_type": "cause"},
    )
    assert node.status_code == 201

    action = await client.post(
        f"/rca-cases/cases/{case_id}/actions", headers=headers,
        json={"description": "Standardize changeover checklist"},
    )
    assert action.status_code == 201

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "RCA case capture", "sections": [{"type": "rca_case", "case_id": case_id}]},
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Changeover took too long" in res.content
    assert b"Standardize changeover checklist" in res.content


async def test_rca_case_section_404s_on_unknown_case(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Bad case",
            "sections": [{"type": "rca_case", "case_id": "00000000-0000-0000-0000-000000000000"}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_report_builder.py::test_rca_case_section_renders_causes_and_actions tests/test_report_builder.py::test_rca_case_section_404s_on_unknown_case -v`
Expected: FAIL - `_resolve_rca_case_sections` doesn't exist yet.

- [ ] **Step 3: Add the resolver**

Add the import to `backend/app/services/reports.py`:

```python
from app.services import rca_cases as rca_cases_service
```

Add the resolver next to `_resolve_kind_report_sections`:

```python
async def _resolve_rca_case_sections(db: AsyncSession, org_id: uuid.UUID, spec: ReportSectionSpec) -> list[Section]:
    if spec.case_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'rca_case' section needs case_id")
    try:
        case = await rca_cases_service.get_case_or_404(db, org_id, spec.case_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    nodes = await rca_cases_service.list_nodes(db, org_id, spec.case_id)
    actions = await rca_cases_service.list_actions(db, org_id, spec.case_id)

    causes = Section(
        title=spec.title or f"{case.title} — Causes",
        columns=["Text", "Type", "Category", "Status"],
        rows=[[n.text, n.node_type, n.category or EM_DASH, n.status] for n in nodes] or [[EM_DASH] * 4],
    )
    actions_section = Section(
        title=f"{case.title} — Actions",
        columns=["Description", "Owner", "Status", "OEE Δ"],
        rows=[
            [
                a.description,
                a.owner or EM_DASH,
                a.status,
                _num(a.verification["delta_oee"]) if a.verification else EM_DASH,
            ]
            for a in actions
        ] or [[EM_DASH] * 4],
    )
    return [causes, actions_section]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_report_builder.py -q`
Expected: all tests pass.

- [ ] **Step 5: Run the full backend suite to confirm nothing else broke**

Run: `cd backend && python -m pytest -q`
Expected: all tests pass (this is the last backend resolver task - a full-suite run here catches any cross-file regression from Tasks 2-6 before moving to the inbox API).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/reports.py backend/tests/test_report_builder.py
git commit -m "feat: add rca_case section type - captures a case's causes and actions as tables"
```

---

## Task 7: Inbox API - `POST/GET/DELETE /reports/inbox`

**Files:**
- Modify: `backend/app/api/routes/reports.py`
- Test: `backend/tests/test_report_builder.py`

**Interfaces:**
- Consumes: `app.models.report_spec.ReportInboxItem` (Task 1), `ReportSectionSpec` (Task 3).
- Produces: `POST /reports/inbox`, `GET /reports/inbox`, `DELETE /reports/inbox/{item_id}` - used by Task 8's frontend hooks.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_report_builder.py`:

```python
async def test_inbox_create_list_and_delete(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/inbox", headers=headers,
        json={"label": "OEE — July", "type": "text", "text": "captured content"},
    )
    assert created.status_code == 201
    item = created.json()
    assert item["label"] == "OEE — July"
    assert item["spec"]["type"] == "text"

    listed = await client.get("/reports/inbox", headers=headers)
    assert listed.status_code == 200
    assert any(i["id"] == item["id"] for i in listed.json())

    deleted = await client.delete(f"/reports/inbox/{item['id']}", headers=headers)
    assert deleted.status_code == 204
    assert not any(i["id"] == item["id"] for i in (await client.get("/reports/inbox", headers=headers)).json())


async def test_inbox_delete_allowed_for_creator_not_other_members(client):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="ibowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    created = await client.post(
        "/reports/inbox", headers=headers,
        json={"label": "Owner's capture", "type": "text", "text": "hi"},
    )
    item_id = created.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="ibmember@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "ibmember@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # a different member can't delete someone else's inbox item...
    res = await client.delete(f"/reports/inbox/{item_id}", headers=member_headers)
    assert res.status_code == 403

    # ...but the creator can delete their own.
    res = await client.delete(f"/reports/inbox/{item_id}", headers=headers)
    assert res.status_code == 204


async def test_inbox_delete_allowed_for_owner_deleting_someone_elses_item(client):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="ib2owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="ib2member@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "ib2member@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created = await client.post(
        "/reports/inbox", headers=member_headers,
        json={"label": "Member's capture", "type": "text", "text": "hi"},
    )
    item_id = created.json()["id"]

    # the org owner can delete a different member's inbox item.
    res = await client.delete(f"/reports/inbox/{item_id}", headers=headers)
    assert res.status_code == 204


async def test_inbox_cross_org_isolation(client):
    token_a = await _register_and_login(client, email="iba@example.com", org_name="InboxOrgA")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    created = await client.post(
        "/reports/inbox", headers=headers_a,
        json={"label": "OrgA capture", "type": "text", "text": "secret"},
    )
    item_id = created.json()["id"]

    token_b = await _register_and_login(client, email="ibb@example.com", org_name="InboxOrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    listed_b = await client.get("/reports/inbox", headers=headers_b)
    assert not any(i["id"] == item_id for i in listed_b.json())
    assert (await client.delete(f"/reports/inbox/{item_id}", headers=headers_b)).status_code == 404
```

`test_inbox_delete_allowed_for_creator_not_other_members` and `test_inbox_delete_allowed_for_owner_deleting_someone_elses_item` reference a module-level `test_engine` fixture the way `test_datasets.py::test_rbac_blocks_non_owner_from_delete` does (`client, test_engine` fixture args) - both tests must take `test_engine` as a second fixture parameter, matching that existing test's signature exactly:

```python
async def test_inbox_delete_allowed_for_creator_not_other_members(client, test_engine):
```

```python
async def test_inbox_delete_allowed_for_owner_deleting_someone_elses_item(client, test_engine):
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_report_builder.py::test_inbox_create_list_and_delete tests/test_report_builder.py::test_inbox_delete_allowed_for_creator_not_other_members tests/test_report_builder.py::test_inbox_delete_allowed_for_owner_deleting_someone_elses_item tests/test_report_builder.py::test_inbox_cross_org_isolation -v`
Expected: FAIL - `/reports/inbox` routes don't exist yet (404 on every request).

- [ ] **Step 3: Add the model import and request/response models**

Add to the imports in `backend/app/api/routes/reports.py`:

```python
from app.models.report_spec import ReportInboxItem, SavedReport
```

(`SavedReport` is already imported there as `from app.models.report_spec import SavedReport` - just extend that one line.)

Add these models near `SavedReportWriteRequest`/`SavedReportResponse`:

```python
class InboxItemCreateRequest(ReportSectionSpec):
    label: str


class InboxItemResponse(BaseModel):
    id: str
    label: str
    spec: dict
    created_by: str
    created_at: str


def _inbox_item_to_response(item: ReportInboxItem) -> InboxItemResponse:
    return InboxItemResponse(
        id=str(item.id), label=item.label, spec=item.spec,
        created_by=str(item.created_by), created_at=item.created_at.isoformat(),
    )
```

- [ ] **Step 4: Add the three routes**

Add these right after the existing `/custom/{report_id}/narrate` route and before the `/{kind}` catch-all (same reasoning as the existing comment above `/custom`: a literal path must be registered before the `/{kind}` wildcard or it gets shadowed):

```python
@router.post("/inbox", response_model=InboxItemResponse, status_code=status.HTTP_201_CREATED)
async def create_inbox_item(
    req: InboxItemCreateRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    spec = ReportSectionSpec(**req.model_dump(exclude={"label"}))
    item = ReportInboxItem(
        org_id=current.org_id, created_by=current.id, label=req.label,
        spec=spec.model_dump(mode="json"),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _inbox_item_to_response(item)


@router.get("/inbox", response_model=list[InboxItemResponse])
async def list_inbox_items(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(ReportInboxItem)
            .where(ReportInboxItem.org_id == current.org_id)
            .order_by(ReportInboxItem.created_at.desc())
        )
    ).scalars().all()
    return [_inbox_item_to_response(r) for r in rows]


@router.delete("/inbox/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbox_item(
    item_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    item = (
        await db.execute(
            select(ReportInboxItem).where(ReportInboxItem.id == item_id, ReportInboxItem.org_id == current.org_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbox item not found")
    if item.created_by != current.id and current.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the item's creator or an owner can delete it")
    await db.delete(item)
    await db.commit()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_report_builder.py -q`
Expected: all tests pass.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && python -m pytest -q`
Expected: all tests pass. This is the last backend task - the whole capture/inbox backend is now complete and independently testable via the API.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/reports.py backend/tests/test_report_builder.py
git commit -m "feat: add POST/GET/DELETE /reports/inbox with creator-or-owner delete RBAC"
```

---

## Task 8: Shared inbox types + `useInbox`/`SendToReportButton`

**Files:**
- Modify: `frontend/src/pages/analytics/shared.tsx`

**Interfaces:**
- Produces:
  - `export interface ReportSectionSpecPayload` - the TS mirror of the backend `ReportSectionSpec`.
  - `export interface InboxItem { id: string; label: string; spec: ReportSectionSpecPayload; created_by: string; created_at: string }`
  - `export function useInbox()` - `useQuery<InboxItem[]>` against `GET /reports/inbox`.
  - `export function SendToReportButton({ spec, label }: { spec: ReportSectionSpecPayload; label: string })` - self-contained button + `POST /reports/inbox` mutation + inline success/error state.
- Used by: Task 9 (`EngineScreen`) and Tasks 10-17 (every other "Send to report" call site).

- [ ] **Step 1: Add the types and hooks**

Add to `frontend/src/pages/analytics/shared.tsx`, right after `useDatasets` (which Task-8-onward call sites will sit near):

```typescript
export interface ReportSectionSpecPayload {
  type:
    | "title" | "exec_summary" | "table" | "bi_chart"
    | "findings" | "risks" | "recommendations" | "caveats" | "text"
    | "engine" | "kind_report" | "rca_case" | "sql";
  title?: string;
  text?: string;
  items?: string[];
  dataset_id?: string;
  // unknown, not Record<string, unknown>: callers pass through whatever
  // useQueryBuilder().toRequestBody() or a Scenario's base_query already
  // produced (inferred object-literal shapes), and this button only ever
  // JSON.stringifies the whole spec - it never reads into `query` itself.
  query?: unknown;
  chart_id?: string;
  engine_path?: string;
  engine_body?: Record<string, unknown>;
  report_kind?: string;
  report_params?: Record<string, unknown>;
  case_id?: string;
  sql_text?: string;
}

export interface InboxItem {
  id: string;
  label: string;
  spec: ReportSectionSpecPayload;
  created_by: string;
  created_at: string;
}

export function useInbox() {
  return useQuery<InboxItem[]>({
    queryKey: ["report-inbox"],
    queryFn: async () => {
      const res = await apiFetch("/reports/inbox");
      if (!res.ok) throw new Error("Failed to load report inbox");
      return res.json();
    },
  });
}

export function SendToReportButton({ spec, label }: { spec: ReportSectionSpecPayload; label: string }) {
  const qc = useQueryClient();
  const send = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/reports/inbox", {
        method: "POST",
        body: JSON.stringify({ label, ...spec }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to send to report");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["report-inbox"] }),
  });

  if (send.isSuccess) {
    return <span className="text-sm text-muted-foreground">Sent to report inbox.</span>;
  }
  return (
    <div className="flex items-center gap-2">
      <Button variant="secondary" size="sm" loading={send.isPending} onClick={() => send.mutate()}>
        Send to report
      </Button>
      {send.isError && <span className="text-sm text-destructive">{(send.error as Error).message}</span>}
    </div>
  );
}
```

(`useMutation`, `useQueryClient`, `apiFetch`, and `Button` are already imported at the top of this file - no new imports needed.)

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. (`SendToReportButton` isn't used anywhere yet, so this only checks it's internally well-typed.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/analytics/shared.tsx
git commit -m "feat: add useInbox and SendToReportButton shared components"
```

---

## Task 9: Wire `EngineScreen` - covers all 7 analytics-engine pages

**Files:**
- Modify: `frontend/src/pages/analytics/shared.tsx`

**Interfaces:**
- Consumes: `SendToReportButton` (Task 8).
- Produces: every `EngineScreen` consumer (`Downtime.tsx`, `Oee.tsx`, `PlantSummary.tsx`, `Scrap.tsx`, `Shifts.tsx`, `Spc.tsx`, `Throughput.tsx`) gets a "Send to report" button under its result for free - no changes needed in any of those 7 files.

- [ ] **Step 1: Capture the last successful compute body**

In `EngineScreen` (`frontend/src/pages/analytics/shared.tsx`), add a ref to hold the exact body of the last successful compute call, and stash it right before the fetch:

```typescript
  const lastComputeBody = useRef<Record<string, unknown> | null>(null);

  const compute = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = { dataset_id: datasetId };
      for (const f of fields) {
        const v = form[f.key];
        if (f.kind === "num") body[f.key] = v ? Number(v) : null;
        else if (f.kind === "select") body[f.key] = v || f.options?.[0];
        else if (v) body[f.key] = v;
      }
      lastComputeBody.current = body;
      const res = await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Compute failed");
      return res.json() as Promise<T>;
    },
    onSuccess: (r) => {
      setError(null);
      setResult(r);
    },
    onError: (e: Error) => setError(e.message),
  });
```

(This replaces the existing `compute` mutation definition - only the added `lastComputeBody.current = body;` line and the new `useRef` declaration above it are new. `useRef` needs adding to the existing `import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";` line at the top of the file - it's already imported there, so no import change is actually needed.)

- [ ] **Step 2: Render the button under the result**

In `EngineScreen`'s return statement, find:

```typescript
      {result !== null && children(result)}
```

Replace with:

```typescript
      {result !== null && (
        <>
          {children(result)}
          <SendToReportButton
            spec={{ type: "engine", engine_path: path, engine_body: lastComputeBody.current ?? {} }}
            label={`${title} — ${new Date().toLocaleString()}`}
          />
        </>
      )}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Manual verification**

Start both servers and confirm the button appears and works on one representative analytics page (all 7 share the same code path, so one confirms the mechanism):

Run: use `preview_start` with the `backend-dev` and `frontend` configurations from `.claude/launch.json` (already present in this repo from earlier work on this branch).

In the browser: log in, navigate to Plant Analytics → OEE, pick a dataset, map the required columns, click "Compute" (or whatever the run button is labeled), confirm results render, then confirm a "Send to report" button appears below the results, click it, and confirm it shows "Sent to report inbox." with no error. Then `GET /reports/inbox` (e.g. via the Network tab, or just trust the next task's picker once built) should show one new item labeled "OEE — <timestamp>".

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/analytics/shared.tsx
git commit -m "feat: wire Send to report into EngineScreen, covering all 7 analytics-engine pages"
```

---

## Task 10: Report Builder - "Add from inbox" picker + narration label relabel

**Files:**
- Modify: `frontend/src/pages/ReportBuilder.tsx`

**Interfaces:**
- Consumes: `useInbox`, `InboxItem` (Task 8).
- Produces: an "Add from inbox" dialog in the section editor, appending selected inbox items' specs to `sections`.

- [ ] **Step 1: Import the inbox hook and add picker state**

In `frontend/src/pages/ReportBuilder.tsx`, change the import line:

```typescript
import { useCharts, useDatasets, useInbox, type InboxItem } from "./analytics/shared";
```

Add state inside `ReportBuilder()`, next to the other `useState` calls:

```typescript
  const inbox = useInbox();
  const [showInboxPicker, setShowInboxPicker] = useState(false);
  const [checkedInboxIds, setCheckedInboxIds] = useState<Set<string>>(new Set());
```

- [ ] **Step 2: Add the delete-inbox-item mutation**

Add next to the `remove` mutation:

```typescript
  const deleteInboxItem = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/reports/inbox/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Delete failed");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["report-inbox"] }),
  });
```

- [ ] **Step 3: Add the "add checked items" action**

Add next to `openAddSection`:

```typescript
  function addFromInbox() {
    const chosen = (inbox.data ?? []).filter((item) => checkedInboxIds.has(item.id));
    setSections((prev) => [...prev, ...chosen.map((item) => ({ ...item.spec, title: item.spec.title ?? item.label }))]);
    setCheckedInboxIds(new Set());
    setShowInboxPicker(false);
  }
```

- [ ] **Step 4: Add the "Add from inbox" button and dialog**

In the JSX, find:

```typescript
          <Button variant="secondary" size="sm" onClick={openAddSection}>+ Add section</Button>
```

Replace with:

```typescript
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={openAddSection}>+ Add section</Button>
            <Button variant="secondary" size="sm" onClick={() => setShowInboxPicker(true)}>Add from inbox</Button>
          </div>
```

Add the picker dialog right after the existing "Add section" `<Dialog>` (before the closing `</div>` of the component):

```typescript
      <Dialog open={showInboxPicker} onClose={() => setShowInboxPicker(false)} title="Add from inbox">
        <div className="space-y-3">
          {(inbox.data ?? []).length === 0 && (
            <p className="text-sm text-muted-foreground">
              Nothing captured yet — use "Send to report" on any analytics page, BI chart, or RCA case.
            </p>
          )}
          {(inbox.data ?? []).map((item: InboxItem) => (
            <div key={item.id} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
              <label className="flex min-w-0 items-center gap-2">
                <input
                  type="checkbox"
                  checked={checkedInboxIds.has(item.id)}
                  onChange={(e) => {
                    setCheckedInboxIds((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(item.id);
                      else next.delete(item.id);
                      return next;
                    });
                  }}
                />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge variant="neutral">{item.spec.type}</Badge>
                    <span className="truncate font-medium">{item.label}</span>
                  </div>
                  <div className="text-xs text-muted-foreground">{new Date(item.created_at).toLocaleString()}</div>
                </div>
              </label>
              <Button variant="ghost" size="sm" onClick={() => deleteInboxItem.mutate(item.id)}>Delete</Button>
            </div>
          ))}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setShowInboxPicker(false)}>Cancel</Button>
            <Button disabled={checkedInboxIds.size === 0} onClick={addFromInbox}>
              Add {checkedInboxIds.size || ""} selected
            </Button>
          </div>
        </div>
      </Dialog>
```

- [ ] **Step 5: Relabel the narration style options**

Find:

```typescript
                  {NARRATION_STYLES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
```

Replace with:

```typescript
                  {NARRATION_STYLES.map((s) => (
                    <option key={s} value={s}>
                      {s === "narrative" ? "Paragraph" : s === "bullets" ? "Bullet points" : "Brief"}
                    </option>
                  ))}
```

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Manual verification**

With both servers running (continuing from Task 9's captured OEE item): open Report Builder, start a new report, click "Add from inbox," confirm the captured item appears with its label and an "engine" badge, check it, click "Add N selected," confirm it appears in the section list, save the report, and confirm the section renders correctly on export (Download HTML).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/ReportBuilder.tsx
git commit -m "feat: add 'Add from inbox' picker to Report Builder; relabel narration styles"
```

---

## Task 11: Wire `Reports.tsx`

**Files:**
- Modify: `frontend/src/pages/Reports.tsx`

**Interfaces:**
- Consumes: `SendToReportButton` (Task 8).

- [ ] **Step 1: Import and add the button**

Change the import line:

```typescript
import { useDatasets, SendToReportButton } from "./analytics/shared";
```

In the JSX, after the `<section><h2>Export</h2>...</section>` block, add:

```typescript
      <section>
        <h2>Send to Report Builder</h2>
        <SendToReportButton
          spec={{
            type: "kind_report",
            report_kind: kind,
            report_params: {
              horizon_start: kind === "mrp-plan" ? horizonStart : undefined,
              horizon_end: kind === "mrp-plan" ? horizonEnd : undefined,
              dimension: kind === "rca" ? dimension : undefined,
              lead_times: kind === "scenario" ? leadTimeParam : undefined,
              dataset_id: kind === "dataset" ? datasetId : undefined,
            },
          }}
          label={`${active.label} — ${today()}`}
        />
      </section>
```

Disable the button when `!ready` by conditionally rendering it instead - wrap the whole `<section>` in `{ready && (...)}`  since `SendToReportButton` has no `disabled` prop and this matches how the existing Download buttons already gate on `ready`:

```typescript
      {ready && (
        <section>
          <h2>Send to Report Builder</h2>
          <SendToReportButton
            spec={{
              type: "kind_report",
              report_kind: kind,
              report_params: {
                horizon_start: kind === "mrp-plan" ? horizonStart : undefined,
                horizon_end: kind === "mrp-plan" ? horizonEnd : undefined,
                dimension: kind === "rca" ? dimension : undefined,
                lead_times: kind === "scenario" ? leadTimeParam : undefined,
                dataset_id: kind === "dataset" ? datasetId : undefined,
              },
            }}
            label={`${active.label} — ${today()}`}
          />
        </section>
      )}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Navigate to Reports, select "MRP plan," set a horizon, confirm the preview renders, then confirm the "Send to Report Builder" button appears and works; repeat for "Root cause analysis" (confirms the `dimension` param path).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Reports.tsx
git commit -m "feat: wire Send to report into the standalone Reports page"
```

---

## Task 12: Wire `Planning.tsx`

**Files:**
- Modify: `frontend/src/pages/Planning.tsx`

**Interfaces:**
- Consumes: `SendToReportButton` (Task 8).

- [ ] **Step 1: Import and add the button**

`Planning.tsx` has no existing import from `analytics/shared`. Add this new import line after the existing `import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";` line (the last import in the file):

```typescript
import { SendToReportButton } from "./analytics/shared";
```

In the "MRP plan" `Card`'s `CardContent`, right after the horizon `Field`s grid (`</div>` closing the `grid gap-4 sm:grid-cols-2 lg:max-w-md` block) and before the `{plan.isError && ...}` line, add:

```typescript
          {plan.data && plan.data.lines.length > 0 && (
            <SendToReportButton
              spec={{ type: "kind_report", report_kind: "mrp-plan", report_params: { horizon_start: horizonStart, horizon_end: horizonEnd } }}
              label={`MRP plan — ${horizonStart} to ${horizonEnd}`}
            />
          )}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Navigate to Planning, confirm an MRP plan with at least one line renders, confirm the "Send to report" button appears and works.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Planning.tsx
git commit -m "feat: wire Send to report into the Planning page's MRP plan"
```

---

## Task 13: Wire `RcaCases.tsx`

**Files:**
- Modify: `frontend/src/pages/RcaCases.tsx`

**Interfaces:**
- Consumes: `SendToReportButton` (Task 8).

- [ ] **Step 1: Import and add the button**

Add to the existing `import { useDatasets } from "@/pages/analytics/shared";` line in `frontend/src/pages/RcaCases.tsx`:

```typescript
import { SendToReportButton, useDatasets } from "@/pages/analytics/shared";
```

In `CaseDetail`, change the `PageHeader`'s `actions` prop from:

```typescript
        actions={
          <Button variant="secondary" onClick={onBack}>
            Back to cases
          </Button>
        }
```

to:

```typescript
        actions={
          <div className="flex gap-2">
            {caseQuery.data && (
              <SendToReportButton spec={{ type: "rca_case", case_id: caseId }} label={caseQuery.data.title} />
            )}
            <Button variant="secondary" onClick={onBack}>
              Back to cases
            </Button>
          </div>
        }
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Navigate to RCA Cases, open a case with at least one cause node and one action, confirm the "Send to report" button appears next to "Back to cases" and works.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/RcaCases.tsx
git commit -m "feat: wire Send to report into RCA case detail view"
```

---

## Task 14: Wire `DashboardDetail.tsx`'s `ChartCard`

**Files:**
- Modify: `frontend/src/pages/bi/DashboardDetail.tsx`

**Interfaces:**
- Consumes: `SendToReportButton` (Task 8).

- [ ] **Step 1: Import and add the button**

Change the import line:

```typescript
import { EChart, SendToReportButton } from "../analytics/shared";
```

In `ChartCard`'s action row, find:

```typescript
        <div className="flex gap-1">
          {/* Keyboard/screen-reader alternative to the drag handle above -
              native HTML5 drag-and-drop has no keyboard equivalent. */}
          <Button variant="ghost" size="icon" onClick={onMoveUp} title="Move up">
            <ArrowUp className="size-4" />
          </Button>
          <Button variant="ghost" size="icon" onClick={onMoveDown} title="Move down">
            <ArrowDown className="size-4" />
          </Button>
          <Button variant="ghost" size="icon" onClick={onDelete} title="Remove from dashboard">
            <Trash2 className="size-4" />
          </Button>
        </div>
```

Add a `SendToReportButton` above this action row instead of squeezing it into the icon-button row (it renders its own label text, not an icon, so it doesn't fit the `size-4` icon-button pattern) - wrap `CardHeader`'s two children in a way that puts the button on its own line inside `CardContent`, right before the chart itself. In `CardContent`, find:

```typescript
      <CardContent>
        {data.isLoading ? (
```

Replace with:

```typescript
      <CardContent className="space-y-2">
        <SendToReportButton spec={{ type: "bi_chart", chart_id: chart.id }} label={chart.name} />
        {data.isLoading ? (
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Navigate to BI Dashboards, open a dashboard with at least one chart, confirm the "Send to report" button appears above the chart and works.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/bi/DashboardDetail.tsx
git commit -m "feat: wire Send to report into saved BI dashboard charts"
```

---

## Task 15: Wire `DatasetDetail.tsx`'s `QueryPanel`

**Files:**
- Modify: `frontend/src/pages/analytics/DatasetDetail.tsx`

**Interfaces:**
- Consumes: `SendToReportButton` (Task 8).

- [ ] **Step 1: Import and add the button**

Change the import line (currently `import { C, ColumnChecklist, QueryBuilderFields, fileSize, fmt, useQueryBuilder, type Dataset } from "./shared";`):

```typescript
import { C, ColumnChecklist, QueryBuilderFields, SendToReportButton, fileSize, fmt, useQueryBuilder, type Dataset } from "./shared";
```

In `QueryPanel`, find:

```typescript
      {query.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle>Results ({query.data.rows.length} rows)</CardTitle>
          </CardHeader>
          <CardContent>
```

Replace with:

```typescript
      {query.isSuccess && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle>Results ({query.data.rows.length} rows)</CardTitle>
            <SendToReportButton spec={{ type: "table", dataset_id: d.id, query: qb.toRequestBody() }} label={d.name} />
          </CardHeader>
          <CardContent>
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Navigate to a dataset's Query tab, run a query, confirm the "Send to report" button appears next to the Results heading and works.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/analytics/DatasetDetail.tsx
git commit -m "feat: wire Send to report into the dataset Query tab"
```

---

## Task 16: Wire `SqlEditor.tsx`

**Files:**
- Modify: `frontend/src/pages/SqlEditor.tsx`

**Interfaces:**
- Consumes: `SendToReportButton` (Task 8).

- [ ] **Step 1: Import and add the button**

Change the import line:

```typescript
import { SendToReportButton, useDatasets } from "@/pages/analytics/shared";
```

Find:

```typescript
      {query.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle>Results ({query.data.rows.length} rows)</CardTitle>
          </CardHeader>
          <CardContent>
```

Replace with:

```typescript
      {query.isSuccess && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle>Results ({query.data.rows.length} rows)</CardTitle>
            <SendToReportButton
              spec={{ type: "sql", dataset_id: effectiveDatasetId, sql_text: sql }}
              label={activeDataset?.name ?? "SQL query"}
            />
          </CardHeader>
          <CardContent>
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Navigate to SQL Editor, run a query, confirm the "Send to report" button appears next to the Results heading; add this item to a report in Report Builder and confirm it renders correctly on export (this exercises the `sql` section type end-to-end, not just its backend test from Task 3).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SqlEditor.tsx
git commit -m "feat: wire Send to report into the SQL Editor"
```

---

## Task 17: Wire `Scenarios.tsx`

**Files:**
- Modify: `frontend/src/pages/Scenarios.tsx`

**Interfaces:**
- Consumes: `SendToReportButton` (Task 8).

- [ ] **Step 1: Import and add the button**

Change the import line:

```typescript
import { SendToReportButton, useDatasets, useQueryBuilder, QueryBuilderFields } from "@/pages/analytics/shared";
```

In `ScenarioList`, each row already has a "Run" and "Delete" button. Add a "Send to report" button per scenario row - find:

```typescript
                  <TD className="flex gap-2">
                    <Button size="sm" variant="secondary" loading={run.isPending} onClick={() => run.mutate(s.id)}>
                      Run
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => del.mutate(s.id)}>
                      Delete
                    </Button>
                  </TD>
```

Replace with:

```typescript
                  <TD className="flex gap-2">
                    <Button size="sm" variant="secondary" loading={run.isPending} onClick={() => run.mutate(s.id)}>
                      Run
                    </Button>
                    <SendToReportButton
                      spec={{ type: "table", dataset_id: s.dataset_id, query: s.base_query }}
                      label={s.name}
                    />
                    <Button size="sm" variant="ghost" onClick={() => del.mutate(s.id)}>
                      Delete
                    </Button>
                  </TD>
```

(`s.base_query` is already typed as `Record<string, unknown>` on the `Scenario` interface at the top of the file, which matches `ReportSectionSpecPayload.query`'s type - no cast needed.)

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Navigate to Scenarios, create a scenario, confirm the "Send to report" button appears in its row and works.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Scenarios.tsx
git commit -m "feat: wire Send to report into the Scenarios what-if page"
```

---

## Task 18: End-to-end verification pass

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Full backend test suite**

Run: `cd backend && python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Full frontend typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Browser walkthrough covering every source type in one report**

With both servers running: capture at least one item from each of the source types added in this plan (an analytics engine via OEE, a kind_report via Reports.tsx, an rca_case, a bi_chart, a table via the dataset Query tab, a sql via SQL Editor). In Report Builder, add all of them to one new report via "Add from inbox," reorder a couple with Up/Down, save, then confirm each of Download HTML/PDF/CSV/XLSX succeeds and the HTML export visibly contains content from every section. Finally, click "Narrate" with each of the three (relabeled) styles and confirm narration text is returned for every section without error.

- [ ] **Step 4: Confirm no regression in the manual-entry path**

In Report Builder, use "+ Add section" (not the inbox) to hand-author a `text` and a `findings` section, confirming the pre-existing manual flow still works unchanged alongside the new inbox flow.
