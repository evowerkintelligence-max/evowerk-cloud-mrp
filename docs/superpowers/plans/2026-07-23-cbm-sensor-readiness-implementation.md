# CBM Sensor Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a machine with a realtime stream but no mapped sensor channels its own
`"unmapped"` status in the CBM fleet dashboard, instead of sitting in `"learning"` forever with no
explanation, and show the user a "Configure" CTA for it in the Predictive Maintenance page.

**Architecture:** Backend: one new status branch in `fleet_dashboard()` (`backend/app/services/cbm.py`)
that short-circuits to `"unmapped"` when a stream's `sensor_map` is empty, skipping the
baseline/anomaly/reading queries entirely. Frontend: `Predictive.tsx`'s existing fleet table
renders the new status via its existing `STATUS_LABEL` lookup and existing null-safe cell
rendering, plus one new conditional (skip row-click-to-detail, show a Configure button instead)
for `"unmapped"` rows.

**Tech Stack:** Python/FastAPI/SQLAlchemy (async), React/TypeScript/TanStack Query, pytest, vitest.
No new dependencies.

**Full design reference:** `docs/superpowers/specs/2026-07-23-cbm-sensor-readiness-design.md`

## Global Constraints

- No new tables, no new endpoints, no new npm/pip dependencies (per spec's Architecture section).
- No generic "sensor feature requirements" registry — CBM is the only consumer of this pattern
  today (per spec's Scope decision section).
- No deep link from the Configure CTA to the specific stream's edit form — links to `/realtime`
  generally (per spec's frontend section and its `ponytail:` note).
- Any non-empty `sensor_map` counts as ready — no validation of which channels are mapped (per
  spec's Explicit non-goals).

---

### Task 1: Backend — `"unmapped"` status branch in `fleet_dashboard` (TDD)

**Files:**
- Modify: `backend/app/services/cbm.py:40-89` (the `fleet_dashboard` function)
- Test: `backend/tests/test_cbm_api.py`

**Interfaces:**
- Produces (consumed by Task 2): the `GET /cbm/fleet` response's `machines[]` entries gain a
  `status: "unmapped"` value (alongside the existing `"learning"`/`"active"`) and every entry
  (regardless of status) gains a `stream_id: string` field. Shape otherwise unchanged:
  `{machine: str, status: str, risk_level: str, failure_probability_30d: float | null,
  active_anomalies: int, last_reading_at: str | null, stream_id: str}`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_cbm_api.py`, directly below `test_fleet_dashboard_shows_stream_status_per_machine`:

```python
async def test_fleet_dashboard_flags_unmapped_stream(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    stream = await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {}},
    )
    assert stream.status_code == 200, stream.text

    res = await client.get("/cbm/fleet", headers=_h(token))
    assert res.status_code == 200, res.text
    machines = {m["machine"]: m for m in res.json()["machines"]}
    assert machines["M1"]["status"] == "unmapped"
    assert machines["M1"]["risk_level"] == "unknown"
    assert machines["M1"]["failure_probability_30d"] is None
    assert machines["M1"]["active_anomalies"] == 0
    assert machines["M1"]["last_reading_at"] is None
    assert machines["M1"]["stream_id"] == stream.json()["id"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `backend/`): `pytest tests/test_cbm_api.py::test_fleet_dashboard_flags_unmapped_stream -v`
Expected: FAIL — `assert 'learning' == 'unmapped'` (today an empty-`sensor_map` stream falls into
the existing `"learning"` branch since nothing checks `sensor_map` yet).

- [ ] **Step 3: Implement the status branch**

Replace the full body of `fleet_dashboard` in `backend/app/services/cbm.py:40-89` with:

```python
async def fleet_dashboard(db: AsyncSession, org_id: uuid.UUID) -> list[dict]:
    streams = (
        await db.execute(
            select(RealtimeStream).where(RealtimeStream.org_id == org_id, RealtimeStream.machine.is_not(None))
        )
    ).scalars().all()

    out = []
    for stream in streams:
        machine = stream.machine

        if not stream.sensor_map:
            out.append({
                "machine": machine, "status": "unmapped", "risk_level": "unknown",
                "failure_probability_30d": None, "active_anomalies": 0,
                "last_reading_at": None, "stream_id": str(stream.id),
            })
            continue

        baselines = (
            await db.execute(select(SensorBaseline).where(SensorBaseline.org_id == org_id, SensorBaseline.machine == machine))
        ).scalars().all()

        # No baselines yet (no readings scored) or any sensor still under
        # MIN_BASELINE readings -> still learning. Once every mapped sensor
        # has a baseline past MIN_BASELINE, the machine is "active".
        if not baselines or any(b.n < MIN_BASELINE for b in baselines):
            status = "learning"
        else:
            status = "active"

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        active_anomalies = (
            await db.execute(
                select(SensorAnomaly).where(
                    SensorAnomaly.org_id == org_id, SensorAnomaly.machine == machine,
                    SensorAnomaly.detected_at >= cutoff, SensorAnomaly.acknowledged_at.is_(None),
                )
            )
        ).scalars().all()

        last_reading = (
            await db.execute(
                select(SensorReading)
                .where(SensorReading.org_id == org_id, SensorReading.machine == machine)
                .order_by(SensorReading.ts.desc()).limit(1)
            )
        ).scalar_one_or_none()

        _fit, prob = await _failure_probability(db, org_id, machine)

        out.append({
            "machine": machine, "status": status, "risk_level": _risk_level(prob),
            "failure_probability_30d": prob, "active_anomalies": len(active_anomalies),
            "last_reading_at": last_reading.ts.isoformat() if last_reading else None,
            "stream_id": str(stream.id),
        })

    out.sort(key=lambda m: {"high": 0, "medium": 1, "low": 2, "unknown": 3}.get(m["risk_level"], 4))
    return out
```

- [ ] **Step 4: Run the new test to verify it passes**

Run (from `backend/`): `pytest tests/test_cbm_api.py::test_fleet_dashboard_flags_unmapped_stream -v`
Expected: PASS.

- [ ] **Step 5: Run the full CBM test file to check for regressions**

Run (from `backend/`): `pytest tests/test_cbm_api.py -v`
Expected: all tests PASS, including `test_fleet_dashboard_shows_stream_status_per_machine` (its
stream has `sensor_map: {"vib_x": "v"}` — non-empty, so it still takes the `"learning"` branch
unchanged) and `test_cbm_tenant_isolation`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/cbm.py backend/tests/test_cbm_api.py
git commit -m "feat: flag CBM machines with no mapped sensor as unmapped"
```

---

### Task 2: Frontend — "Needs sensor mapping" row in `Predictive.tsx`

**Files:**
- Modify: `frontend/src/pages/Predictive.tsx`

**Interfaces:**
- Consumes: `GET /cbm/fleet` response shape from Task 1 — `status` now includes `"unmapped"`
  (other fields unchanged; `stream_id` is present but unused by this task, per the spec's
  no-deep-link non-goal).
- Produces: nothing consumed by a later task — this is the last task in the plan.

- [ ] **Step 1: Add the `useNavigate` import**

Change line 1 of `frontend/src/pages/Predictive.tsx` from:

```tsx
import { useState } from "react";
```

to:

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
```

- [ ] **Step 2: Widen the `FleetMachine` status type**

Change line 15 from:

```tsx
  status: "learning" | "active";
```

to:

```tsx
  status: "learning" | "active" | "unmapped";
```

- [ ] **Step 3: Add the status label**

Change line 34 from:

```tsx
const STATUS_LABEL: Record<string, string> = { learning: "Learning baseline", active: "Active" };
```

to:

```tsx
const STATUS_LABEL: Record<string, string> = {
  learning: "Learning baseline", active: "Active", unmapped: "Needs sensor mapping",
};
```

- [ ] **Step 4: Add `navigate` and gate the row click + last-reading cell**

In the `Predictive()` function, change line 102 from:

```tsx
  const [selectedMachine, setSelectedMachine] = useState<string | null>(null);
```

to:

```tsx
  const [selectedMachine, setSelectedMachine] = useState<string | null>(null);
  const navigate = useNavigate();
```

Then change the table row block (lines 150-162) from:

```tsx
                  {fleet.data?.machines.map((m) => (
                    <TR key={m.machine} className="cursor-pointer" onClick={() => setSelectedMachine(m.machine)}>
                      <TD className="font-medium">{m.machine}</TD>
                      <TD className="text-muted-foreground">{STATUS_LABEL[m.status] ?? m.status}</TD>
                      <TD>
                        <Badge variant={RISK_VARIANT[m.risk_level] ?? "neutral"}>{m.risk_level}</Badge>
                      </TD>
                      <TD>{m.failure_probability_30d != null ? `${(m.failure_probability_30d * 100).toFixed(1)}%` : "—"}</TD>
                      <TD>{m.active_anomalies}</TD>
                      <TD className="text-muted-foreground">
                        {m.last_reading_at ? new Date(m.last_reading_at).toLocaleString() : "No readings yet"}
                      </TD>
                    </TR>
                  ))}
```

to:

```tsx
                  {fleet.data?.machines.map((m) => {
                    const isUnmapped = m.status === "unmapped";
                    return (
                      <TR
                        key={m.machine}
                        className={isUnmapped ? undefined : "cursor-pointer"}
                        onClick={isUnmapped ? undefined : () => setSelectedMachine(m.machine)}
                      >
                        <TD className="font-medium">{m.machine}</TD>
                        <TD className="text-muted-foreground">{STATUS_LABEL[m.status] ?? m.status}</TD>
                        <TD>
                          <Badge variant={RISK_VARIANT[m.risk_level] ?? "neutral"}>{m.risk_level}</Badge>
                        </TD>
                        <TD>{m.failure_probability_30d != null ? `${(m.failure_probability_30d * 100).toFixed(1)}%` : "—"}</TD>
                        <TD>{m.active_anomalies}</TD>
                        <TD className="text-muted-foreground">
                          {isUnmapped ? (
                            <Button size="sm" variant="outline" onClick={() => navigate("/realtime")}>
                              Configure
                            </Button>
                          ) : m.last_reading_at ? (
                            new Date(m.last_reading_at).toLocaleString()
                          ) : (
                            "No readings yet"
                          )}
                        </TD>
                      </TR>
                    );
                  })}
```

- [ ] **Step 5: Type-check**

Run (from `frontend/`): `npx tsc -b --noEmit`
Expected: no new errors.

- [ ] **Step 6: Manual smoke test in the browser**

There is no component-rendering test harness in this repo (same situation as the data-readiness
plan's Task 2 Step 6). Verify by hand:

1. Start both dev servers (`.claude/launch.json` configurations `backend-dev` and `frontend`).
2. Log in (register a throwaway account if needed).
3. Go to **Integrations**, create an MQTT (or any type) integration.
4. Go to **Realtime**, create a stream on that integration with a **Machine** set (e.g. `M1`) and
   **no sensor map rows added**.
5. Go to **Predictive Maintenance**. Confirm the fleet table shows one row for `M1` with status
   "Needs sensor mapping", risk badge "unknown", failure probability "—", active anomalies `0`,
   and a **Configure** button in the last column instead of "No readings yet".
6. Click the row (not the button) — confirm it does **not** open the machine detail view (no
   navigation, no drill-down panel).
7. Click **Configure** — confirm it navigates to `/realtime`.
8. On the Realtime page, edit that stream and add a sensor map row (e.g. name `vib_x`, field `v`),
   save, and go back to Predictive Maintenance. Confirm `M1` now shows "Learning baseline" instead
   of "Needs sensor mapping", and the row is clickable again (opens machine detail).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/Predictive.tsx
git commit -m "feat: show Configure CTA for CBM machines with no sensor mapped"
```

---

## Self-Review Notes

- **Spec coverage:** Architecture section (backend `"unmapped"` branch + `stream_id`) → Task 1.
  Frontend section (status label, cell rendering, row-click gating, Configure CTA to `/realtime`)
  → Task 2. Testing section (backend unit test distinguishing `"unmapped"` from `"learning"`;
  frontend manual smoke test) → Task 1 Steps 1-5, Task 2 Step 6. Explicit non-goals (no registry,
  no deep link, no `MachineDetail.tsx` change, no channel-name validation) are honored by omission
  — no task touches `MachineDetail.tsx` or adds a registry file, and the CTA target is the bare
  `/realtime` route.
- **Placeholder scan:** none — every step has complete, runnable code.
- **Type consistency:** `FleetMachine.status` (Task 2 Step 2) matches the three string values the
  backend can now emit (Task 1 Step 3: `"unmapped"`, `"learning"`, `"active"`). `stream_id` is
  added to every backend response row in Task 1 but intentionally left off the frontend
  `FleetMachine` interface since Task 2 never reads it (matches the spec's no-deep-link non-goal —
  adding an unused field to the interface would be dead weight, not accuracy).
