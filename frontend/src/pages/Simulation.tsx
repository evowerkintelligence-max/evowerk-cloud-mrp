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
import { useDatasets } from "@/pages/analytics/shared";
import { BASELINE_FIELDS, CHANGE_FIELDS, DEFAULT_BASELINE, METRIC_LABELS, PRESETS, deltaColor, fmtVal } from "./simulation/config";
import { SimResultCard } from "./simulation/SimResultCard";
import type {
  Baseline,
  BaselineDerivation,
  Changes,
  CompareResult,
  SavedSimulation,
  SimResult,
  SimulationRun,
} from "./simulation/types";

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

      {result && <SimResultCard result={result} />}

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
