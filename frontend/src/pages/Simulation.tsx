import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "@/pages/analytics/shared";

interface Baseline {
  shifts_per_day: number;
  shift_hours: number;
  workers_per_shift: number;
  avg_wage_per_hour: number;
  machine_count: number;
  throughput_per_shift: number;
  oee_pct: number;
  demand_units_per_day: number;
  defect_rate_pct: number;
  scrap_cost_per_unit: number;
  raw_material_cost_per_unit: number;
  selling_price_per_unit: number;
}

const DEFAULT_BASELINE: Baseline = {
  shifts_per_day: 2,
  shift_hours: 8,
  workers_per_shift: 25,
  avg_wage_per_hour: 20,
  machine_count: 10,
  throughput_per_shift: 1000,
  oee_pct: 75,
  demand_units_per_day: 1800,
  defect_rate_pct: 3.5,
  scrap_cost_per_unit: 5,
  raw_material_cost_per_unit: 15,
  selling_price_per_unit: 40,
};

const BASELINE_FIELDS: { key: keyof Baseline; label: string; step?: string }[] = [
  { key: "shifts_per_day", label: "Shifts / day" },
  { key: "shift_hours", label: "Shift hours" },
  { key: "workers_per_shift", label: "Workers / shift" },
  { key: "avg_wage_per_hour", label: "Avg wage / hour ($)", step: "0.01" },
  { key: "machine_count", label: "Machine count" },
  { key: "throughput_per_shift", label: "Throughput / shift (units)" },
  { key: "oee_pct", label: "OEE (%)", step: "0.1" },
  { key: "demand_units_per_day", label: "Demand (units/day)" },
  { key: "defect_rate_pct", label: "Defect rate (%)", step: "0.1" },
  { key: "scrap_cost_per_unit", label: "Scrap cost / unit ($)", step: "0.01" },
  { key: "raw_material_cost_per_unit", label: "Raw material cost / unit ($)", step: "0.01" },
  { key: "selling_price_per_unit", label: "Selling price / unit ($)", step: "0.01" },
];

type Changes = Record<string, number>;

const PRESETS: { key: string; label: string; apply: (b: Baseline) => Changes }[] = [
  { key: "add_shift", label: "Add a Shift", apply: (b) => ({ shifts_per_day: b.shifts_per_day + 1 }) },
  { key: "demand_20", label: "Demand +20%", apply: () => ({ demand_change_pct: 20 }) },
  { key: "oee_5", label: "OEE +5 pp", apply: () => ({ oee_change_pct: 5 }) },
  { key: "defect_half", label: "Halve Defect Rate", apply: (b) => ({ defect_change_pct: -(b.defect_rate_pct / 2) }) },
  { key: "workers_10pct", label: "Workers +10%", apply: (b) => ({ workers_per_shift: Math.round(b.workers_per_shift * 1.1) }) },
];

const CHANGE_FIELDS: { key: string; label: string; step?: string }[] = [
  { key: "shifts_per_day", label: "Shifts / day" },
  { key: "workers_per_shift", label: "Workers / shift" },
  { key: "throughput_per_shift", label: "Throughput / shift" },
  { key: "oee_change_pct", label: "OEE change (pp)", step: "0.1" },
  { key: "demand_units_per_day", label: "Demand (units/day)" },
  { key: "demand_change_pct", label: "Demand change (%)", step: "0.1" },
  { key: "defect_change_pct", label: "Defect rate change (pp)", step: "0.1" },
];

const METRIC_LABELS: Record<string, string> = {
  daily_capacity: "Daily Capacity (units)",
  good_units_per_day: "Good Units/Day",
  oee_pct: "OEE",
  demand_units_per_day: "Demand (units/day)",
  defect_rate_pct: "Defect Rate",
  labor_cost_per_day: "Labour Cost/Day",
  scrap_loss_per_day: "Scrap Loss/Day",
  utilisation_pct: "Machine Utilisation",
  daily_revenue: "Daily Revenue",
  daily_margin: "Daily Margin",
};
const CURRENCY_KEYS = new Set(["labor_cost_per_day", "scrap_loss_per_day", "daily_revenue", "daily_margin"]);
const PCT_KEYS = new Set(["oee_pct", "defect_rate_pct", "utilisation_pct"]);
const LOWER_BETTER = new Set(["defect_rate_pct", "labor_cost_per_day", "scrap_loss_per_day"]);

