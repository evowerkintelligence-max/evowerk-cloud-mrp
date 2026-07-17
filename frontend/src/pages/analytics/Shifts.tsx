import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { StatCard } from "@/components/StatCard";
import {
  AXIS_LABEL,
  AXIS_LINE,
  C,
  EChart,
  EngineScreen,
  SPLIT_LINE,
  TOOLTIP,
  fmt,
  oeeColor,
  paceStatus,
  rankedBarOption,
} from "./shared";
import {
  useAutoMLRun,
  useAutoMLRuns,
  useCreateAutoMLRun,
  useForecastTrajectory,
} from "../automl/api";

interface DowntimeCategoryBreakdown {
  category: string;
  total_minutes: number;
  pct: number;
}

interface ShiftRow {
  shift: string;
  row_count: number;
  total_units: number | null;
  total_scrap: number | null;
  scrap_rate_pct: number | null;
  fpq_pct: number | null;
  availability_pct: number | null;
  quality_pct: number | null;
  oee_pct: number | null;
  total_downtime_min: number | null;
  downtime_by_category: DowntimeCategoryBreakdown[];
}

interface ShiftMachineRow {
  shift: string;
  machine: string;
  total_units: number | null;
  total_scrap: number | null;
  scrap_rate_pct: number | null;
  fpq_pct: number | null;
  availability_pct: number | null;
  quality_pct: number | null;
  oee_pct: number | null;
  total_downtime_min: number | null;
}

interface TrendPoint {
  period: string;
  shift: string;
  value: number | null;
}

interface HourlyPoint {
  shift: string;
  hour: string;
  units: number;
}

interface ShiftsResult {
  shifts: ShiftRow[];
  best_shift: string | null;
  worst_shift: string | null;
  metrics_available: string[];
  row_count: number;
  invalid_cells: number;
  rows_with_issues: number;
  unparsed_date_rows: number;
  by_machine: ShiftMachineRow[];
  trend: TrendPoint[];
  hourly: HourlyPoint[];
  target_rate: number | null;
}

interface ScheduleItem {
  shift_label: string;
  start_time: string;
  end_time: string;
}

const FIELDS = [
  { key: "shift_col", label: "Shift", required: true, hints: [/shift/i, /crew/i, /team/i] },
  { key: "units_col", label: "Units (optional)", hints: [/units_produced/i, /total_unit/i, /output/i, /units/i] },
  { key: "planned_col", label: "Planned time (optional)", hints: [/planned/i, /scheduled/i] },
  { key: "operating_col", label: "Operating time (optional)", hints: [/operating/i, /run_time/i, /uptime/i] },
  { key: "good_units_col", label: "Good units (optional)", hints: [/good_unit/i, /pass/i, /conform/i] },
  { key: "total_units_col", label: "Total units (optional)", hints: [/total_unit/i, /units_produced/i] },
  { key: "scrap_col", label: "Scrap (optional)", hints: [/scrap/i, /defect/i, /reject/i] },
  { key: "downtime_col", label: "Downtime (optional)", hints: [/downtime/i, /down_min/i, /stop/i] },
  { key: "downtime_category_col", label: "Downtime reason (optional)", hints: [/reason/i, /category/i, /downtime_cat/i] },
  { key: "machine_col", label: "Machine (optional)", hints: [/machine/i, /line/i, /asset/i, /equip/i, /station/i] },
  { key: "timestamp_col", label: "Timestamp (optional)", hints: [/timestamp/i, /datetime/i, /date/i, /\bts\b/i, /period/i] },
  { key: "target_rate", label: "Target rate (units/min)", kind: "num" as const },
];

