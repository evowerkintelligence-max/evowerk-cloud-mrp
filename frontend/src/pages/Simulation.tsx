import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { SendToReportButton, useDatasets } from "@/pages/analytics/shared";

interface Baseline {
  shifts_per_day: number;
  shift_hours: number;
  workers_per_shift: number;
  avg_wage_per_hour: number;
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
  { key: "throughput_per_shift", label: "Throughput / shift (units)" },
  { key: "oee_pct", label: "OEE (%)", step: "0.1" },
  { key: "demand_units_per_day", label: "Demand (units/day)" },
  { key: "defect_rate_pct", label: "Defect rate (%)", step: "0.1" },
  { key: "scrap_cost_per_unit", label: "Scrap cost / unit ($)", step: "0.01" },
  { key: "raw_material_cost_per_unit", label: "Raw material cost / unit ($)", step: "0.01" },
  { key: "selling_price_per_unit", label: "Selling price / unit ($)", step: "0.01" },
];

const BASELINE_FIELD_LABELS: Record<string, string> = Object.fromEntries(BASELINE_FIELDS.map((f) => [f.key, f.label]));

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

function resultMetricKeys(result: SimResult): string[] {
  return Object.keys(METRIC_LABELS).filter((k) => k in result.baseline);
}

function resultToReportText(result: SimResult): string {
  const lines = resultMetricKeys(result).map(
    (k) => `${METRIC_LABELS[k]}: ${fmtVal(k, result.baseline[k])} → ${fmtVal(k, result.projected[k])}`,
  );
  return [...lines, "", ...result.insights].join("\n");
}

interface Band {
  p10: number;
  p50: number;
  p90: number;
}

interface SensitivityEntry {
  parameter: string;
  baseline_value: number;
  low_value: number;
  high_value: number;
  impact: number;
}

interface SimResult {
  scenario_name: string;
  baseline: Record<string, number>;
  projected: Record<string, number>;
  delta: Record<string, { absolute: number; pct: number; direction: string }>;
  insights: string[];
  bands?: Record<string, Band>;
  sensitivity?: { target_metric: string; ranked: SensitivityEntry[] };
  variance_source?: "dataset" | "default";
}

interface BaselineDerivation {
  baseline: Partial<Baseline>;
  provenance: { rows: number; period_days: number; oee_pct: number };
  assumptions: string[];
}

interface SavedSimulation {
  id: string;
  dataset_id: string | null;
  name: string;
  description: string;
  baseline: Partial<Baseline>;
  changes: Changes;
  created_at: string;
  updated_at: string;
}

interface SimulationRun {
  id: string;
  changes: Changes;
  result: SimResult;
  created_at: string;
}

interface CompareResult {
  run_a_id: string;
  run_b_id: string;
  delta: Record<string, { absolute: number; pct: number; direction: string }>;
}

async function j<T>(resPromise: Promise<Response>): Promise<T> {
  const res = await resPromise;
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Request failed");
  return res.json();
}

