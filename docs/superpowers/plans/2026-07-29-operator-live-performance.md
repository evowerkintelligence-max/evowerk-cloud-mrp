# Operator Live Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show, per operator per shift, floor time / check-in count / distinct machines worked — derived from live badge/QR check-in data (`CaptureEvent`) and the existing shift schedule — as a new card on the Operators tab.

**Architecture:** One new read-only backend endpoint (`GET /operators/capture-events`) returns raw capture events for a lookback window; all shift-bucketing math happens client-side by generalizing the existing `resolveCurrentShift` function (renamed `resolveShiftForTime`) so it works for any timestamp, not just "now." A new card in `OperatorsPanel` (`frontend/src/pages/Production.tsx`) fetches both, aggregates in memory, and renders a shift-picker + ranked bar chart + table, reusing `EChart`/`rankedBarOption` from `pages/analytics/shared.tsx`.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), React + TanStack Query + ECharts (frontend), pytest (backend tests), vitest (frontend tests).

## Global Constraints

- No new dependencies — everything needed already exists (FastAPI, SQLAlchemy, React Query, ECharts).
- `days` query param bounded `[1, 90]`, default `7` — enforced at the request boundary via `Query(7, ge=1, le=90)`, no silent clamping.
- Shift bucketing uses only an event's `started_at` — never split a single event across two shift buckets.
- No shift schedule configured → events bucket into a single `"Unassigned"` pseudo-shift; this must never be treated as an error.
- Read-only feature: no new mutations, no new role restrictions beyond `get_current_user` (same tier as `GET /operators`).

---

### Task 1: Backend — `GET /operators/capture-events` endpoint

**Files:**
- Modify: `backend/app/api/routes/operators.py`
- Test: `backend/tests/test_operators.py`

