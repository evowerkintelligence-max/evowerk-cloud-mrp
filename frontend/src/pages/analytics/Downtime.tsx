import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  gradientAreaSeries,
  paretoOption,
} from "./shared";

interface DowntimeResult {
  total_incidents: number;
  total_minutes: number;
  mttr_minutes: number;
  top_category: string | null;
  by_category: { category: string; incidents: number; total_minutes: number; pct_of_total: number; cumulative_pct: number }[];
  by_machine: { machine: string; incidents: number; total_minutes: number; mttr_minutes: number }[];
  trend: { date: string; incidents: number; total_minutes: number }[];
}

const FIELDS = [
  { key: "duration_col", label: "Duration", required: true, hints: [/duration/i, /downtime/i, /minutes/i, /hours/i] },
  { key: "duration_unit", label: "Duration unit", kind: "select" as const, options: ["minutes", "hours"] },
  { key: "category_col", label: "Category (optional)", hints: [/categor/i, /reason/i, /cause/i, /type/i] },
  { key: "machine_col", label: "Machine (optional)", hints: [/machine/i, /line/i, /asset/i, /equip/i, /station/i] },
  { key: "timestamp_col", label: "Timestamp (optional)", hints: [/date/i, /time/i, /start/i] },
];

const hours = (min: number) => (min >= 120 ? `${(min / 60).toFixed(1)} h` : `${fmt(Math.round(min))} min`);

export function Downtime() {
  return (
    <EngineScreen<DowntimeResult>
      title="Downtime"
      description="Pareto analysis of downtime — where the minutes go, which machines bleed the most, and MTTR."
      path="/analytics/downtime"
      fields={FIELDS}
      runLabel="Analyze downtime"
    >
      {(r) => (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Incidents" value={fmt(r.total_incidents)} />
            <StatCard label="Total downtime" value={hours(r.total_minutes)} />
            <StatCard label="MTTR" value={`${r.mttr_minutes} min`} hint="mean time to repair" />
            <StatCard label="Top category" value={r.top_category ?? "—"} />
          </div>

          {r.by_category.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Pareto by category</CardTitle>
              </CardHeader>
              <CardContent>
                <EChart
                  height={300}
                  option={paretoOption({
                    categories: r.by_category.map((c) => c.category),
                    values: r.by_category.map((c) => c.total_minutes),
                    cumulativePct: r.by_category.map((c) => c.cumulative_pct),
                    barName: "Minutes",
                    barColor: C.red,
                    yAxisName: "minutes",
                  })}
                />
              </CardContent>
            </Card>
          )}

          {r.trend.length > 1 && (
            <Card>
              <CardHeader>
                <CardTitle>Trend</CardTitle>
              </CardHeader>
              <CardContent>
                <EChart
                  height={260}
                  option={{
                    tooltip: TOOLTIP,
                    grid: { top: 24, bottom: 28, left: 56, right: 20 },
                    xAxis: {
                      type: "category",
                      data: r.trend.map((t) => t.date),
                      axisLabel: AXIS_LABEL,
                      axisLine: AXIS_LINE,
                      boundaryGap: false,
                    },
                    yAxis: { type: "value", name: "minutes", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                    series: [gradientAreaSeries("Downtime (min)", r.trend.map((t) => t.total_minutes), C.red)],
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
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Machine</TH>
                        <TH>Incidents</TH>
                        <TH>Total minutes</TH>
                        <TH>MTTR (min)</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {r.by_machine.map((m) => (
                        <TR key={m.machine}>
                          <TD className="font-medium">{m.machine}</TD>
                          <TD>{fmt(m.incidents)}</TD>
                          <TD>{fmt(m.total_minutes)}</TD>
                          <TD>{m.mttr_minutes}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </EngineScreen>
  );
}
