import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { StatCard } from "@/components/StatCard";
import { AXIS_LABEL, AXIS_LINE, C, EChart, EngineScreen, SPLIT_LINE, TOOLTIP, fmt, oeeColor, pct, rankedBarOption, token } from "./shared";
import { cn } from "@/lib/utils";
import type { Machine, OeeResult, TrendPoint } from "./oee/types";
import { Revenue, SixBigLosses } from "./oee/LossPanels";

const FIELDS = [
  { key: "planned_time_col", label: "Planned time", required: true, hints: [/planned/i, /plan_time/i, /scheduled/i] },
  { key: "operating_time_col", label: "Operating time", required: true, hints: [/operating_time/i, /operating/i, /run_time/i, /uptime/i, /actual_time/i] },
  { key: "total_units_col", label: "Total units", required: true, hints: [/total_unit/i, /units_produced/i, /output/i, /total_prod/i] },
  { key: "good_units_col", label: "Good units", required: true, hints: [/good_unit/i, /pass/i, /conform/i, /ok_unit/i, /first_pass/i] },
  { key: "machine_col", label: "Machine (optional)", hints: [/machine/i, /line/i, /asset/i, /equip/i, /station/i] },
  { key: "date_col", label: "Date (optional)", hints: [/date/i, /time/i, /period/i, /shift_date/i] },
  { key: "target_rate", label: "Target rate (units/min)", kind: "num" as const },
  { key: "unit_price", label: "Unit price ($, for revenue impact)", kind: "num" as const },
  { key: "unplanned_downtime_col", label: "Unplanned downtime (optional, min)", hints: [/unplanned/i, /breakdown/i, /downtime/i] },
  { key: "planned_downtime_col", label: "Setup/changeover time (optional, min)", hints: [/setup/i, /changeover/i, /planned_downtime/i] },
  { key: "minor_stops_col", label: "Minor stops (optional, min)", hints: [/minor_stop/i, /idling/i, /micro_stop/i] },
  { key: "startup_rejects_col", label: "Startup rejects (optional, units)", hints: [/startup_reject/i, /warmup/i, /warm_up/i] },
];

const METRIC_HELP: Record<string, string> = {
  OEE: "Availability × Performance × Quality — the single number summarizing all three losses.",
  Availability: "Operating time ÷ Planned time — how much of your scheduled time the machine actually ran.",
  Performance: "Actual output ÷ ideal output at the target rate — how fast it ran while running.",
  Quality: "Good units ÷ Total units — how much of what it made was good.",
  TEEP: "OEE × Utilization — OEE if you also count all calendar time, not just planned time.",
  Utilization: "Planned time ÷ all calendar time in the dataset's date range (24/7 across every machine).",
};

const LEVELS: [number, string, "success" | "info" | "warning" | "danger"][] = [
  [85, "World Class", "success"],
  [65, "Good", "info"],
  [45, "Fair", "warning"],
  [0, "Poor", "danger"],
];

