import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { StatCard } from "@/components/StatCard";
import { AXIS_LABEL, AXIS_LINE, C, EChart, EngineScreen, SPLIT_LINE, TOOLTIP, fmt, oeeColor, pct, rankedBarOption } from "./shared";

interface TrendPoint {
  date: string;
  oee: number;
  total_units: number;
  scrap_rate_pct: number | null;
}

interface MachineRow {
  machine: string;
  oee: number;
  availability: number;
  performance: number;
  quality: number;
  total_units: number;
  good_units: number;
}

interface PlantSummaryResult {
  oee: number;
  availability: number;
  performance: number;
  quality: number;
  performance_estimated: boolean;
  total_units: number;
  good_units: number;
  row_count: number;
  invalid_cells: number;
  blank_cells: number;
  scrap_rate_pct: number | null;
  fpq_pct: number | null;
  defect_rate_per_1000: number | null;
  trend: TrendPoint[];
  machines: MachineRow[];
}

const FIELDS = [
  { key: "planned_time_col", label: "Planned time", required: true, hints: [/planned/i, /plan_time/i, /scheduled/i] },
  { key: "operating_time_col", label: "Operating time", required: true, hints: [/operating_time/i, /operating/i, /run_time/i, /uptime/i, /actual_time/i] },
  { key: "total_units_col", label: "Total units", required: true, hints: [/total_unit/i, /units_produced/i, /output/i, /total_prod/i] },
  { key: "good_units_col", label: "Good units", required: true, hints: [/good_unit/i, /pass/i, /conform/i, /ok_unit/i, /first_pass/i] },
  { key: "machine_col", label: "Machine / line (optional)", hints: [/machine/i, /line/i, /asset/i, /equip/i, /station/i] },
  { key: "date_col", label: "Date (optional)", hints: [/date/i, /time/i, /period/i, /shift_date/i] },
  { key: "scrap_units_col", label: "Scrap units (optional)", hints: [/scrap/i, /reject/i, /defect_unit/i] },
  { key: "defects_col", label: "Defects (optional)", hints: [/defects/i, /defect_count/i] },
  { key: "target_rate", label: "Target rate (units/min)", kind: "num" as const },
];

function Trend({ trend }: { trend: TrendPoint[] }) {
  const hasScrap = trend.some((t) => t.scrap_rate_pct !== null);
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
              data: hasScrap ? ["OEE", "Scrap rate"] : ["OEE"],
              textStyle: { color: "#64748b", fontSize: 11 },
              top: 4,
              icon: "circle",
              itemWidth: 8,
              itemHeight: 8,
            },
            grid: { top: 40, bottom: 28, left: 52, right: 20 },
            xAxis: {
              type: "category",
              data: trend.map((t) => t.date),
              axisLabel: AXIS_LABEL,
              axisLine: AXIS_LINE,
              axisTick: { show: false },
              boundaryGap: false,
            },
            yAxis: { type: "value", min: 0, max: 100, axisLabel: { ...AXIS_LABEL, formatter: "{value}%" }, splitLine: SPLIT_LINE },
            series: [
              {
                name: "OEE",
                type: "line",
                smooth: true,
                symbol: "none",
                data: trend.map((t) => t.oee),
                lineStyle: { width: 3, color: C.green },
                itemStyle: { color: C.green },
              },
              ...(hasScrap
                ? [
                    {
                      name: "Scrap rate",
                      type: "line" as const,
                      smooth: true,
                      symbol: "none" as const,
                      data: trend.map((t) => t.scrap_rate_pct),
                      lineStyle: { width: 2, color: C.red },
                      itemStyle: { color: C.red },
                    },
                  ]
                : []),
            ],
          }}
        />
      </CardContent>
    </Card>
  );
}

function Machines({ machines }: { machines: MachineRow[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>By machine / line</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <EChart
          height={Math.max(120, machines.length * 36 + 20)}
          option={rankedBarOption({
            categories: machines.map((m) => m.machine),
            values: machines.map((m) => m.oee),
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
                <TH>Machine / line</TH>
                <TH>OEE</TH>
                <TH>Availability</TH>
                <TH>Performance</TH>
                <TH>Quality</TH>
                <TH>Good / Total</TH>
              </TR>
            </THead>
            <TBody>
              {machines.map((m) => (
                <TR key={m.machine}>
                  <TD className="font-medium">{m.machine}</TD>
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

export function PlantSummary() {
  return (
    <EngineScreen<PlantSummaryResult>
      title="Plant Summary"
      description="One-screen rollup across the whole plant: OEE, scrap/quality, and a per-machine breakdown."
      path="/analytics/plant-summary"
      fields={FIELDS}
      runLabel="Compute Summary"
    >
      {(r) => (
        <div className="space-y-6">
          {r.performance_estimated && (
            <Alert variant="warning">No target rate set — Performance assumed 100%; OEE shown is an upper bound.</Alert>
          )}
          {(r.invalid_cells > 0 || r.blank_cells > 0) && (
            <Alert variant="warning">
              {r.invalid_cells > 0 && `${r.invalid_cells} non-numeric/negative value(s) were treated as 0. `}
              {r.blank_cells > 0 && `${r.blank_cells} blank cell(s) were also treated as 0.`}
            </Alert>
          )}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              label="OEE"
              value={
                <span style={{ color: oeeColor(r.oee) }}>
                  {r.oee}%{r.performance_estimated ? "*" : ""}
                </span>
              }
            />
            <StatCard label="Availability" value={pct(r.availability)} />
            <StatCard label="Performance" value={pct(r.performance)} />
            <StatCard label="Quality" value={pct(r.quality)} />
            <StatCard label="Total units" value={fmt(r.total_units)} />
            {r.scrap_rate_pct !== null && <StatCard label="Scrap rate" value={pct(r.scrap_rate_pct)} />}
            {r.fpq_pct !== null && <StatCard label="First Pass Quality" value={pct(r.fpq_pct)} />}
            {r.defect_rate_per_1000 !== null && <StatCard label="Defects / 1000" value={fmt(r.defect_rate_per_1000)} />}
          </div>

          {r.trend.length > 1 && <Trend trend={r.trend} />}
          {r.machines.length > 0 && <Machines machines={r.machines} />}
        </div>
      )}
    </EngineScreen>
  );
}
