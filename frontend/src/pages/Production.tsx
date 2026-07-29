import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight, Cog, Route as RouteIcon, Users, Wrench } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { ItemSelect } from "@/components/ItemSelect";
import { humanize } from "@/lib/status";
import { useCurrentUser } from "@/lib/currentUser";
import { C, EChart, asUtcMs, rankedBarOption, resolveShiftForTime, useShiftSchedule, type ScheduleItem } from "./analytics/shared";
import { JobsPanel } from "./production/JobsPanel";

interface WorkCenterResponse {
  id: string;
  code: string;
  name: string;
  status: string;
  machine_capacity: number;
  default_cycle_time_seconds: number | null;
  default_mtbf_hours: number | null;
  default_mttr_hours: number | null;
}

interface MachineResponse {
  id: string;
  work_center_id: string;
  code: string;
  name: string;
  status: string;
  mtbf_hours: number | null;
  mttr_hours: number | null;
  needs_review: boolean;
}

interface OperatorResponse {
  id: string;
  name: string;
  code: string | null;
  badge_id: string | null;
  status: string;
  needs_review: boolean;
}

interface RoutingStepResponse {
  id: string;
  sequence: number;
  work_center_id: string;
  cycle_time_seconds: number | null;
  setup_time_seconds: number | null;
}

interface RoutingResponse {
  id: string;
  item_id: string;
  version: number;
  status: string;
  steps: RoutingStepResponse[];
}

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
}

function statusVariant(status: string) {
  if (status === "active") return "success" as const;
  if (status === "retired" || status === "archived") return "neutral" as const;
  return "warning" as const; // inactive / draft
}

/** Empty string -> null, so an untouched optional number field isn't sent as 0. */
function optionalNumber(value: string): number | null {
  return value.trim() === "" ? null : Number(value);
}

function useWorkCenters() {
  return useQuery<WorkCenterResponse[]>({
    queryKey: ["work-centers"],
    queryFn: async () => {
      const res = await apiFetch("/work-centers");
      if (!res.ok) throw new Error("Failed to load work centers");
      return res.json();
    },
  });
}

