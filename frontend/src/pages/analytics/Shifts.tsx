import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { StatCard } from "@/components/StatCard";
import { C, EChart, EngineScreen, fmt, oeeColor, rankedBarOption } from "./shared";

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
}

interface ShiftsResult {
  shifts: ShiftRow[];
  best_shift: string | null;
  worst_shift: string | null;
  metrics_available: string[];
  row_count: number;
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

export function Shifts() {
  return (
    <EngineScreen<ShiftsResult>
      title="Shift Comparison"
      description="Compare shifts head-to-head on OEE, quality, throughput, and downtime — find who's ahead and why."
      path="/analytics/shifts"
      fields={FIELDS}
      runLabel="Compare shifts"
    >
      {(r) => {
        const cols = COLS.filter((c) => r.shifts.some((s) => s[c.key] !== null && s[c.key] !== undefined));
        // Primary chart metric mirrors the backend's ranking axis.
        const primary =
          (["oee_pct", "fpq_pct", "total_units", "total_downtime_min"] as const).find((k) =>
            r.shifts.some((s) => s[k] !== null),
          ) ?? null;
        const primaryLabel = COLS.find((c) => c.key === primary)?.label ?? "";
        const lowerIsBetter = primary === "total_downtime_min";
        return (
          <div className="space-y-6">
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
          </div>
        );
      }}
    </EngineScreen>
  );
}
