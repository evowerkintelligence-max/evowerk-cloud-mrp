import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  gradientAreaSeries,
  paceStatus,
  pct,
  rankedBarOption,
  token,
} from "./shared";

interface ThroughputResult {
  total_units: number;
  period_count: number;
  avg_units_per_period: number;
  peak_units: number;
  peak_period: string | null;
  efficiency_pct: number | null;
  row_count: number;
  invalid_cells: number;
  rows_with_issues: number;
  unparsed_date_rows: number;
  is_target_estimated: boolean;
  trend: { period: string; units: number; planned: number | null; efficiency_pct?: number | null }[];
  by_machine: { machine: string; total_units: number; pct_of_total: number }[];
}

const FIELDS = [
  { key: "units_col", label: "Units", required: true, hints: [/units_produced/i, /total_unit/i, /output/i, /units/i, /qty/i, /quantity/i] },
  { key: "planned_units_col", label: "Planned units (optional)", hints: [/planned_unit/i, /target/i, /plan/i] },
  { key: "machine_col", label: "Machine (optional)", hints: [/machine/i, /line/i, /asset/i, /equip/i, /station/i] },
  { key: "date_col", label: "Date (optional)", hints: [/date/i, /time/i, /period/i, /shift_date/i] },
];

export function Throughput() {
  return (
    <EngineScreen<ThroughputResult>
      title="Throughput"
      description="Production output over time — actual vs. planned, peak periods, and machine contribution."
      path="/analytics/throughput"
      fields={FIELDS}
      runLabel="Analyze throughput"
    >
      {(r) => (
        <div className="space-y-6">
          {(r.rows_with_issues > 0 || r.unparsed_date_rows > 0) && (
            <Alert variant="warning">
              {r.rows_with_issues > 0 &&
                `${r.invalid_cells} non-numeric/negative value(s) in ${r.rows_with_issues} row(s) were treated as 0 — check that the mapped columns are the ones you intended. `}
              {r.unparsed_date_rows > 0 &&
                `${r.unparsed_date_rows} row(s) had a date that couldn't be parsed and were grouped by raw text instead — the trend may not be in chronological order for those.`}
            </Alert>
          )}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Total units" value={fmt(r.total_units)} tone="primary" />
            <StatCard label="Avg / period" value={fmt(r.avg_units_per_period)} hint={`${r.period_count} periods`} tone="accent" />
            <StatCard label="Peak" value={fmt(r.peak_units)} hint={r.peak_period ?? undefined} tone="success" />
            <StatCard
              label={r.is_target_estimated ? "Pace vs. estimated target" : "Pace vs. plan"}
              value={
                r.efficiency_pct === null ? (
                  "—"
                ) : (
                  <span style={{ color: paceStatus(r.efficiency_pct).color }}>{paceStatus(r.efficiency_pct).label}</span>
                )
              }
              hint={r.efficiency_pct === null ? undefined : `${pct(r.efficiency_pct)} of ${r.is_target_estimated ? "historical average" : "plan"}`}
              tone="warning"
            />
          </div>

          {r.trend.length > 1 && (
            <Card>
              <CardHeader>
                <CardTitle>Output per period</CardTitle>
              </CardHeader>
              <CardContent>
                <EChart
                  height={300}
                  option={{
                    tooltip: TOOLTIP,
                    legend: { textStyle: { color: token("--muted-foreground"), fontSize: 11 }, top: 4 },
                    grid: { top: 40, bottom: 28, left: 56, right: 20 },
                    xAxis: {
                      type: "category",
                      data: r.trend.map((t) => t.period),
                      axisLabel: AXIS_LABEL,
                      axisLine: AXIS_LINE,
                      boundaryGap: false,
                    },
                    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                    series: [
                      gradientAreaSeries("Units", r.trend.map((t) => t.units), C.blue),
                      ...(r.trend.some((t) => t.planned !== null)
                        ? [
                            {
                              name: r.is_target_estimated ? "Target (est.)" : "Planned",
                              type: "line" as const,
                              smooth: true,
                              symbol: "none" as const,
                              data: r.trend.map((t) => t.planned),
                              lineStyle: { width: 1.5, color: C.amber, type: "dashed" as const },
                              itemStyle: { color: C.amber },
                            },
                          ]
                        : []),
                    ],
                  }}
                />
              </CardContent>
            </Card>
          )}

          {r.by_machine.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>By machine</CardTitle>
              </CardHeader>
              <CardContent>
                {(() => {
                  const rows = [...r.by_machine].reverse(); // horizontal bars read top-down
                  return (
                    <EChart
                      height={Math.max(120, rows.length * 36 + 20)}
                      option={rankedBarOption({
                        categories: rows.map((m) => m.machine),
                        values: rows.map((m) => m.total_units),
                        colorOf: () => C.blue,
                        formatter: (v, i) => `${fmt(v)} (${rows[i].pct_of_total}%)`,
                        rightMargin: 110,
                      })}
                    />
                  );
                })()}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </EngineScreen>
  );
}