// Table columns rendered only when at least one shift has a value for them.
const COLS: { key: keyof ShiftRow; label: string; render?: (v: number) => JSX.Element | string }[] = [
  { key: "oee_pct", label: "OEE", render: (v) => <span style={{ color: oeeColor(v) }}>{v}%</span> },
  { key: "availability_pct", label: "Availability", render: (v) => `${v}%` },
  { key: "quality_pct", label: "Quality", render: (v) => `${v}%` },
  { key: "fpq_pct", label: "FPQ", render: (v) => `${v}%` },
  { key: "total_units", label: "Units" },
  { key: "total_scrap", label: "Scrap" },
  { key: "scrap_rate_pct", label: "Scrap rate", render: (v) => `${v}%` },
  { key: "total_downtime_min", label: "Downtime (min)" },
];

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function minutesOfDay(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// Matches `now` against each configured shift window, handling the case
// where a shift crosses midnight (end_time < start_time).
function resolveCurrentShift(schedule: ScheduleItem[], now: Date): { shift: string; minutesLeft: number } | null {
  const nowMin = now.getHours() * 60 + now.getMinutes();
  for (const s of schedule) {
    const start = minutesOfDay(s.start_time);
    const end = minutesOfDay(s.end_time);
    const wraps = end <= start;
    const within = wraps ? nowMin >= start || nowMin < end : nowMin >= start && nowMin < end;
    if (within) {
      const minutesLeft = wraps ? (nowMin >= start ? end + 24 * 60 - nowMin : end - nowMin) : end - nowMin;
      return { shift: s.shift_label, minutesLeft };
    }
  }
  return null;
}

function useShiftSchedule() {
  return useQuery<ScheduleItem[]>({
    queryKey: ["shift-schedule"],
    queryFn: async () => {
      const res = await apiFetch("/analytics/shifts/schedule");
      if (!res.ok) throw new Error("Failed to load shift schedule");
      return res.json();
    },
  });
}

// Weekly-recurring start/end time per shift label — powers the "current
// shift" resolution for the handover and hourly-pace cards below.
function ScheduleEditor({ shiftLabels }: { shiftLabels: string[] }) {
  const qc = useQueryClient();
  const schedule = useShiftSchedule();
  const byLabel = new Map((schedule.data ?? []).map((s) => [s.shift_label, s]));
  const [form, setForm] = useState<Record<string, { start: string; end: string }>>({});

  const save = useMutation({
    mutationFn: async () => {
      const items = shiftLabels
        .map((label) => ({
          shift_label: label,
          start_time: form[label]?.start ?? byLabel.get(label)?.start_time ?? "",
          end_time: form[label]?.end ?? byLabel.get(label)?.end_time ?? "",
        }))
        .filter((i) => i.start_time && i.end_time);
      const res = await apiFetch("/analytics/shifts/schedule", { method: "PUT", body: JSON.stringify(items) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to save shift schedule");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["shift-schedule"] }),
  });

  if (shiftLabels.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Shift schedule</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Set each shift's start/end time to resolve a "current shift" for the handover and hourly-pace views below.
        </p>
        {shiftLabels.map((label) => (
          <div key={label} className="flex flex-wrap items-end gap-3">
            <div className="w-24 text-sm font-medium">{label}</div>
            <Field label="Start">
              <Input
                type="time"
                value={form[label]?.start ?? byLabel.get(label)?.start_time ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    [label]: { start: e.target.value, end: form[label]?.end ?? byLabel.get(label)?.end_time ?? "" },
                  })
                }
              />
            </Field>
            <Field label="End">
              <Input
                type="time"
                value={form[label]?.end ?? byLabel.get(label)?.end_time ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    [label]: { start: form[label]?.start ?? byLabel.get(label)?.start_time ?? "", end: e.target.value },
                  })
                }
              />
            </Field>
          </div>
        ))}
        <Button variant="secondary" size="sm" loading={save.isPending} onClick={() => save.mutate()}>
          Save schedule
        </Button>
        {save.isError && <p className="text-sm text-destructive">{(save.error as Error).message}</p>}
      </CardContent>
    </Card>
  );
}

interface ForecastSource {
  dataset_id: string;
  target_column: string;
  time_column: string;
  metric_label: string;
  rows: number;
}

// N_LAGS + 1 in backend/app/services/automl_engine.py's recursive trajectory
// walk - the minimum history a forecast run needs before it can train at all.
const MIN_FORECAST_ROWS = 15;

