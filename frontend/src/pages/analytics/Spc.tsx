import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { STAT_TONES, StatCard } from "@/components/StatCard";
import { AXIS_LABEL, AXIS_LINE, C, EChart, EngineScreen, SPLIT_LINE, TOOLTIP, fmt } from "./shared";

export interface SpcPoint {
  label: string;
  value: number;
  range_value?: number;
  in_control: boolean;
}

interface SpcResult {
  chart_type: "individuals" | "xbar_r";
  subgroup_size: number;
  centerline: number;
  ucl: number;
  lcl: number;
  range_centerline: number;
  range_ucl: number;
  range_lcl: number;
  points: SpcPoint[];
  out_of_control_count: number;
  row_count: number;
  truncated: boolean;
  capability?: { mean: number; sigma_within: number; sigma_overall: number; usl: number | null; lsl: number | null; cp: number | null; cpk: number | null; pp: number | null; ppk: number | null };
}

const FIELDS = [
  { key: "value_col", label: "Measurement value", required: true, hints: [/value/i, /measure/i, /reading/i, /dim/i, /weight/i, /length/i] },
  { key: "subgroup_col", label: "Subgroup (optional → Xbar-R)", hints: [/subgroup/i, /sample/i, /batch/i, /lot/i] },
  { key: "order_col", label: "Order / time (optional)", hints: [/date/i, /time/i, /seq/i, /order/i] },
  { key: "usl", label: "Upper spec limit", kind: "num" as const },
  { key: "lsl", label: "Lower spec limit", kind: "num" as const },
];

export function controlChart(points: SpcPoint[], value: (p: SpcPoint) => number | undefined, cl: number, ucl: number, lcl: number, name: string) {
  return {
    tooltip: TOOLTIP,
    grid: { top: 24, bottom: 28, left: 56, right: 88 },
    xAxis: {
      type: "category" as const,
      data: points.map((p) => p.label),
      axisLabel: AXIS_LABEL,
      axisLine: AXIS_LINE,
      boundaryGap: false,
    },
    yAxis: { type: "value" as const, scale: true, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [
      {
        name,
        type: "line" as const,
        symbol: "circle" as const,
        symbolSize: 6,
        data: points.map((p) => ({
          value: value(p),
          itemStyle: p.in_control ? { color: C.blue } : { color: C.red, borderColor: "#fff", borderWidth: 1 },
        })),
        lineStyle: { width: 1.5, color: C.blue },
        markLine: {
          silent: true,
          symbol: "none" as const,
          data: [
            { yAxis: ucl, lineStyle: { color: C.red, type: "dashed" as const }, label: { formatter: `UCL ${ucl}`, color: C.red, fontSize: 10, position: "end" as const } },
            { yAxis: cl, lineStyle: { color: C.green, type: "solid" as const, opacity: 0.6 }, label: { formatter: `CL ${cl}`, color: C.green, fontSize: 10, position: "end" as const } },
            { yAxis: lcl, lineStyle: { color: C.red, type: "dashed" as const }, label: { formatter: `LCL ${lcl}`, color: C.red, fontSize: 10, position: "end" as const } },
          ],
        },
      },
    ],
  };
}

export function Spc() {
  return (
    <EngineScreen<SpcResult>
      title="SPC"
      description="Statistical process control — control charts with Western Electric limits and process capability (Cp/Cpk)."
      path="/analytics/spc"
      fields={FIELDS}
      runLabel="Build control chart"
    >
      {(r) => (
        <div className="space-y-6">
          {r.truncated && (
            <Alert variant="warning">Large dataset — chart shows the first {fmt(r.row_count)} points.</Alert>
          )}
          {r.out_of_control_count > 0 && (
            <Alert variant="destructive">
              {r.out_of_control_count} point(s) outside control limits — the process shows special-cause variation.
            </Alert>
          )}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatCard label="Chart" value={r.chart_type === "xbar_r" ? "X̄-R" : "I-MR"} hint={`subgroup n=${r.subgroup_size}`} tone="primary" />
            <StatCard label="Centerline" value={fmt(r.centerline)} tone="accent" />
            <StatCard label="UCL" value={fmt(r.ucl)} tone="success" />
            <StatCard label="LCL" value={fmt(r.lcl)} tone="warning" />
            <StatCard
              label="Out of control"
              value={<span style={{ color: r.out_of_control_count > 0 ? C.red : C.green }}>{r.out_of_control_count}</span>}
              hint={`${fmt(r.row_count)} points`}
              tone="primary"
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>{r.chart_type === "xbar_r" ? "X̄ chart (subgroup means)" : "Individuals chart"}</CardTitle>
            </CardHeader>
            <CardContent>
              <EChart height={320} option={controlChart(r.points, (p) => p.value, r.centerline, r.ucl, r.lcl, "Value")} />
            </CardContent>
          </Card>

          {r.chart_type === "xbar_r" && (
            <Card>
              <CardHeader>
                <CardTitle>R chart (subgroup ranges)</CardTitle>
              </CardHeader>
              <CardContent>
                <EChart
                  height={260}
                  option={controlChart(r.points, (p) => p.range_value, r.range_centerline, r.range_ucl, r.range_lcl, "Range")}
                />
              </CardContent>
            </Card>
          )}

          {r.capability && (
            <Card>
              <CardHeader>
                <CardTitle>Process capability</CardTitle>
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                {(
                  [
                    ["Cp", r.capability.cp],
                    ["Cpk", r.capability.cpk],
                    ["Pp", r.capability.pp],
                    ["Ppk", r.capability.ppk],
                  ] as [string, number | null][]
                ).map(([label, v], i) => (
                  <StatCard
                    key={label}
                    label={label}
                    value={
                      v === null ? "—" : <span style={{ color: v >= 1.33 ? C.green : v >= 1 ? C.amber : C.red }}>{v}</span>
                    }
                    hint={v === null ? undefined : v >= 1.33 ? "capable" : v >= 1 ? "marginal" : "not capable"}
                    tone={STAT_TONES[i % STAT_TONES.length]}
                  />
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </EngineScreen>
  );
}