export function Simulation() {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const [datasetId, setDatasetId] = useState("");
  const [scenarioName, setScenarioName] = useState("What-if Scenario");
  const [baseline, setBaseline] = useState<Baseline>(DEFAULT_BASELINE);
  const [changes, setChanges] = useState<Changes>({});
  const [simulationId, setSimulationId] = useState<string | null>(null);
  const [expandedSimId, setExpandedSimId] = useState<string | null>(null);
  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");
  const [result, setResult] = useState<SimResult | null>(null);
  const [targetMetric, setTargetMetric] = useState("daily_margin");
  const [downloadingKey, setDownloadingKey] = useState<string | null>(null);

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
    onSuccess: (data) => setResult(data),
  });

  const simulations = useQuery<SavedSimulation[]>({
    queryKey: ["simulations"],
    queryFn: () => j(apiFetch("/simulation")),
  });

  const runs = useQuery<SimulationRun[]>({
    queryKey: ["simulation-runs", expandedSimId],
    queryFn: () => j(apiFetch(`/simulation/${expandedSimId}/runs`)),
    enabled: !!expandedSimId,
  });

  const save = useMutation({
    mutationFn: () =>
      j<SavedSimulation>(
        apiFetch("/simulation", {
          method: "POST",
          body: JSON.stringify({ name: scenarioName, description: "", dataset_id: datasetId || undefined, baseline, changes }),
        }),
      ),
    onSuccess: (data) => {
      setSimulationId(data.id);
      qc.invalidateQueries({ queryKey: ["simulations"] });
    },
  });

  const runAndSave = useMutation({
    mutationFn: async () => {
      await j(
        apiFetch(`/simulation/${simulationId}`, {
          method: "PUT",
          body: JSON.stringify({ name: scenarioName, baseline, changes }),
        }),
      );
      return j<SimulationRun>(
        apiFetch(`/simulation/${simulationId}/run`, { method: "POST", body: JSON.stringify({ changes: {} }) }),
      );
    },
    onSuccess: (data) => {
      setResult(data.result);
      qc.invalidateQueries({ queryKey: ["simulation-runs", simulationId] });
      qc.invalidateQueries({ queryKey: ["simulations"] });
    },
  });

  const runMonteCarlo = useMutation({
    mutationFn: async () => {
      await j(
        apiFetch(`/simulation/${simulationId}`, {
          method: "PUT",
          body: JSON.stringify({ name: scenarioName, baseline, changes }),
        }),
      );
      return j<SimulationRun>(
        apiFetch(`/simulation/${simulationId}/run/monte-carlo`, {
          method: "POST",
          body: JSON.stringify({ changes: {}, target_metric: targetMetric }),
        }),
      );
    },
    onSuccess: (data) => {
      setResult(data.result);
      qc.invalidateQueries({ queryKey: ["simulation-runs", simulationId] });
      qc.invalidateQueries({ queryKey: ["simulations"] });
    },
  });

  const del = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/simulation/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Delete failed");
    },
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["simulations"] });
      if (id === simulationId) setSimulationId(null);
      if (id === expandedSimId) setExpandedSimId(null);
    },
  });

  const compare = useMutation({
    mutationFn: () =>
      j<CompareResult>(
        apiFetch("/simulation/compare", {
          method: "POST",
          body: JSON.stringify({ run_a_id: compareA, run_b_id: compareB }),
        }),
      ),
  });

  function loadSimulation(sim: SavedSimulation) {
    setSimulationId(sim.id);
    setScenarioName(sim.name);
    setDatasetId(sim.dataset_id ?? "");
    setBaseline((b) => ({ ...b, ...sim.baseline }));
    setChanges(sim.changes ?? {});
    setResult(null);
  }

  async function downloadRun(simId: string, runId: string, fmt: "csv" | "xlsx") {
    const key = `${runId}:${fmt}`;
    setDownloadingKey(key);
    try {
      const res = await apiFetch(`/simulation/${simId}/runs/${runId}/export?fmt=${fmt}`);
      if (!res.ok) throw new Error("Export failed");
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `simulation-run.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloadingKey((k) => (k === key ? null : k));
    }
  }

  function toggleExpandedSim(id: string) {
    setExpandedSimId((cur) => (cur === id ? null : id));
    setCompareA("");
    setCompareB("");
  }

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

          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={!hasChanges} loading={run.isPending} onClick={() => run.mutate()}>
              Run Simulation
            </Button>
            <Button variant="secondary" disabled={!scenarioName} loading={save.isPending} onClick={() => save.mutate()}>
              {simulationId ? "Save as new" : "Save simulation"}
            </Button>
            {simulationId && (
              <>
                <Button variant="secondary" loading={runAndSave.isPending} onClick={() => runAndSave.mutate()}>
                  Run &amp; save to history
                </Button>
                <Select value={targetMetric} onChange={(e) => setTargetMetric(e.target.value)} className="w-56">
                  {Object.entries(METRIC_LABELS).map(([k, label]) => (
                    <option key={k} value={k}>
                      {label}
                    </option>
                  ))}
                </Select>
                <Button variant="secondary" loading={runMonteCarlo.isPending} onClick={() => runMonteCarlo.mutate()}>
                  Run with confidence range
                </Button>
              </>
            )}
          </div>
          {!hasChanges && <p className="text-xs text-muted-foreground">Enter at least one change above, or apply a preset.</p>}
          {run.isError && <Alert variant="destructive">{(run.error as Error).message}</Alert>}
          {save.isError && <Alert variant="destructive">{(save.error as Error).message}</Alert>}
          {runAndSave.isError && <Alert variant="destructive">{(runAndSave.error as Error).message}</Alert>}
          {runMonteCarlo.isError && <Alert variant="destructive">{(runMonteCarlo.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {result && (
        <Card>
          <CardHeader>
            <CardTitle>{result.scenario_name}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <SendToReportButton spec={{ type: "text", text: resultToReportText(result) }} label={result.scenario_name} />
            {result.variance_source && (
              <div className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs text-muted-foreground">
                {result.variance_source === "dataset"
                  ? "Confidence range: derived from your data"
                  : "Confidence range: default estimate (no linked dataset)"}
              </div>
            )}
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Metric</TH>
                    <TH>Baseline</TH>
                    <TH>Projected</TH>
                    {result.bands && <TH>Range (P10–P90)</TH>}
                    <TH>Δ</TH>
                  </TR>
                </THead>
                <TBody>
                  {resultMetricKeys(result)
                    .map((k) => {
                      const d = result.delta[k] ?? { absolute: 0, pct: 0, direction: "unchanged" };
                      const arrow = d.direction === "increase" ? "↑" : d.direction === "decrease" ? "↓" : "→";
                      return (
                        <TR key={k}>
                          <TD className="font-medium">{METRIC_LABELS[k]}</TD>
                          <TD>{fmtVal(k, result.baseline[k])}</TD>
                          <TD className="font-semibold">{fmtVal(k, result.projected[k])}</TD>
                          {result.bands && (
                            <TD className="text-muted-foreground">
                              {result.bands[k] ? `${fmtVal(k, result.bands[k].p10)} – ${fmtVal(k, result.bands[k].p90)}` : "—"}
                            </TD>
                          )}
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
              {result.insights.map((insight, i) => (
                <div key={i} className="rounded-r-md border-l-4 border-primary bg-primary/5 px-4 py-2.5 text-sm leading-relaxed">
                  {insight}
                </div>
              ))}
            </div>

            {result.sensitivity && result.sensitivity.ranked.length > 0 && (
              <div className="space-y-2">
                <div className="text-sm font-medium">
                  What moves {METRIC_LABELS[result.sensitivity.target_metric] ?? result.sensitivity.target_metric} the most
                </div>
                {(() => {
                  const ranked = result.sensitivity!.ranked;
                  const maxImpact = Math.max(...ranked.map((r) => r.impact), 1);
                  return ranked.map((r) => (
                    <div key={r.parameter} className="flex items-center gap-3 text-sm">
                      <span className="w-40 shrink-0 text-muted-foreground">{BASELINE_FIELD_LABELS[r.parameter] ?? r.parameter}</span>
                      <div className="h-3 flex-1 rounded-full bg-muted">
                        <div className="h-3 rounded-full bg-primary" style={{ width: `${(r.impact / maxImpact) * 100}%` }} />
                      </div>
                      <span className="w-24 shrink-0 text-right text-xs text-muted-foreground">
                        {fmtVal(result.sensitivity!.target_metric, r.impact)}
                      </span>
                    </div>
                  ));
                })()}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Saved Simulations</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Description</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {(simulations.data ?? []).map((s) => (
                  <Fragment key={s.id}>
                    <TR>
                      <TD className="font-medium">{s.name}</TD>
                      <TD className="text-muted-foreground">{s.description || "—"}</TD>
                      <TD className="flex gap-2">
                        <Button size="sm" variant="secondary" onClick={() => loadSimulation(s)}>
                          Load
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => toggleExpandedSim(s.id)}>
                          {expandedSimId === s.id ? "Hide runs" : "View runs"}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => del.mutate(s.id)}>
                          Delete
                        </Button>
                      </TD>
                    </TR>
                    {expandedSimId === s.id && (
                      <TR>
                        <TD colSpan={3}>
                          {runs.isLoading && <p className="text-sm text-muted-foreground">Loading runs…</p>}
                          {runs.data?.length === 0 && <p className="text-sm text-muted-foreground">No runs yet.</p>}
                          {(runs.data ?? []).map((r) => (
                            <div key={r.id} className="flex items-center justify-between border-b border-border py-2 text-sm last:border-0">
                              <span>
                                {new Date(r.created_at).toLocaleString()} — {Object.keys(r.changes).length} change(s)
                              </span>
                              <div className="flex items-center gap-3">
                                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                                  <input type="radio" name="compareA" checked={compareA === r.id} onChange={() => setCompareA(r.id)} /> A
                                </label>
                                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                                  <input type="radio" name="compareB" checked={compareB === r.id} onChange={() => setCompareB(r.id)} /> B
                                </label>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  loading={downloadingKey === `${r.id}:csv`}
                                  onClick={() => downloadRun(s.id, r.id, "csv")}
                                >
                                  CSV
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  loading={downloadingKey === `${r.id}:xlsx`}
                                  onClick={() => downloadRun(s.id, r.id, "xlsx")}
                                >
                                  XLSX
                                </Button>
                              </div>
                            </div>
                          ))}
                        </TD>
                      </TR>
                    )}
                  </Fragment>
                ))}
                {simulations.data?.length === 0 && (
                  <TR>
                    <TD colSpan={3} className="text-center text-muted-foreground">
                      No saved simulations yet.
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          </TableWrap>
          {del.isError && <Alert variant="destructive">{(del.error as Error).message}</Alert>}

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              disabled={!compareA || !compareB || compareA === compareB}
              loading={compare.isPending}
              onClick={() => compare.mutate()}
            >
              Compare selected runs
            </Button>
            {compareA && compareB && compareA === compareB && (
              <span className="text-xs text-muted-foreground">Pick two different runs.</span>
            )}
          </div>
          {compare.isError && <Alert variant="destructive">{(compare.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {compare.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle>Run comparison</CardTitle>
          </CardHeader>
          <CardContent>
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Metric</TH>
                    <TH>Δ (B → A)</TH>
                  </TR>
                </THead>
                <TBody>
                  {Object.keys(METRIC_LABELS)
                    .filter((k) => k in compare.data.delta)
                    .map((k) => {
                      const d = compare.data.delta[k];
                      const arrow = d.direction === "increase" ? "↑" : d.direction === "decrease" ? "↓" : "→";
                      return (
                        <TR key={k}>
                          <TD className="font-medium">{METRIC_LABELS[k]}</TD>
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
          </CardContent>
        </Card>
      )}
    </div>
  );
}
