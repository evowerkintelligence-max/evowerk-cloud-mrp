import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { StatCard } from "@/components/StatCard";
import { AXIS_LABEL, AXIS_LINE, C, EChart, EngineScreen, SPLIT_LINE, TOOLTIP, fmt, gradientAreaSeries, paretoOption } from "./shared";

interface ScrapResult {
  total_units: number;
  total_scrap: number;
  scrap_rate_pct: number;
  fpq_pct: number;
  defect_rate_per_1000: number;
  by_reason: { reason: string; count: number; pct_of_total: number; cumulative_pct: number }[];
  by_machine: { machine: string; total_units: number; scrap_units: number; scrap_rate_pct: number }[];
  trend: { period: string; total_units: number; scrap_units: number; scrap_rate_pct: number }[];
}

const FIELDS = [
  { key: "total_units_col", label: "Total units", required: true, hints: [/total_unit/i, /units_produced/i, /output/i] },
  { key: "scrap_col", label: "Scrap units", required: true, hints: [/scrap/i, /defect/i, /reject/i, /fail/i, /bad/i] },
  { key: "reason_col", label: "Reason (optional)", hints: [/reason/i, /defect_type/i, /cause/i, /categor/i] },
  { key: "machine_col", label: "Machine (optional)", hints: [/machine/i, /line/i, /asset/i, /equip/i, /station/i] },
  { key: "date_col", label: "Date (optional)", hints: [/date/i, /time/i, /period/i, /shift_date/i] },
];

export function Scrap() {
  return (
    <EngineScreen<ScrapResult>
      title="Scrap & Quality"
      description="Scrap rate, first-pass quality, and a Pareto of defect reasons to attack first."
      path="/analytics/scrap"
      fields={FIELDS}
      runLabel="Analyze scrap"
    >
      {(r) => (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatCard label="Total units" value={fmt(r.total_units)} tone="primary" />
            <StatCard label="Scrap units" value={<span style={{ color: C.red }}>{fmt(r.total_scrap)}</span>} tone="accent" />
            <StatCard
              label="Scrap rate"
              value={
                <span style={{ color: r.scrap_rate_pct <= 1 ? C.green : r.scrap_rate_pct <= 5 ? C.amber : C.red }}>
                  {r.scrap_rate_pct}%
                </span>
              }
              tone="success"
            />
            <StatCard label="First-pass quality" value={`${r.fpq_pct}%`} tone="warning" />
            <StatCard label="Defects / 1000" value={fmt(r.defect_rate_per_1000)} tone="primary" />
          </div>

          {r.by_reason.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Pareto by reason</CardTitle>
              </CardHeader>
              <CardContent>
                <EChart
                  height={300}
                  option={paretoOption({
                    categories: r.by_reason.map((x) => x.reason),
                    values: r.by_reason.map((x) => x.count),
                    cumulativePct: r.by_reason.map((x) => x.cumulative_pct),
                    barName: "Scrap",
                    barColor: C.pink,
                    yAxisName: "scrap units",
                  })}
                />
              </CardContent>
            </Card>
          )}

          {r.trend.length > 1 && (
            <Card>
              <CardHeader>
                <CardTitle>Scrap rate trend</CardTitle>
              </CardHeader>
              <CardContent>
                <EChart
                  height={260}
                  option={{
                    tooltip: TOOLTIP,
                    grid: { top: 24, bottom: 28, left: 48, right: 20 },
                    xAxis: {
                      type: "category",
                      data: r.trend.map((t) => t.period),
                      axisLabel: AXIS_LABEL,
                      axisLine: AXIS_LINE,
                      boundaryGap: false,
                    },
                    yAxis: { type: "value", axisLabel: { ...AXIS_LABEL, formatter: "{value}%" }, splitLine: SPLIT_LINE },
                    series: [gradientAreaSeries("Scrap rate", r.trend.map((t) => t.scrap_rate_pct), C.pink)],
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
                        <TH>Total units</TH>
                        <TH>Scrap units</TH>
                        <TH>Scrap rate</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {r.by_machine.map((m) => (
                        <TR key={m.machine}>
                          <TD className="font-medium">{m.machine}</TD>
                          <TD>{fmt(m.total_units)}</TD>
                          <TD>{fmt(m.scrap_units)}</TD>
                          <TD>
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 w-24 rounded-full bg-secondary">
                                <div
                                  className="h-1.5 rounded-full"
                                  style={{
                                    width: `${Math.min(100, m.scrap_rate_pct * 4)}%`,
                                    background: m.scrap_rate_pct <= 1 ? C.green : m.scrap_rate_pct <= 5 ? C.amber : C.red,
                                  }}
                                />
                              </div>
                              <span
                                className="text-xs font-semibold"
                                style={{ color: m.scrap_rate_pct <= 1 ? C.green : m.scrap_rate_pct <= 5 ? C.amber : C.red }}
                              >
                                {m.scrap_rate_pct}%
                              </span>
                            </div>
                          </TD>
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