function fmtVal(key: string, v: number) {
  if (v == null || Number.isNaN(v)) return "—";
  if (CURRENCY_KEYS.has(key)) return "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (PCT_KEYS.has(key)) return v.toFixed(1) + "%";
  return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function deltaColor(key: string, direction: string) {
  if (direction === "unchanged") return "text-muted-foreground";
  const positive = LOWER_BETTER.has(key) ? direction === "decrease" : direction === "increase";
  return positive ? "text-emerald-400" : "text-rose-400";
}

interface SimResult {
  scenario_name: string;
  baseline: Record<string, number>;
  projected: Record<string, number>;
  delta: Record<string, { absolute: number; pct: number; direction: string }>;
  insights: string[];
}

interface BaselineDerivation {
  baseline: Partial<Baseline>;
  provenance: { rows: number; period_days: number; oee_pct: number };
  assumptions: string[];
}

export function Simulation() {
  const datasets = useDatasets();
  const [datasetId, setDatasetId] = useState("");
  const [scenarioName, setScenarioName] = useState("What-if Scenario");
  const [baseline, setBaseline] = useState<Baseline>(DEFAULT_BASELINE);
  const [changes, setChanges] = useState<Changes>({});

  const loadBaseline = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/simulation/baseline", { method: "POST", body: JSON.stringify({ dataset_id: datasetId }) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Baseline derivation failed");
      return res.json() as Promise<BaselineDerivation>;
    },
    onSuccess: (data) => setBaseline((b) => ({ ...b, ...data.baseline })),
  });

  const run = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/simulation/run", {
        method: "POST",
        body: JSON.stringify({ scenario_name: scenarioName, baseline, changes }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Simulation failed");
      return res.json() as Promise<SimResult>;
    },
  });

  function setBaselineField(key: keyof Baseline, value: string) {
    const n = parseFloat(value);
    setBaseline((b) => ({ ...b, [key]: Number.isNaN(n) ? b[key] : n }));
  }

  function setChangeField(key: string, value: string) {
    setChanges((c) => {
      if (value.trim() === "") {
        const { [key]: _drop, ...rest } = c;
        return rest;
      }
      const n = parseFloat(value);
      return Number.isNaN(n) ? c : { ...c, [key]: n };
    });
  }

  const hasChanges = Object.keys(changes).length > 0;

  return (
    <div className="space-y-6">
      <PageHeader title="Simulation" description="Model manufacturing capacity, cost, OEE, and throughput under what-if parameter changes." />

      <Card>
        <CardHeader>
          <CardTitle>Baseline</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Load baseline from dataset" className="min-w-[16rem]">
              <Select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
                <option value="">Select a dataset…</option>
                {(datasets.data ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Button variant="secondary" disabled={!datasetId} loading={loadBaseline.isPending} onClick={() => loadBaseline.mutate()}>
              Derive baseline
            </Button>
          </div>
          {loadBaseline.isError && <Alert variant="destructive">{(loadBaseline.error as Error).message}</Alert>}
          {loadBaseline.isSuccess && (
            <Alert>
              Derived from {loadBaseline.data.provenance.rows} rows over {loadBaseline.data.provenance.period_days} day(s), OEE{" "}
              {loadBaseline.data.provenance.oee_pct}%. Assumed: {loadBaseline.data.assumptions.join(", ")}.
            </Alert>
          )}

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {BASELINE_FIELDS.map((f) => (
              <Field key={f.key} label={f.label}>
                <Input type="number" step={f.step ?? "1"} value={baseline[f.key]} onChange={(e) => setBaselineField(f.key, e.target.value)} />
              </Field>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Scenario</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <Field label="Scenario name" className="max-w-sm">
            <Input value={scenarioName} onChange={(e) => setScenarioName(e.target.value)} />
          </Field>

          <div className="space-y-2">
            <div className="text-sm font-medium">Quick presets</div>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((p) => (
                <Button key={p.key} variant="outline" size="sm" onClick={() => setChanges(p.apply(baseline))}>
                  {p.label}
                </Button>
              ))}
              <Button variant="ghost" size="sm" onClick={() => setChanges({})}>
                Clear
              </Button>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {CHANGE_FIELDS.map((f) => (
              <Field key={f.key} label={f.label}>
                <Input
                  type="number"
                  step={f.step ?? "1"}
                  placeholder="No change"
                  value={changes[f.key] ?? ""}
                  onChange={(e) => setChangeField(f.key, e.target.value)}
                />
              </Field>
            ))}
          </div>

          <Button disabled={!hasChanges} loading={run.isPending} onClick={() => run.mutate()}>
            Run Simulation
          </Button>
          {!hasChanges && <p className="text-xs text-muted-foreground">Enter at least one change above, or apply a preset.</p>}
          {run.isError && <Alert variant="destructive">{(run.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {run.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle>{run.data.scenario_name}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Metric</TH>
                    <TH>Baseline</TH>
                    <TH>Projected</TH>
                    <TH>Δ</TH>
                  </TR>
                </THead>
                <TBody>
                  {Object.keys(METRIC_LABELS)
                    .filter((k) => k in run.data.baseline)
                    .map((k) => {
                      const d = run.data.delta[k] ?? { absolute: 0, pct: 0, direction: "unchanged" };
                      const arrow = d.direction === "increase" ? "↑" : d.direction === "decrease" ? "↓" : "→";
                      return (
                        <TR key={k}>
                          <TD className="font-medium">{METRIC_LABELS[k]}</TD>
                          <TD>{fmtVal(k, run.data.baseline[k])}</TD>
                          <TD className="font-semibold">{fmtVal(k, run.data.projected[k])}</TD>
                          <TD className={deltaColor(k, d.direction) + " font-semibold"}>
                            {arrow} {fmtVal(k, d.absolute)} ({d.pct > 0 ? "+" : ""}
                            {d.pct.toFixed(1)}%)
                          </TD>
                        </TR>
                      );
                    })}
                </TBody>
              </Table>
            </TableWrap>

            <div className="space-y-2">
              {run.data.insights.map((insight, i) => (
                <div key={i} className="rounded-r-md border-l-4 border-primary bg-primary/5 px-4 py-2.5 text-sm leading-relaxed">
                  {insight}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