**Interfaces:**
- Produces: `GET /operators/capture-events?days=N` → `list[CaptureEventItem]`, where
  `CaptureEventItem = {operator_id: str, operator_name: str, machine_id: str, machine_name: str, started_at: datetime, ended_at: datetime | None}`.
  This is the only interface later tasks (Task 3) depend on.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_operators.py`. First, add `timedelta` to the existing `datetime` import at the top of the file:

```python
from datetime import datetime, timedelta, timezone
```

Then append these tests at the end of the file:

```python
async def test_capture_events_route_scopes_by_org_and_days(client, db_session):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = await _org_id(client, headers)
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    wc = (await client.post("/work-centers", json={"code": "WC1", "name": "Line 1"}, headers=headers)).json()
    machine = (
        await client.post(
            "/machines", json={"work_center_id": wc["id"], "code": "M1", "name": "Machine 1"}, headers=headers
        )
    ).json()

    recent = await open_capture_event(
        db_session, org_id=org_id, operator_id=uuid.UUID(op["id"]), machine_id=uuid.UUID(machine["id"]),
        source="reader", scanned_at=NOW,
    )
    recent.ended_at = NOW
    # 20 days old rather than exactly 30: a boundary of exactly `days=30` would
    # be flaky, since the endpoint computes its own `since` a few
    # milliseconds after this NOW timestamp was captured, making the exact
    # 30-day-old row fall just outside the window depending on execution speed.
    old = CaptureEvent(
        org_id=org_id, operator_id=uuid.UUID(op["id"]), machine_id=uuid.UUID(machine["id"]),
        source="reader", started_at=NOW - timedelta(days=20),
        ended_at=NOW - timedelta(days=20) + timedelta(hours=1),
    )
    db_session.add(old)
    await db_session.commit()

    other_token = await _register_and_login(client, email="owner2@example.com", org_name="Other")
    other_headers = {"Authorization": f"Bearer {other_token}"}

    res = await client.get("/operators/capture-events?days=7", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["operator_name"] == "J Kim"
    assert body[0]["machine_name"] == "Machine 1"
    assert body[0]["ended_at"] is not None

    res30 = await client.get("/operators/capture-events?days=30", headers=headers)
    assert len(res30.json()) == 2

    other_res = await client.get("/operators/capture-events", headers=other_headers)
    assert other_res.json() == []


async def test_capture_events_days_out_of_range_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/operators/capture-events?days=0", headers=headers)).status_code == 422
    assert (await client.get("/operators/capture-events?days=91", headers=headers)).status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/pytest tests/test_operators.py -k capture_events -v`
Expected: FAIL — `404 Not Found` on `/operators/capture-events` (route doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

In `backend/app/api/routes/operators.py`, update the imports at the top:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.capture_event import CaptureEvent
from app.models.machine import Machine
from app.models.operator import Operator
from app.models.work_order import WorkOrder
from app.services.registry_matching import FUZZY_MATCH_THRESHOLD, normalized_similarity
```

(This adds `Query` to the fastapi import and adds the new `Machine` import; everything else is unchanged.)

Then add this response model and endpoint directly after `_to_response` (i.e. right before the existing `@router.get("", response_model=list[OperatorResponse])` block — order doesn't matter functionally, but keeping read-only GETs grouped together matches the file's existing style):

```python
class CaptureEventItem(BaseModel):
    operator_id: str
    operator_name: str
    machine_id: str
    machine_name: str
    started_at: datetime
    ended_at: datetime | None


@router.get("/capture-events", response_model=list[CaptureEventItem])
async def list_capture_events(
    days: int = Query(7, ge=1, le=90),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(CaptureEvent, Operator.name, Machine.name)
            .join(Operator, Operator.id == CaptureEvent.operator_id)
            .join(Machine, Machine.id == CaptureEvent.machine_id)
            .where(CaptureEvent.org_id == current.org_id, CaptureEvent.started_at >= since)
            .order_by(CaptureEvent.started_at)
        )
    ).all()
    return [
        CaptureEventItem(
            operator_id=str(event.operator_id),
            operator_name=operator_name,
            machine_id=str(event.machine_id),
            machine_name=machine_name,
            started_at=event.started_at,
            ended_at=event.ended_at,
        )
        for event, operator_name, machine_name in rows
    ]
```

**Important:** this route must be registered as a plain `/capture-events` path (no `{operator_id}` collision exists in this router today, so placement doesn't matter — but do not place it after a route that could shadow it if one is added later).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/pytest tests/test_operators.py -k capture_events -v`
Expected: PASS (3 tests: the org/days-scoping test and the two out-of-range tests count as one test with two asserts).

- [ ] **Step 5: Run the full operators test file to check for regressions**

Run: `cd backend && .venv/Scripts/pytest tests/test_operators.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/operators.py backend/tests/test_operators.py
git commit -m "feat(operators): add GET /operators/capture-events for live performance view"
```

---

### Task 2: Frontend — generalize shift-resolution into `shared.tsx`

**Files:**
- Modify: `frontend/src/pages/analytics/shared.tsx`
- Modify: `frontend/src/pages/analytics/Shifts.tsx`
- Test: `frontend/src/pages/analytics/shared.test.ts` (new)

**Interfaces:**
- Consumes: nothing new (pure refactor of existing logic already in `Shifts.tsx`).
- Produces (for Task 3 to consume):
  - `export interface ScheduleItem { shift_label: string; start_time: string; end_time: string }`
  - `export function useShiftSchedule(): UseQueryResult<ScheduleItem[]>` (react-query key `["shift-schedule"]`, GET `/analytics/shifts/schedule`)
  - `export function resolveShiftForTime(schedule: ScheduleItem[], at: Date): { shift: string; minutesLeft: number } | null`
  - `export function asUtcMs(iso: string): number` (already exists in `shared.tsx` — Task 3 reuses it as-is, listed here for completeness)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/analytics/shared.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { resolveShiftForTime, type ScheduleItem } from "./shared";

const SCHEDULE: ScheduleItem[] = [
  { shift_label: "Day", start_time: "08:00", end_time: "20:00" },
  { shift_label: "Night", start_time: "20:00", end_time: "08:00" },
];

describe("resolveShiftForTime", () => {
  it("resolves a time inside a normal (non-wrapping) shift window", () => {
    const at = new Date(2026, 0, 1, 14, 30);
    const result = resolveShiftForTime(SCHEDULE, at);
    expect(result?.shift).toBe("Day");
    expect(result?.minutesLeft).toBe(330); // 20:00 - 14:30 = 5h30m
  });

  it("resolves a time inside a midnight-wrapping shift window", () => {
    const at = new Date(2026, 0, 1, 2, 0);
    const result = resolveShiftForTime(SCHEDULE, at);
    expect(result?.shift).toBe("Night");
    expect(result?.minutesLeft).toBe(360); // 08:00 - 02:00 = 6h
  });

  it("returns null when no shift window covers the time", () => {
    const result = resolveShiftForTime([{ shift_label: "Day", start_time: "08:00", end_time: "12:00" }], new Date(2026, 0, 1, 15, 0));
    expect(result).toBeNull();
  });

  it("returns null for an empty schedule", () => {
    expect(resolveShiftForTime([], new Date())).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/analytics/shared.test.ts`
Expected: FAIL — `resolveShiftForTime` is not exported from `./shared`.

- [ ] **Step 3: Move and generalize the logic into `shared.tsx`**

In `frontend/src/pages/analytics/shared.tsx`, add near the other exported helpers (e.g. right after the `useElapsed`/`asUtcMs` block, before the `C` palette constant):

```typescript
export interface ScheduleItem {
  shift_label: string;
  start_time: string;
  end_time: string;
}

export function useShiftSchedule() {
  return useQuery<ScheduleItem[]>({
    queryKey: ["shift-schedule"],
    queryFn: async () => {
      const res = await apiFetch("/analytics/shifts/schedule");
      if (!res.ok) throw new Error("Failed to load shift schedule");
      return res.json();
    },
  });
}

function minutesOfDay(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// Matches `at` against each configured shift window, handling the case where
// a shift crosses midnight (end_time < start_time). Used both for "now" (the
// Shift Comparison page's current-shift resolution) and for bucketing any
// historical timestamp — e.g. an operator capture event — into a shift.
export function resolveShiftForTime(schedule: ScheduleItem[], at: Date): { shift: string; minutesLeft: number } | null {
  const atMin = at.getHours() * 60 + at.getMinutes();
  for (const s of schedule) {
    const start = minutesOfDay(s.start_time);
    const end = minutesOfDay(s.end_time);
    const wraps = end <= start;
    const within = wraps ? atMin >= start || atMin < end : atMin >= start && atMin < end;
    if (within) {
      const minutesLeft = wraps ? (atMin >= start ? end + 24 * 60 - atMin : end - atMin) : end - atMin;
      return { shift: s.shift_label, minutesLeft };
    }
  }
  return null;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/analytics/shared.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Remove the now-duplicated definitions from `Shifts.tsx` and import from `shared.tsx` instead**

In `frontend/src/pages/analytics/Shifts.tsx`:

1. Delete the local `interface ScheduleItem { ... }` block (currently at lines 94-98).
2. Delete the local `minutesOfDay` function (lines 129-132).
3. Delete the local `resolveCurrentShift` function (lines 134-149) and the local `useShiftSchedule` function (lines 151-160).
4. Update the import from `"./shared"` (currently lines 13-26) to add the three new names:

```typescript
import {
  AXIS_LABEL,
  AXIS_LINE,
  C,
  EChart,
  EngineScreen,
  ScheduleItem,
  SPLIT_LINE,
  TOOLTIP,
  fmt,
  oeeColor,
  paceStatus,
  rankedBarOption,
  resolveShiftForTime,
  token,
  useShiftSchedule,
} from "./shared";
```

5. Update the one call site that used the old name — inside the `Shifts()` component, change:

```typescript
const resolved = resolveCurrentShift(schedule.data ?? [], new Date());
```

to:

```typescript
const resolved = resolveShiftForTime(schedule.data ?? [], new Date());
```

- [ ] **Step 6: Typecheck and build to confirm no regressions**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors (in particular, no "cannot find name `resolveCurrentShift`" / `useShiftSchedule` / `ScheduleItem` errors in `Shifts.tsx`).

- [ ] **Step 7: Run the frontend test suite to confirm nothing else broke**

Run: `cd frontend && npx vitest run`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/analytics/shared.tsx frontend/src/pages/analytics/Shifts.tsx frontend/src/pages/analytics/shared.test.ts
git commit -m "refactor(shifts): generalize resolveCurrentShift into shared resolveShiftForTime"
```

---

### Task 3: Frontend — "Operator performance by shift" card in `OperatorsPanel`

**Files:**
- Modify: `frontend/src/pages/Production.tsx`
- Modify: `frontend/src/pages/Production.render.test.tsx`

**Interfaces:**
- Consumes:
  - `useShiftSchedule(): UseQueryResult<ScheduleItem[]>` and `resolveShiftForTime(schedule, at)` from Task 2
  - `EChart`, `rankedBarOption`, `C`, `asUtcMs` from `./analytics/shared` (all already exported today)
- Produces: nothing consumed by later tasks — this is the last task.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/pages/Production.render.test.tsx`, inside the existing `describe("Production (setup tabs)", ...)` block (after the last `it(...)` currently there, i.e. after `"operators tab surfaces a needs-review row with a merge picker"`):

```typescript
  it("operators tab shows performance stats bucketed by shift", () => {
    const html = renderSetup("operators", (qc) => {
      qc.setQueryData(
        ["operators"],
        [{ id: "op-1", name: "J Kim", code: null, badge_id: null, status: "active", needs_review: false }],
      );
      qc.setQueryData(["shift-schedule"], [{ shift_label: "Day", start_time: "08:00", end_time: "20:00" }]);
      qc.setQueryData(
        ["operator-capture-events", 7],
        [
          {
            operator_id: "op-1", operator_name: "J Kim",
            machine_id: "m-1", machine_name: "Machine 1",
            started_at: "2026-07-27T10:00:00+00:00", ended_at: "2026-07-27T11:30:00+00:00",
          },
        ],
      );
    });
    expect(html).toContain("Operator performance by shift");
    expect(html).toContain("J Kim");
    expect(html).toContain("Day");
    expect(html).toContain("Machine 1");
  });

  it("operators tab points at the shift schedule when none is configured", () => {
    const html = renderSetup("operators", (qc) => {
      qc.setQueryData(["operators"], []);
      qc.setQueryData(["shift-schedule"], []);
      qc.setQueryData(
        ["operator-capture-events", 7],
        [
          {
            operator_id: "op-1", operator_name: "J Kim",
            machine_id: "m-1", machine_name: "Machine 1",
            started_at: "2026-07-27T10:00:00+00:00", ended_at: "2026-07-27T11:30:00+00:00",
          },
        ],
      );
    });
    expect(html).toContain("Unassigned");
    expect(html).toContain("Set one up");
  });

  it("operators tab badges an operator with an open check-in as currently on the floor", () => {
    const html = renderSetup("operators", (qc) => {
      qc.setQueryData(
        ["operators"],
        [{ id: "op-1", name: "J Kim", code: null, badge_id: null, status: "active", needs_review: false }],
      );
      qc.setQueryData(["shift-schedule"], [{ shift_label: "Day", start_time: "08:00", end_time: "20:00" }]);
      qc.setQueryData(
        ["operator-capture-events", 7],
        [
          {
            operator_id: "op-1", operator_name: "J Kim",
            machine_id: "m-1", machine_name: "Machine 1",
            started_at: "2026-07-27T10:00:00+00:00", ended_at: null,
          },
        ],
      );
    });
    expect(html).toContain("On floor");
    expect(html).toContain("Machine 1");
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/Production.render.test.tsx`
Expected: FAIL — the new assertions (`"Operator performance by shift"`, `"Unassigned"`, `"On floor"`) don't appear anywhere yet.

- [ ] **Step 3: Add imports to `Production.tsx`**

At the top of `frontend/src/pages/Production.tsx`, add to the existing import list (after the `humanize`/`useCurrentUser` imports, before `import { JobsPanel }`):

```typescript
import { C, EChart, asUtcMs, rankedBarOption, resolveShiftForTime, useShiftSchedule, type ScheduleItem } from "./analytics/shared";
```

- [ ] **Step 4: Add the capture-events hook, aggregation helper, and card component**

In `frontend/src/pages/Production.tsx`, directly above the existing `function OperatorsPanel() {` definition, add:

```typescript
interface CaptureEventItem {
  operator_id: string;
  operator_name: string;
  machine_id: string;
  machine_name: string;
  started_at: string;
  ended_at: string | null;
}

const CAPTURE_EVENT_DAY_OPTIONS = [7, 30, 90];
const UNASSIGNED_SHIFT = "Unassigned";

function useOperatorCaptureEvents(days: number) {
  return useQuery<CaptureEventItem[]>({
    queryKey: ["operator-capture-events", days],
    queryFn: async () => {
      const res = await apiFetch(`/operators/capture-events?days=${days}`);
      if (!res.ok) throw new Error("Failed to load check-in history");
      return res.json();
    },
  });
}

interface OperatorShiftStat {
  operatorId: string;
  operatorName: string;
  shift: string;
  totalMinutes: number;
  checkins: number;
  machines: Set<string>;
}

// Buckets each capture event by its start time (never split across a shift
// boundary) and sums duration/check-ins/distinct machines per operator per
// shift. An open event (ended_at null) counts as running until now.
function aggregateByOperatorShift(events: CaptureEventItem[], schedule: ScheduleItem[]): OperatorShiftStat[] {
  const now = Date.now();
  const byKey = new Map<string, OperatorShiftStat>();
  for (const e of events) {
    const started = asUtcMs(e.started_at);
    const ended = e.ended_at ? asUtcMs(e.ended_at) : now;
    const minutes = Math.max(0, (ended - started) / 60000);
    const resolved = schedule.length > 0 ? resolveShiftForTime(schedule, new Date(started)) : null;
    const shift = resolved?.shift ?? UNASSIGNED_SHIFT;
    const key = `${e.operator_id}::${shift}`;
    let stat = byKey.get(key);
    if (!stat) {
      stat = { operatorId: e.operator_id, operatorName: e.operator_name, shift, totalMinutes: 0, checkins: 0, machines: new Set() };
      byKey.set(key, stat);
    }
    stat.totalMinutes += minutes;
    stat.checkins += 1;
    // Dedupe by name (not id) so the Machines column can render the list of
    // distinct machines directly instead of a bare count that hides which
    // machines were involved.
    stat.machines.add(e.machine_name);
  }
  return Array.from(byKey.values());
}

function formatMinutes(total: number): string {
  const h = Math.floor(total / 60);
  const m = Math.round(total % 60);
  return `${h}h ${m}m`;
}

function OperatorPerformancePanel() {
  const [days, setDays] = useState(7);
  const [shift, setShift] = useState("");
  const schedule = useShiftSchedule();
  const events = useOperatorCaptureEvents(days);

  const scheduleRows = schedule.data ?? [];
  const eventRows = events.data ?? [];
  const stats = aggregateByOperatorShift(eventRows, scheduleRows);
  const shifts = Array.from(new Set(stats.map((s) => s.shift))).sort();
  const activeShift = shift || shifts[0] || "";
  const rowsForShift = stats.filter((s) => s.shift === activeShift).sort((a, b) => b.totalMinutes - a.totalMinutes);

  const onFloor = new Map<string, string>();
  for (const e of eventRows) if (e.ended_at === null) onFloor.set(e.operator_id, e.machine_name);

  const sortedStats = [...stats].sort(
    (a, b) => a.operatorName.localeCompare(b.operatorName) || a.shift.localeCompare(b.shift),
  );

  return (
    <Card className="mt-8">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Operator performance by shift</CardTitle>
        <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))} className="w-32">
          {CAPTURE_EVENT_DAY_OPTIONS.map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </Select>
      </CardHeader>
      <CardContent className="space-y-4">
        {scheduleRows.length === 0 && eventRows.length > 0 && (
          <Alert variant="warning">
            No shift schedule configured — check-ins are grouped as "{UNASSIGNED_SHIFT}". Set one up on the Shift
            Comparison page (Analytics → Shift Comparison) to break this down by shift.
          </Alert>
        )}
        {eventRows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No check-ins in the last {days} days.</p>
        ) : (
          <>
            <Select value={activeShift} onChange={(e) => setShift(e.target.value)} className="w-40">
              {shifts.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
            {rowsForShift.length > 0 && (
              <EChart
                height={Math.max(120, rowsForShift.length * 36 + 20)}
                option={rankedBarOption({
                  categories: rowsForShift.map((r) => r.operatorName),
                  values: rowsForShift.map((r) => Math.round((r.totalMinutes / 60) * 10) / 10),
                  colorOf: () => C.blue,
                  formatter: (v) => `${v} h`,
                })}
              />
            )}
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Operator</TH>
                    <TH>Shift</TH>
                    <TH>Total time</TH>
                    <TH>Check-ins</TH>
                    <TH>Machines</TH>
                  </TR>
                </THead>
                <TBody>
                  {sortedStats.map((s) => (
                    <TR key={`${s.operatorId}::${s.shift}`}>
                      <TD className="font-medium">
                        {s.operatorName}
                        {onFloor.has(s.operatorId) && (
                          <Badge variant="success" className="ml-2">
                            On floor — {onFloor.get(s.operatorId)}
                          </Badge>
                        )}
                      </TD>
                      <TD>{s.shift}</TD>
                      <TD>{formatMinutes(s.totalMinutes)}</TD>
                      <TD>{s.checkins}</TD>
                      <TD>{Array.from(s.machines).join(", ")}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 5: Render the panel inside `OperatorsPanel`**

In `frontend/src/pages/Production.tsx`, inside `function OperatorsPanel()`'s returned JSX, add `<OperatorPerformancePanel />` immediately after the closing `)}` of the operators table / empty-state block (i.e. right before the final closing `</div>` of `OperatorsPanel`'s return). The end of the function's return block currently looks like:

```tsx
      ) : (
        <TableWrap>
          <Table>
            ...
          </Table>
        </TableWrap>
      )}
    </div>
  );
}
```

Change it to:

```tsx
      ) : (
        <TableWrap>
          <Table>
            ...
          </Table>
        </TableWrap>
      )}

      <OperatorPerformancePanel />
    </div>
  );
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/Production.render.test.tsx`
Expected: PASS (all tests in the file, including the 3 new ones).

- [ ] **Step 7: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 8: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: all PASS.

- [ ] **Step 9: Manual check in the running app**

Start the frontend dev server and backend, log in, go to Production → Operators tab. Confirm the "Operator performance by shift" card renders below the roster table, the days selector works, and (if any check-ins exist in the seeded/dev data) the shift picker and bar chart populate. If no check-ins exist yet, confirm the "No check-ins in the last 7 days" message shows instead of a crash.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/pages/Production.tsx frontend/src/pages/Production.render.test.tsx
git commit -m "feat(operators): add operator performance by shift card from live check-in data"
```

---

## Plan Self-Review Notes

- **Spec coverage:** Task 1 covers the backend endpoint and its error handling (422 on out-of-range `days`, org-scoping). Task 2 covers the shift-resolution reuse the spec's "Why shift-bucketing happens in the browser" section calls for. Task 3 covers the card itself: days selector, shift selector + ranked chart, table (operator/shift/time/check-ins/machines), the "currently on floor" badge, and the no-schedule-configured message. All spec sections are represented.
- **Type consistency:** `CaptureEventItem` (Task 1's Pydantic model / Task 3's TS interface) and `ScheduleItem`/`resolveShiftForTime` (Task 2) are named and shaped identically everywhere they're used across tasks.
- **Deferred by spec, correctly out of scope here:** output/quality metrics, the dataset-column operator dimension on Shift Comparison, splitting an event across a shift boundary, and access control — none of these appear in any task above.