function WorkCentersPanel() {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [capacity, setCapacity] = useState("1");
  const [cycleTime, setCycleTime] = useState("");
  const [mtbf, setMtbf] = useState("");
  const [mttr, setMttr] = useState("");
  const [error, setError] = useState<string | null>(null);

  const workCenters = useWorkCenters();

  const createWorkCenter = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/work-centers", {
        method: "POST",
        body: JSON.stringify({
          code,
          name,
          machine_capacity: Number(capacity) || 1,
          default_cycle_time_seconds: optionalNumber(cycleTime),
          default_mtbf_hours: optionalNumber(mtbf),
          default_mttr_hours: optionalNumber(mttr),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create work center");
      }
      return res.json();
    },
    onSuccess: () => {
      setCode("");
      setName("");
      setCapacity("1");
      setCycleTime("");
      setMtbf("");
      setMttr("");
      queryClient.invalidateQueries({ queryKey: ["work-centers"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createWorkCenter.mutate();
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add a work center</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Code" htmlFor="wc-code">
              <Input id="wc-code" value={code} onChange={(e) => setCode(e.target.value)} required />
            </Field>
            <Field label="Name" htmlFor="wc-name">
              <Input id="wc-name" value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <Field label="Machines in parallel" htmlFor="wc-capacity">
              <Input
                id="wc-capacity"
                type="number"
                min="1"
                step="1"
                value={capacity}
                onChange={(e) => setCapacity(e.target.value)}
                required
              />
            </Field>
            <Field label="Cycle time (s)" htmlFor="wc-cycle" hint="Optional default for routing steps">
              <Input
                id="wc-cycle"
                type="number"
                min="0"
                step="0.01"
                value={cycleTime}
                onChange={(e) => setCycleTime(e.target.value)}
              />
            </Field>
            <Field label="MTBF (hours)" htmlFor="wc-mtbf" hint="Optional">
              <Input
                id="wc-mtbf"
                type="number"
                min="0"
                step="0.01"
                value={mtbf}
                onChange={(e) => setMtbf(e.target.value)}
              />
            </Field>
            <Field label="MTTR (hours)" htmlFor="wc-mttr" hint="Optional">
              <Input
                id="wc-mttr"
                type="number"
                min="0"
                step="0.01"
                value={mttr}
                onChange={(e) => setMttr(e.target.value)}
              />
            </Field>
            <div className="flex items-end">
              <Button type="submit" className="w-full" loading={createWorkCenter.isPending}>
                Add work center
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {workCenters.data && workCenters.data.length === 0 ? (
        <EmptyState
          icon={<Cog />}
          title="No work centers yet"
          description="A work center is a station or cell that production passes through."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Code</TH>
                <TH>Name</TH>
                <TH>Status</TH>
                <TH>Capacity</TH>
                <TH>Cycle time (s)</TH>
                <TH>MTBF (h)</TH>
                <TH>MTTR (h)</TH>
              </TR>
            </THead>
            <TBody>
              {workCenters.data?.map((wc) => (
                <TR key={wc.id}>
                  <TD className="font-medium">{wc.code}</TD>
                  <TD>{wc.name}</TD>
                  <TD>
                    <Badge variant={statusVariant(wc.status)}>{humanize(wc.status)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{wc.machine_capacity}</TD>
                  <TD className="text-muted-foreground">{wc.default_cycle_time_seconds ?? "-"}</TD>
                  <TD className="text-muted-foreground">{wc.default_mtbf_hours ?? "-"}</TD>
                  <TD className="text-muted-foreground">{wc.default_mttr_hours ?? "-"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}

// Same pattern ReportBuilder.tsx uses for an authenticated file download: open
// the tab synchronously (so the popup blocker doesn't see an async click),
// then point it at the blob once apiFetch's Authorization header has done its
// job - an <img src> can't carry that header on its own.
//
// The backend endpoint returns the bare QR image with no label, so a printed
// sticker with nothing on it but a QR code is a mismatch waiting to happen
// once a shop has more than a couple of machines - this wraps it in a small
// page naming the machine. Built via DOM APIs (not innerHTML/document.write)
// so the machine's own name/code - free text an owner typed - can't inject
// markup into the print tab.
async function printQr(machineId: string, code: string, name: string) {
  const tab = window.open("", "_blank");
  const res = await apiFetch(`/machines/${machineId}/checkin-qr`);
  if (!res.ok) {
    tab?.close();
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  if (tab) {
    const doc = tab.document;
    doc.title = `Check-in QR - ${code}`;
    const style = doc.createElement("style");
    style.textContent = `
      body { font-family: system-ui, sans-serif; text-align: center; padding: 2rem; }
      h1 { font-size: 1.25rem; margin: 0 0 0.25rem; }
      p { color: #555; margin: 0 0 1.5rem; }
      img { width: 320px; height: 320px; }
    `;
    doc.head.appendChild(style);
    const heading = doc.createElement("h1");
    heading.textContent = `${code} - ${name}`;
    const subheading = doc.createElement("p");
    subheading.textContent = "Scan to check in";
    const img = doc.createElement("img");
    img.src = url;
    img.alt = "Check-in QR code";
    doc.body.append(heading, subheading, img);
  } else {
    window.open(url, "_blank");
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function MachinesPanel() {
  const queryClient = useQueryClient();
  const [workCenterId, setWorkCenterId] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [mtbf, setMtbf] = useState("");
  const [mttr, setMttr] = useState("");
  const [error, setError] = useState<string | null>(null);

  const workCenters = useWorkCenters();
  const machines = useQuery<MachineResponse[]>({
    queryKey: ["machines"],
    queryFn: async () => {
      const res = await apiFetch("/machines");
      if (!res.ok) throw new Error("Failed to load machines");
      return res.json();
    },
  });

  const createMachine = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/machines", {
        method: "POST",
        body: JSON.stringify({
          work_center_id: workCenterId,
          code,
          name,
          mtbf_hours: optionalNumber(mtbf),
          mttr_hours: optionalNumber(mttr),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create machine");
      }
      return res.json();
    },
    onSuccess: () => {
      setCode("");
      setName("");
      setMtbf("");
      setMttr("");
      queryClient.invalidateQueries({ queryKey: ["machines"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createMachine.mutate();
  }

  const workCenterLabel = (id: string) => {
    const wc = workCenters.data?.find((w) => w.id === id);
    return wc ? `${wc.code} - ${wc.name}` : id;
  };

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add a machine</CardTitle>
        </CardHeader>
        <CardContent>
          {workCenters.data && workCenters.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Add a work center first - every machine belongs to one.
            </p>
          ) : (
            <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Work center" htmlFor="machine-wc">
                <Select
                  id="machine-wc"
                  value={workCenterId}
                  onChange={(e) => setWorkCenterId(e.target.value)}
                  required
                >
                  <option value="" disabled>
                    Select a work center
                  </option>
                  {workCenters.data?.map((wc) => (
                    <option key={wc.id} value={wc.id}>
                      {wc.code} - {wc.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Code" htmlFor="machine-code">
                <Input id="machine-code" value={code} onChange={(e) => setCode(e.target.value)} required />
              </Field>
              <Field label="Name" htmlFor="machine-name">
                <Input id="machine-name" value={name} onChange={(e) => setName(e.target.value)} required />
              </Field>
              <Field label="MTBF (hours)" htmlFor="machine-mtbf" hint="Optional">
                <Input
                  id="machine-mtbf"
                  type="number"
                  min="0"
                  step="0.01"
                  value={mtbf}
                  onChange={(e) => setMtbf(e.target.value)}
                />
              </Field>
              <Field label="MTTR (hours)" htmlFor="machine-mttr" hint="Optional">
                <Input
                  id="machine-mttr"
                  type="number"
                  min="0"
                  step="0.01"
                  value={mttr}
                  onChange={(e) => setMttr(e.target.value)}
                />
              </Field>
              <div className="flex items-end">
                <Button type="submit" className="w-full" loading={createMachine.isPending}>
                  Add machine
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>

      {machines.data && machines.data.length === 0 ? (
        <EmptyState icon={<Wrench />} title="No machines yet" />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Code</TH>
                <TH>Name</TH>
                <TH>Work center</TH>
                <TH>Status</TH>
                <TH>MTBF (h)</TH>
                <TH>MTTR (h)</TH>
                <TH>Review</TH>
                <TH>QR</TH>
              </TR>
            </THead>
            <TBody>
              {machines.data?.map((m) => (
                <TR key={m.id}>
                  <TD className="font-medium">{m.code}</TD>
                  <TD>{m.name}</TD>
                  <TD className="text-muted-foreground">{workCenterLabel(m.work_center_id)}</TD>
                  <TD>
                    <Badge variant={statusVariant(m.status)}>{humanize(m.status)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{m.mtbf_hours ?? "-"}</TD>
                  <TD className="text-muted-foreground">{m.mttr_hours ?? "-"}</TD>
                  <TD>{m.needs_review ? <Badge variant="warning">Needs review</Badge> : ""}</TD>
                  <TD>
                    <Button size="sm" variant="outline" onClick={() => printQr(m.id, m.code, m.name)}>
                      Print QR
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}

function OperatorRow({
  operator,
  others,
  onError,
}: {
  operator: OperatorResponse;
  others: OperatorResponse[];
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [mergeTarget, setMergeTarget] = useState("");

  const merge = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/operators/${operator.id}/merge`, {
        method: "POST",
        body: JSON.stringify({ target_operator_id: mergeTarget }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to merge operators");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operators"] }),
    onError: (e: Error) => onError(e.message),
  });

  const keepBoth = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/operators/${operator.id}`, {
        method: "PATCH",
        body: JSON.stringify({ needs_review: false }),
      });
      if (!res.ok) throw new Error("Failed to update operator");
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operators"] }),
    onError: (e: Error) => onError(e.message),
  });

  return (
    <>
      <TR>
        <TD className="font-medium">{operator.name}</TD>
        <TD className="text-muted-foreground">{operator.code ?? "-"}</TD>
        <TD className="text-muted-foreground">{operator.badge_id ? "Assigned" : "-"}</TD>
        <TD>
          <Badge variant={statusVariant(operator.status)}>{humanize(operator.status)}</Badge>
        </TD>
        <TD>{operator.needs_review ? <Badge variant="warning">Needs review</Badge> : ""}</TD>
      </TR>
      {operator.needs_review && (
        <TR>
          <TD colSpan={5}>
            <div className="flex flex-wrap items-center gap-3 rounded-md bg-amber-50 p-3 text-sm dark:bg-amber-950">
              <span>Possible duplicate of an existing operator.</span>
              <Select value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)}>
                <option value="" disabled>
                  Merge into...
                </option>
                {others.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </Select>
              <Button size="sm" disabled={!mergeTarget} loading={merge.isPending} onClick={() => merge.mutate()}>
                Merge
              </Button>
              <Button size="sm" variant="secondary" loading={keepBoth.isPending} onClick={() => keepBoth.mutate()}>
                Keep both
              </Button>
            </div>
          </TD>
        </TR>
      )}
    </>
  );
}

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
    // ponytail: dedupe by name (not id) so the Machines column can render the
    // list of distinct machines directly instead of a bare count that hides
    // which machines were involved.
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

function OperatorsPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [badgeId, setBadgeId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const operators = useQuery<OperatorResponse[]>({
    queryKey: ["operators"],
    queryFn: async () => {
      const res = await apiFetch("/operators");
      if (!res.ok) throw new Error("Failed to load operators");
      return res.json();
    },
  });

  const createOperator = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/operators", {
        method: "POST",
        body: JSON.stringify({ name, code: code.trim() || null, badge_id: badgeId.trim() || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create operator");
      }
      return res.json();
    },
    onSuccess: () => {
      setName("");
      setCode("");
      setBadgeId("");
      queryClient.invalidateQueries({ queryKey: ["operators"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createOperator.mutate();
  }

  const sorted = [...(operators.data ?? [])].sort((a, b) => Number(b.needs_review) - Number(a.needs_review));

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add an operator</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Name" htmlFor="operator-name">
              <Input id="operator-name" value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <Field label="Code" htmlFor="operator-code" hint="Optional">
              <Input id="operator-code" value={code} onChange={(e) => setCode(e.target.value)} />
            </Field>
            <Field label="Badge ID" htmlFor="operator-badge" hint="Optional">
              <Input id="operator-badge" value={badgeId} onChange={(e) => setBadgeId(e.target.value)} />
            </Field>
            <div className="flex items-end">
              <Button type="submit" className="w-full" loading={createOperator.isPending}>
                Add operator
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {operators.data && operators.data.length === 0 ? (
        <EmptyState
          icon={<Users />}
          title="No operators yet"
          description="The roster people are attributed against on production data."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Code</TH>
                <TH>Badge</TH>
                <TH>Status</TH>
                <TH>Review</TH>
              </TR>
            </THead>
            <TBody>
              {sorted.map((o) => (
                <OperatorRow
                  key={o.id}
                  operator={o}
                  others={sorted.filter((other) => other.id !== o.id)}
                  onError={setError}
                />
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <OperatorPerformancePanel />
    </div>
  );
}

// POST /routings/{id}/steps replaces the whole step list, so "add a step" sends
// the existing steps back alongside the new one.
function RoutingRow({
  routing,
  workCenters,
  itemLabel,
  onError,
}: {
  routing: RoutingResponse;
  workCenters: WorkCenterResponse[];
  itemLabel: string;
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [stepWorkCenterId, setStepWorkCenterId] = useState("");
  const [cycleTime, setCycleTime] = useState("");
  const [setupTime, setSetupTime] = useState("");

  const addStep = useMutation({
    mutationFn: async () => {
      const nextSequence = Math.max(0, ...routing.steps.map((s) => s.sequence)) + 1;
      const steps = [
        ...routing.steps.map((s) => ({
          sequence: s.sequence,
          work_center_id: s.work_center_id,
          cycle_time_seconds: s.cycle_time_seconds,
          setup_time_seconds: s.setup_time_seconds,
        })),
        {
          sequence: nextSequence,
          work_center_id: stepWorkCenterId,
          cycle_time_seconds: optionalNumber(cycleTime),
          setup_time_seconds: optionalNumber(setupTime),
        },
      ];
      const res = await apiFetch(`/routings/${routing.id}/steps`, {
        method: "POST",
        body: JSON.stringify(steps),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to add step");
      }
      return res.json();
    },
    onSuccess: () => {
      setStepWorkCenterId("");
      setCycleTime("");
      setSetupTime("");
      queryClient.invalidateQueries({ queryKey: ["routings"] });
    },
    onError: (e: Error) => onError(e.message),
  });

  const activate = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/routings/${routing.id}/activate`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to activate routing");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["routings"] }),
    onError: (e: Error) => onError(e.message),
  });

  const workCenterLabel = (id: string) => {
    const wc = workCenters.find((w) => w.id === id);
    return wc ? `${wc.code} - ${wc.name}` : id;
  };

  return (
    <>
      <TR>
        <TD className="w-6">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-muted-foreground hover:text-foreground"
            title={expanded ? "Collapse" : "Show steps"}
          >
            {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </button>
        </TD>
        <TD className="font-medium">{itemLabel}</TD>
        <TD className="text-muted-foreground">v{routing.version}</TD>
        <TD>
          <Badge variant={statusVariant(routing.status)}>{humanize(routing.status)}</Badge>
        </TD>
        <TD className="text-muted-foreground">{routing.steps.length}</TD>
        <TD>
          {routing.status === "draft" && (
            <Button size="sm" variant="secondary" loading={activate.isPending} onClick={() => activate.mutate()}>
              Activate
            </Button>
          )}
        </TD>
      </TR>
      {expanded && (
        <TR>
          <TD colSpan={6}>
            <div className="space-y-4 py-2">
              {routing.steps.length === 0 ? (
                <p className="text-sm text-muted-foreground">No steps yet.</p>
              ) : (
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Seq</TH>
                        <TH>Work center</TH>
                        <TH>Cycle time (s)</TH>
                        <TH>Setup time (s)</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {routing.steps.map((s) => (
                        <TR key={s.id}>
                          <TD className="font-medium">{s.sequence}</TD>
                          <TD>{workCenterLabel(s.work_center_id)}</TD>
                          <TD className="text-muted-foreground">{s.cycle_time_seconds ?? "-"}</TD>
                          <TD className="text-muted-foreground">{s.setup_time_seconds ?? "-"}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              )}

              {routing.status === "draft" && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    addStep.mutate();
                  }}
                  className="grid gap-4 sm:grid-cols-[1fr_160px_160px_auto] sm:items-end"
                >
                  <Field label="Work center" htmlFor={`step-wc-${routing.id}`}>
                    <Select
                      id={`step-wc-${routing.id}`}
                      value={stepWorkCenterId}
                      onChange={(e) => setStepWorkCenterId(e.target.value)}
                      required
                    >
                      <option value="" disabled>
                        Select a work center
                      </option>
                      {workCenters.map((wc) => (
                        <option key={wc.id} value={wc.id}>
                          {wc.code} - {wc.name}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Cycle time (s)" htmlFor={`step-cycle-${routing.id}`}>
                    <Input
                      id={`step-cycle-${routing.id}`}
                      type="number"
                      min="0"
                      step="0.01"
                      value={cycleTime}
                      onChange={(e) => setCycleTime(e.target.value)}
                    />
                  </Field>
                  <Field label="Setup time (s)" htmlFor={`step-setup-${routing.id}`}>
                    <Input
                      id={`step-setup-${routing.id}`}
                      type="number"
                      min="0"
                      step="0.01"
                      value={setupTime}
                      onChange={(e) => setSetupTime(e.target.value)}
                    />
                  </Field>
                  <Button type="submit" variant="secondary" loading={addStep.isPending}>
                    Add step
                  </Button>
                </form>
              )}
            </div>
          </TD>
        </TR>
      )}
    </>
  );
}

function RoutingsPanel() {
  const queryClient = useQueryClient();
  const [itemId, setItemId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const workCenters = useWorkCenters();
  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });
  const routings = useQuery<RoutingResponse[]>({
    queryKey: ["routings"],
    queryFn: async () => {
      const res = await apiFetch("/routings");
      if (!res.ok) throw new Error("Failed to load routings");
      return res.json();
    },
  });

  // item_id is a query param on this endpoint, not a body field.
  const createRouting = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/routings?item_id=${encodeURIComponent(itemId)}`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to create routing");
      }
      return res.json();
    },
    onSuccess: () => {
      setItemId("");
      queryClient.invalidateQueries({ queryKey: ["routings"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const itemLabel = (id: string) => {
    const item = items.data?.find((i) => i.id === id);
    return item ? `${item.sku} - ${item.name}` : id;
  };

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Create a draft routing</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              createRouting.mutate();
            }}
            className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end"
          >
            <Field label="For item" htmlFor="routing-item">
              <ItemSelect id="routing-item" items={items.data} value={itemId} onChange={setItemId} required />
            </Field>
            <Button type="submit" loading={createRouting.isPending}>
              Create draft
            </Button>
          </form>
        </CardContent>
      </Card>

      {routings.data && routings.data.length === 0 ? (
        <EmptyState
          icon={<RouteIcon />}
          title="No routings yet"
          description="A routing is the ordered list of work centers an item passes through."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH />
                <TH>Item</TH>
                <TH>Version</TH>
                <TH>Status</TH>
                <TH>Steps</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {routings.data?.map((routing) => (
                <RoutingRow
                  key={routing.id}
                  routing={routing}
                  workCenters={workCenters.data ?? []}
                  itemLabel={itemLabel(routing.item_id)}
                  onError={setError}
                />
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}

const SETUP_TABS = [
  { value: "work-centers", label: "Work Centers" },
  { value: "machines", label: "Machines" },
  { value: "operators", label: "Operators" },
  { value: "routings", label: "Routings" },
] as const;
const ALL_TABS = [{ value: "jobs", label: "Jobs" } as const, ...SETUP_TABS];

type Tab = (typeof ALL_TABS)[number]["value"];

export function Production() {
  const org = useCurrentUser();
  // enabled_features isn't known until org-me resolves - default optimistically to
  // "jobs" (the common case) rather than the setup tabs, and only bump off it once
  // loading has actually settled and the org turns out not to have the feature.
  const hasJobs = (org.data?.enabled_features ?? []).includes("production");
  const tabs = hasJobs ? ALL_TABS : SETUP_TABS;
  const tabValues = new Set<string>(tabs.map((t) => t.value));

  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(
    requestedTab && tabValues.has(requestedTab) ? (requestedTab as Tab) : "jobs",
  );

  useEffect(() => {
    if (!org.isLoading && !hasJobs && tab === "jobs") setTab("work-centers");
  }, [org.isLoading, hasJobs, tab]);

  return (
    <div>
      <PageHeader
        title="Production"
        description="The jobs on the floor, and the work centers, machines, and routings behind them."
        actions={<PageTabs tabs={[...tabs]} active={tab} onChange={setTab} />}
      />
      <div key={tab} className="animate-fade-in">
        {tab === "jobs" && hasJobs && <JobsPanel />}
        {tab === "work-centers" && <WorkCentersPanel />}
        {tab === "machines" && <MachinesPanel />}
        {tab === "operators" && <OperatorsPanel />}
        {tab === "routings" && <RoutingsPanel />}
      </div>
    </div>
  );
}