function useShiftForecastSource(shift: string, requestBody: Record<string, unknown> | null) {
  return useQuery<ForecastSource>({
    queryKey: ["shift-forecast-source", shift, requestBody],
    queryFn: async () => {
      const res = await apiFetch("/analytics/shifts/forecast-source", {
        method: "POST",
        body: JSON.stringify({ ...requestBody, shift }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to prepare forecast data");
      return res.json();
    },
    enabled: !!requestBody && !!shift,
  });
}

// Single-shift forecast card - materializes the shift's trend into a Dataset
// via forecast-source, then trains/polls/renders through the *existing*
// AutoML endpoints and hooks (useCreateAutoMLRun/useAutoMLRun/
// useForecastTrajectory), same as the AutoML page itself uses.
function ShiftForecastCard({ shift, requestBody }: { shift: string; requestBody: Record<string, unknown> | null }) {
  const source = useShiftForecastSource(shift, requestBody);
  const runs = useAutoMLRuns();
  const [runId, setRunId] = useState<string | null>(null);

  useEffect(() => {
    setRunId(null);
  }, [shift]);

  const existingRun = source.data
    ? [...(runs.data ?? [])]
        .filter((r) => r.dataset_id === source.data!.dataset_id && r.task === "forecasting")
        .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
    : undefined;
  const activeRunId = runId ?? existingRun?.id;

  const run = useAutoMLRun(activeRunId);
  const trainMut = useCreateAutoMLRun();
  const trajectory = useForecastTrajectory(activeRunId, run.data?.status === "success");

  function train() {
    if (!source.data) return;
    trainMut.mutate(
      {
        name: `Shift forecast: ${shift}`,
        dataset_id: source.data.dataset_id,
        task: "forecasting",
        target_column: source.data.target_column,
        feature_columns: [],
        thoroughness: "fast",
        algorithms: null,
        handle_class_imbalance: true,
        time_column: source.data.time_column,
        horizon: 14,
        forecast_strategy: "recursive",
        time_budget_seconds: null,
        search_method: "random",
        auto_retrain: false,
      },
      { onSuccess: (r) => setRunId(r.id) },
    );
  }

  const isTraining = trainMut.isPending || run.data?.status === "queued" || run.data?.status === "running";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Forecast — {shift}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {source.isLoading && <p className="text-sm text-muted-foreground">Preparing forecast data…</p>}
        {source.isError && <p className="text-sm text-destructive">{(source.error as Error).message}</p>}
        {source.data && (
          <>
            <p className="text-xs text-muted-foreground">
              {source.data.metric_label} · {source.data.rows} day(s) of history
              {source.data.rows < MIN_FORECAST_ROWS &&
                ` — forecasting needs at least ${MIN_FORECAST_ROWS} days of history for this shift.`}
            </p>
            <Button size="sm" loading={isTraining} onClick={train}>
              {activeRunId ? "Retrain forecast" : "Train forecast"}
            </Button>
            {run.data?.status === "failed" && <p className="text-sm text-destructive">{run.data.error}</p>}
            {trajectory.data && (
              <>
                <EChart
                  height={220}
                  option={{
                    tooltip: TOOLTIP,
                    grid: { top: 20, bottom: 40, left: 48, right: 20 },
                    xAxis: {
                      type: "category",
                      data: [
                        ...trajectory.data.history.map((h) => h.time),
                        ...trajectory.data.points.map((p) => p.time),
                      ],
                      axisLabel: { ...AXIS_LABEL, rotate: 30 },
                      axisLine: AXIS_LINE,
                    },
                    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                    series: [
                      {
                        name: "Actual",
                        type: "line" as const,
                        symbol: "none" as const,
                        data: [
                          ...trajectory.data.history.map((h) => h.actual),
                          ...trajectory.data.points.map(() => null),
                        ],
                        lineStyle: { width: 2, color: C.blue },
                      },
                      {
                        name: "Forecast",
                        type: "line" as const,
                        symbol: "none" as const,
                        data: [
                          ...trajectory.data.history.map(() => null),
                          ...trajectory.data.points.map((p) => p.prediction),
                        ],
                        lineStyle: { width: 2, color: C.amber, type: "dashed" as const },
                      },
                    ],
                  }}
                />
                {trajectory.data.notes.map((n, i) => (
                  <p key={i} className="text-xs text-muted-foreground">
                    {n}
                  </p>
                ))}
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function Shifts() {
  const schedule = useShiftSchedule();
  const [machineShift, setMachineShift] = useState("");
  const [handoverShift, setHandoverShift] = useState("");

  return (
    <EngineScreen<ShiftsResult>
      title="Shift Comparison"
      description="Compare shifts head-to-head on OEE, quality, throughput, and downtime — find who's ahead and why."
      path="/analytics/shifts"
      fields={FIELDS}
      runLabel="Compare shifts"
    >
      {(r, reqBody) => {
        const cols = COLS.filter((c) => r.shifts.some((s) => s[c.key] !== null && s[c.key] !== undefined));
        // Primary chart metric mirrors the backend's ranking axis.
        const primary =
          (["oee_pct", "fpq_pct", "total_units", "total_downtime_min"] as const).find((k) =>
            r.shifts.some((s) => s[k] !== null),
          ) ?? null;
        const primaryLabel = COLS.find((c) => c.key === primary)?.label ?? "";
        const lowerIsBetter = primary === "total_downtime_min";

        // vs-last-shift delta from the trend series (last two chronological points per shift).
        const deltaByShift = new Map<string, number>();
        const byShiftTrend = new Map<string, TrendPoint[]>();
        for (const t of r.trend) {
          if (t.value === null) continue;
          if (!byShiftTrend.has(t.shift)) byShiftTrend.set(t.shift, []);
          byShiftTrend.get(t.shift)!.push(t);
        }
        for (const [shift, pts] of byShiftTrend) {
          const sorted = [...pts].sort((a, b) => a.period.localeCompare(b.period));
          if (sorted.length >= 2) {
            deltaByShift.set(shift, sorted[sorted.length - 1].value! - sorted[sorted.length - 2].value!);
          }
        }

        const trendPeriods = Array.from(new Set(r.trend.map((t) => t.period))).sort();
        const trendShifts = Array.from(new Set(r.trend.map((t) => t.shift)));
        const palette = Object.values(C);

        const weekdayBuckets: number[][] = [[], [], [], [], [], [], []];
        for (const t of r.trend) {
          if (t.value === null) continue;
          const d = new Date(`${t.period}T00:00:00`);
          if (Number.isNaN(d.getTime())) continue;
          weekdayBuckets[d.getDay()].push(t.value);
        }
        const weekdayAvg = weekdayBuckets.map((vals) =>
          vals.length ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 100) / 100 : null,
        );

        const machineShifts = Array.from(new Set(r.by_machine.map((m) => m.shift)));
        const activeMachineShift = machineShift || machineShifts[0] || "";
        const machineRows = r.by_machine.filter((m) => m.shift === activeMachineShift);

        const resolved = resolveCurrentShift(schedule.data ?? [], new Date());
        const handoverCandidate =
          handoverShift ||
          (resolved && r.shifts.some((s) => s.shift === resolved.shift) ? resolved.shift : r.shifts[0]?.shift ?? "");
        const handoverRow = r.shifts.find((s) => s.shift === handoverCandidate);
        const isLiveHandover = !handoverShift && resolved?.shift === handoverCandidate;

        const hourlyForShift = r.hourly.filter((h) => h.shift === handoverCandidate);
        const latestDate = hourlyForShift.reduce<string | null>((max, h) => {
          const d = h.hour.slice(0, 10);
          return !max || d > max ? d : max;
        }, null);
        const hourlyToday = hourlyForShift
          .filter((h) => h.hour.slice(0, 10) === latestDate)
          .sort((a, b) => a.hour.localeCompare(b.hour));
        const targetPerHour = r.target_rate && r.target_rate > 0 ? r.target_rate * 60 : null;

        return (
          <div className="space-y-6">
            {(r.rows_with_issues > 0 || r.unparsed_date_rows > 0) && (
              <Alert variant="warning">
                {r.rows_with_issues > 0 &&
                  `${r.invalid_cells} non-numeric/negative value(s) in ${r.rows_with_issues} row(s) were treated as 0 — OEE/scrap/downtime figures may be understated. `}
                {r.unparsed_date_rows > 0 &&
                  `${r.unparsed_date_rows} row(s) had a timestamp that couldn't be parsed and were excluded from the trend/hourly views.`}
              </Alert>
            )}

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <StatCard label="Best shift" value={<span style={{ color: C.green }}>{r.best_shift ?? "—"}</span>} tone="success" />
              <StatCard label="Worst shift" value={<span style={{ color: C.red }}>{r.worst_shift ?? "—"}</span>} tone="warning" />
              <StatCard label="Rows analyzed" value={fmt(r.row_count)} tone="primary" />
            </div>

            {primary && (
              <Card>
                <CardHeader>
                  <CardTitle>{primaryLabel} by shift</CardTitle>
                </CardHeader>
                <CardContent>
                  <EChart
                    height={Math.max(140, r.shifts.length * 44 + 30)}
                    option={rankedBarOption({
                      categories: r.shifts.map((s) => s.shift),
                      values: r.shifts.map((s) => Number(s[primary])),
                      colorOf: (_v, i) => {
                        const shift = r.shifts[i].shift;
                        return shift === r.best_shift ? C.green : shift === r.worst_shift ? C.red : C.blue;
                      },
                      formatter: (v) => (primary.endsWith("_pct") ? `${v}%` : fmt(v)),
                      isPct: primary.endsWith("_pct"),
                      barMaxWidth: 26,
                      rightMargin: 72,
                    })}
                  />
                  {lowerIsBetter && <p className="mt-2 text-xs text-muted-foreground">Lower is better for downtime.</p>}
                </CardContent>
              </Card>
            )}

            <Card>
              <CardHeader>
                <CardTitle>All shifts</CardTitle>
              </CardHeader>
              <CardContent>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Shift</TH>
                        <TH>Rows</TH>
                        {cols.map((c) => (
                          <TH key={c.key}>{c.label}</TH>
                        ))}
                      </TR>
                    </THead>
                    <TBody>
                      {r.shifts.map((s) => (
                        <TR key={s.shift}>
                          <TD className="font-medium">
                            {s.shift}
                            {s.shift === r.best_shift && (
                              <Badge variant="success" className="ml-2">
                                best
                              </Badge>
                            )}
                            {s.shift === r.worst_shift && (
                              <Badge variant="danger" className="ml-2">
                                worst
                              </Badge>
                            )}
                            {deltaByShift.has(s.shift) &&
                              (() => {
                                const d = deltaByShift.get(s.shift)!;
                                const good = (d > 0) !== lowerIsBetter;
                                return (
                                  <span className="ml-2 text-xs" style={{ color: good ? C.green : C.red }}>
                                    {d > 0 ? "▲" : "▼"} {Math.abs(Math.round(d * 10) / 10)} vs last
                                  </span>
                                );
                              })()}
                          </TD>
                          <TD>{fmt(s.row_count)}</TD>
                          {cols.map((c) => {
                            const v = s[c.key];
                            return (
                              <TD key={c.key}>
                                {v === null || v === undefined ? "—" : c.render ? c.render(v as number) : fmt(v as number)}
                              </TD>
                            );
                          })}
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
                {r.metrics_available.length > 0 && (
                  <p className="mt-3 text-xs text-muted-foreground">Metrics available: {r.metrics_available.join(", ")}</p>
                )}
              </CardContent>
            </Card>

            {trendShifts.length > 0 && trendPeriods.length > 1 && (
              <Card>
                <CardHeader>
                  <CardTitle>Shift trend</CardTitle>
                </CardHeader>
                <CardContent>
                  <EChart
                    height={280}
                    option={{
                      tooltip: TOOLTIP,
                      legend: { textStyle: { color: "#64748b", fontSize: 11 }, top: 4 },
                      grid: { top: 40, bottom: 28, left: 56, right: 20 },
                      xAxis: { type: "category", data: trendPeriods, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE },
                      yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                      series: trendShifts.map((shift, i) => ({
                        name: shift,
                        type: "line" as const,
                        smooth: true,
                        symbol: "none" as const,
                        data: trendPeriods.map((p) => r.trend.find((t) => t.shift === shift && t.period === p)?.value ?? null),
                        lineStyle: { width: 2, color: palette[i % palette.length] },
                        itemStyle: { color: palette[i % palette.length] },
                      })),
                    }}
                  />
                </CardContent>
              </Card>
            )}

            {weekdayAvg.some((v) => v !== null) && (
              <Card>
                <CardHeader>
                  <CardTitle>Average by day of week</CardTitle>
                </CardHeader>
                <CardContent>
                  <EChart
                    height={200}
                    option={{
                      tooltip: TOOLTIP,
                      grid: { top: 20, bottom: 28, left: 48, right: 20 },
                      xAxis: { type: "category", data: WEEKDAYS, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE },
                      yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                      series: [
                        {
                          type: "bar",
                          data: weekdayAvg,
                          itemStyle: { color: C.blue, borderRadius: [4, 4, 0, 0] },
                        },
                      ],
                    }}
                  />
                </CardContent>
              </Card>
            )}

            {machineShifts.length > 0 && (
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>By machine</CardTitle>
                  <Select value={activeMachineShift} onChange={(e) => setMachineShift(e.target.value)} className="w-40">
                    {machineShifts.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </Select>
                </CardHeader>
                <CardContent>
                  {primary && machineRows.length > 0 ? (
                    <EChart
                      height={Math.max(120, machineRows.length * 36 + 20)}
                      option={rankedBarOption({
                        categories: machineRows.map((m) => m.machine),
                        values: machineRows.map((m) => Number(m[primary])),
                        colorOf: () => C.blue,
                        formatter: (v) => (primary.endsWith("_pct") ? `${v}%` : fmt(v)),
                        isPct: primary.endsWith("_pct"),
                      })}
                    />
                  ) : (
                    <p className="text-sm text-muted-foreground">No machine data for {activeMachineShift}.</p>
                  )}
                </CardContent>
              </Card>
            )}

            {r.shifts.some((s) => s.downtime_by_category.length > 0) && (
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>Handover — loss Pareto</CardTitle>
                  <Select value={handoverCandidate} onChange={(e) => setHandoverShift(e.target.value)} className="w-40">
                    {r.shifts.map((s) => (
                      <option key={s.shift} value={s.shift}>
                        {s.shift}
                      </option>
                    ))}
                  </Select>
                </CardHeader>
                <CardContent>
                  {isLiveHandover && (
                    <p className="mb-2 text-xs text-muted-foreground">
                      Current shift, resolved from the schedule below.
                    </p>
                  )}
                  {handoverRow && handoverRow.downtime_by_category.length > 0 ? (
                    <EChart
                      height={Math.max(120, handoverRow.downtime_by_category.length * 36 + 20)}
                      option={rankedBarOption({
                        categories: handoverRow.downtime_by_category.map((c) => c.category),
                        values: handoverRow.downtime_by_category.map((c) => c.total_minutes),
                        colorOf: () => C.amber,
                        formatter: (v, i) => `${fmt(v)} min (${handoverRow.downtime_by_category[i].pct}%)`,
                      })}
                    />
                  ) : (
                    <p className="text-sm text-muted-foreground">No downtime-by-category data for {handoverCandidate || "this shift"}.</p>
                  )}
                  <p className="mt-2 text-xs text-muted-foreground">
                    Turn on Auto-refresh (30s) above for a live view during handover — genuinely live when this dataset
                    is fed continuously (e.g. via Downtime Tracking).
                  </p>
                </CardContent>
              </Card>
            )}

            {hourlyToday.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Hourly pace — {handoverCandidate}</CardTitle>
                </CardHeader>
                <CardContent>
                  {isLiveHandover && resolved ? (
                    <p className="mb-2 text-sm">
                      {targetPerHour !== null &&
                        (() => {
                          const last = hourlyToday[hourlyToday.length - 1];
                          const behind = Math.max(0, Math.round(targetPerHour - last.units));
                          return behind > 0 ? `${fmt(behind)} units behind pace this hour, ` : "On pace this hour, ";
                        })()}
                      {resolved.minutesLeft} min left in shift.
                    </p>
                  ) : (
                    <p className="mb-2 text-xs text-muted-foreground">
                      Set a shift schedule below to see minutes-left-in-shift.
                    </p>
                  )}
                  <EChart
                    height={220}
                    option={{
                      tooltip: TOOLTIP,
                      grid: { top: 20, bottom: 40, left: 48, right: 20 },
                      xAxis: {
                        type: "category",
                        data: hourlyToday.map((h) => h.hour.slice(11, 16)),
                        axisLabel: { ...AXIS_LABEL, rotate: 30 },
                        axisLine: AXIS_LINE,
                      },
                      yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                      series: [
                        {
                          type: "bar",
                          data: hourlyToday.map((h) => ({
                            value: h.units,
                            itemStyle: {
                              color: targetPerHour ? paceStatus((h.units / targetPerHour) * 100).color : C.blue,
                              borderRadius: [4, 4, 0, 0],
                            },
                          })),
                        },
                        ...(targetPerHour !== null
                          ? [
                              {
                                name: "Target",
                                type: "line" as const,
                                symbol: "none" as const,
                                data: hourlyToday.map(() => targetPerHour),
                                lineStyle: { color: C.amber, type: "dashed" as const, width: 1.5 },
                              },
                            ]
                          : []),
                      ],
                    }}
                  />
                </CardContent>
              </Card>
            )}

            {handoverCandidate && <ShiftForecastCard shift={handoverCandidate} requestBody={reqBody} />}

            <ScheduleEditor shiftLabels={r.shifts.map((s) => s.shift)} />
          </div>
        );
      }}
    </EngineScreen>
  );
}