function Gauge({ r }: { r: OeeResult }) {
  const [, level, variant] = LEVELS.find(([min]) => r.oee >= min)!;
  const subs = [
    { label: "Availability", value: r.availability, color: C.blue },
    { label: "Performance", value: r.performance, color: C.amber },
    { label: "Quality", value: r.quality, color: C.green },
  ];
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="cursor-help" title={METRIC_HELP.OEE}>OEE</CardTitle>
        <Badge variant={variant}>{level}</Badge>
      </CardHeader>
      <CardContent className="space-y-5">
        <EChart
          height={190}
          option={{
            series: [
              {
                type: "gauge",
                center: ["50%", "88%"],
                radius: "110%",
                startAngle: 180,
                endAngle: 0,
                min: 0,
                max: 100,
                splitNumber: 4,
                axisLine: {
                  lineStyle: {
                    width: 20,
                    color: [
                      [0.45, C.red],
                      [0.65, C.amber],
                      [0.85, C.blue],
                      [1, C.green],
                    ],
                  },
                },
                pointer: { length: "55%", width: 5, offsetCenter: [0, "5%"], itemStyle: { color: token("--foreground") } },
                axisTick: { show: false },
                splitLine: { length: 10, lineStyle: { color: token("--background"), width: 3 } },
                axisLabel: {
                  distance: 24,
                  color: token("--muted-foreground"),
                  fontSize: 10,
                  formatter: (v: number) => ([0, 45, 65, 85, 100].includes(v) ? `${v}` : ""),
                },
                detail: {
                  valueAnimation: true,
                  formatter: "{value}%",
                  color: token("--foreground"),
                  fontSize: 26,
                  fontWeight: "bold",
                  offsetCenter: [0, "-28%"],
                },
                data: [{ value: r.oee }],
              },
            ],
          }}
        />
        <div className="space-y-3">
          {subs.map((m) => (
            <div key={m.label}>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="cursor-help text-muted-foreground" title={METRIC_HELP[m.label]}>
                  {m.label}
                </span>
                <span className="font-semibold" style={{ color: m.color }}>
                  {m.value}%
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-secondary">
                <div
                  className="h-1.5 rounded-full"
                  style={{ width: `${Math.min(100, m.value)}%`, background: m.color }}
                />
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function Trend({ trend }: { trend: TrendPoint[] }) {
  const sampled = trend.length > 60 ? trend.filter((_, i) => i % Math.ceil(trend.length / 60) === 0) : trend;
  const line = (name: string, key: keyof TrendPoint, color: string, main = false) => ({
    name,
    type: "line" as const,
    smooth: true,
    symbol: "none" as const,
    data: sampled.map((t) => t[key]),
    lineStyle: { width: main ? 3 : 1.5, opacity: main ? 1 : 0.5, color },
    itemStyle: { color },
    ...(main && {
      areaStyle: {
        color: {
          type: "linear" as const,
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: "rgba(52,211,153,0.20)" },
            { offset: 1, color: "rgba(52,211,153,0.01)" },
          ],
        },
      },
      markLine: {
        silent: true,
        symbol: "none" as const,
        data: [{ yAxis: 85 }],
        lineStyle: { color: C.green, type: "dashed" as const, opacity: 0.35 },
        label: { position: "insideEndTop" as const, color: C.green, fontSize: 10, formatter: "85% World Class" },
      },
    }),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Trend</CardTitle>
      </CardHeader>
      <CardContent>
        <EChart
          height={280}
          option={{
            tooltip: TOOLTIP,
            legend: {
              data: ["OEE", "Availability", "Performance", "Quality"],
              textStyle: { color: token("--muted-foreground"), fontSize: 11 },
              top: 4,
              icon: "circle",
              itemWidth: 8,
              itemHeight: 8,
            },
            grid: { top: 40, bottom: 28, left: 52, right: 20 },
            xAxis: {
              type: "category",
              data: sampled.map((t) => t.date),
              axisLabel: AXIS_LABEL,
              axisLine: AXIS_LINE,
              axisTick: { show: false },
              boundaryGap: false,
            },
            yAxis: { type: "value", min: 0, max: 100, axisLabel: { ...AXIS_LABEL, formatter: "{value}%" }, splitLine: SPLIT_LINE },
            series: [
              line("OEE", "oee", C.green, true),
              line("Availability", "availability", C.blue),
              line("Performance", "performance", C.amber),
              line("Quality", "quality", C.cyan),
            ],
          }}
        />
      </CardContent>
    </Card>
  );
}

function Machines({ machines }: { machines: Machine[] }) {
  const sorted = [...machines].sort((a, b) => b.oee - a.oee);
  return (
    <Card>
      <CardHeader>
        <CardTitle>By machine</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <EChart
          height={Math.max(120, sorted.length * 36 + 20)}
          option={rankedBarOption({
            categories: sorted.map((m) => m.machine),
            values: sorted.map((m) => m.oee),
            colorOf: (v) => oeeColor(v),
            formatter: (v) => `${v}%`,
            isPct: true,
            markLineAt: 85,
          })}
        />
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Machine</TH>
                <TH>OEE</TH>
                <TH>Availability</TH>
                <TH>Performance</TH>
                <TH>Quality</TH>
                <TH>Good / Total</TH>
              </TR>
            </THead>
            <TBody>
              {sorted.map((m) => (
                <TR key={m.machine}>
                  <TD className="font-medium">
                    {m.machine}
                    {m.invalid_cells > 0 && (
                      <span className="ml-1 cursor-help" title={`${m.invalid_cells} invalid cell(s) treated as 0 — OEE may be understated`}>
                        🛈
                      </span>
                    )}
                  </TD>
                  <TD>
                    <span className="font-semibold" style={{ color: oeeColor(m.oee) }}>
                      {m.oee}%
                    </span>
                  </TD>
                  <TD>{m.availability}%</TD>
                  <TD>{m.performance}%</TD>
                  <TD>{m.quality}%</TD>
                  <TD>
                    {fmt(m.good_units)} / {fmt(m.total_units)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      </CardContent>
    </Card>
  );
}

export function Oee() {
  return (
    <EngineScreen<OeeResult>
      title="OEE"
      description="Overall Equipment Effectiveness — Availability × Performance × Quality, with revenue impact and TPM loss decomposition."
      path="/analytics/oee"
      fields={FIELDS}
      runLabel="Compute OEE"
    >
      {(r) => (
        <div className="space-y-6">
          {(r.availability_clamped || r.quality_clamped) && (
            <Alert variant="destructive">
              {r.availability_clamped &&
                "Operating time exceeds Planned time — check that Planned/Operating time columns aren't swapped. Availability is capped at 100% and may be wrong. "}
              {r.quality_clamped &&
                "Good units exceed Total units — check that Good/Total unit columns aren't swapped. Quality is capped at 100% and may be wrong."}
            </Alert>
          )}
          {r.performance_estimated && (
            <Alert variant="warning">No target rate set — Performance assumed 100%; OEE shown is an upper bound.</Alert>
          )}
          {(r.invalid_cells > 0 || r.blank_cells > 0) && (
            <Alert variant="warning">
              {r.invalid_cells > 0 &&
                `${r.invalid_cells} non-numeric/negative value(s) in ${r.rows_with_issues} row(s) were treated as 0 — OEE may be understated. `}
              {r.blank_cells > 0 && `${r.blank_cells} blank cell(s) were also treated as 0.`}
            </Alert>
          )}
          {r.duplicate_rows > 0 && (
            <Alert variant="warning">
              {`Looks like ${r.duplicate_rows} row(s) were uploaded twice (identical date+machine+values) — they're counted twice in every total below.`}
            </Alert>
          )}
          {r.date_gap_days > 0 && (
            <Alert variant="warning">
              {`${r.date_gap_days} calendar day(s) in this date range have no rows at all — trend and TEEP treat those as "no production", not "no data".`}
            </Alert>
          )}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              label="OEE"
              size="hero"
              value={null}
              heroValue={r.oee}
              heroFormat={(n) => `${n.toFixed(1)}%${r.performance_estimated ? "*" : ""}`}
              heroColor={oeeColor(r.oee)}
              tone="primary"
              title={METRIC_HELP.OEE}
              className="lg:col-span-2"
            />
            <StatCard label="Availability" value={pct(r.availability)} tone="accent" title={METRIC_HELP.Availability} />
            <StatCard label="Performance" value={pct(r.performance)} tone="success" title={METRIC_HELP.Performance} />
            <StatCard label="Quality" value={pct(r.quality)} tone="warning" title={METRIC_HELP.Quality} />
          </div>

          {r.revenue_impact && <Revenue ri={r.revenue_impact} />}

          <div className="grid gap-6 lg:grid-cols-2">
            <Gauge r={r} />
            <Card>
              <CardHeader>
                <CardTitle>Summary</CardTitle>
              </CardHeader>
              <CardContent>
                <dl className="space-y-3 text-sm">
                  {(
                    [
                      ["Total planned time", fmt(r.total_planned)],
                      ["Total operating time", fmt(r.total_operating)],
                      ["Total units produced", fmt(r.total_units)],
                      ["Good units", fmt(r.good_units)],
                      ["Rows processed", fmt(r.row_count)],
                      ...(r.teep
                        ? ([
                            ["TEEP", `${r.teep.teep}%`],
                            ["Utilization", `${r.teep.utilization}%`],
                          ] as [string, string][])
                        : []),
                    ] as [string, string][]
                  ).map(([label, val]) => (
                    <div key={label} className="flex items-center justify-between border-b border-border/50 pb-2">
                      <dt className={cn("text-muted-foreground", METRIC_HELP[label] && "cursor-help")} title={METRIC_HELP[label]}>
                        {label}
                      </dt>
                      <dd className="font-semibold">{val}</dd>
                    </div>
                  ))}
                </dl>
              </CardContent>
            </Card>
          </div>

          {r.trend && r.trend.length > 1 && <Trend trend={r.trend} />}
          {r.machines && r.machines.length > 0 && <Machines machines={r.machines} />}
          {r.six_big_losses && r.six_big_losses.buckets.length > 0 && <SixBigLosses six={r.six_big_losses} />}
        </div>
      )}
    </EngineScreen>
  );
}
