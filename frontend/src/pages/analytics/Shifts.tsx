import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  resolveShiftForTime,
  token,
  useShiftSchedule,
} from "./shared";
import { ScheduleEditor } from "./shifts/ScheduleEditor";
import { ShiftForecastCard } from "./shifts/ShiftForecastCard";
import { FIELDS, type ShiftRow, type ShiftsResult, type TrendPoint } from "./shifts/types";

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

        const resolved = resolveShiftForTime(schedule.data ?? [], new Date());
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
                      legend: { textStyle: { color: token("--muted-foreground"), fontSize: 11 }, top: 4 },
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
