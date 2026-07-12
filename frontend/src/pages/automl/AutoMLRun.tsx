import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import type * as echarts from "echarts";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets, ColumnChecklist, EChart, rankedBarOption, gradientAreaSeries, C, TOOLTIP, AXIS_LABEL, SPLIT_LINE, AXIS_LINE } from "../analytics/shared";
import {
  TASKS,
  THOROUGHNESS,
  type AutoMLTaskType,
  type ClassificationDiagnostics,
  type RegressionDiagnostics,
  useAutoMLRun,
  useAlgorithms,
  useCreateAutoMLRun,
  useExplain,
  useExplainRow,
  usePartialDependence,
  useForecastTrajectory,
  usePredict,
  useDriftCheck,
} from "./api";

interface AutoMLSchema {
  numeric_features: string[];
  categorical_features: string[];
  categorical_known_values: Record<string, string[]>;
}

function confusionMatrixOption(labels: string[], matrix: number[][]): echarts.EChartsOption {
  const data: [number, number, number][] = [];
  let max = 0;
  matrix.forEach((row, i) =>
    row.forEach((v, j) => {
      data.push([j, i, v]);
      max = Math.max(max, v);
    }),
  );
  return {
    tooltip: {
      backgroundColor: "#1e293b",
      borderColor: "#334155",
      textStyle: { color: "#e2e8f0", fontSize: 12 },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- echarts' formatter param type
      // (CallbackDataParams) doesn't narrow per series type; heatmap always calls this with
      // `data: [number, number, number]` (see the `data.push([j, i, v])` above).
      formatter: (p: any) => {
        const d = (p.data ?? [0, 0, 0]) as [number, number, number];
        return `actual ${labels[d[1]]} → predicted ${labels[d[0]]}: ${d[2]}`;
      },
    },
    grid: { top: 24, bottom: 64, left: 96, right: 24 },
    xAxis: { type: "category", data: labels, name: "Predicted", nameLocation: "middle", nameGap: 36, axisLabel: AXIS_LABEL },
    yAxis: { type: "category", data: labels, name: "Actual", nameLocation: "middle", nameGap: 64, axisLabel: AXIS_LABEL },
    visualMap: { min: 0, max: max || 1, calculable: false, show: false, inRange: { color: ["#0f172a", C.blue] } },
    series: [{ type: "heatmap", data, label: { show: true, color: "#e2e8f0", fontSize: 10 } }],
  };
}

function regressionScatterOption(points: [number, number][]): echarts.EChartsOption {
  const values = points.flat();
  const lo = Math.min(...values, 0);
  const hi = Math.max(...values, 1);
  return {
    tooltip: { trigger: "item", backgroundColor: "#1e293b", borderColor: "#334155", textStyle: { color: "#e2e8f0", fontSize: 12 } },
    grid: { top: 24, bottom: 40, left: 56, right: 24 },
    xAxis: { type: "value", name: "Actual", nameLocation: "middle", nameGap: 28, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, axisLine: AXIS_LINE },
    yAxis: { type: "value", name: "Predicted", nameLocation: "middle", nameGap: 44, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, axisLine: AXIS_LINE },
    series: [
      { type: "scatter", symbolSize: 6, data: points, itemStyle: { color: C.blue, opacity: 0.6 } },
      {
        type: "line",
        data: [[lo, lo], [hi, hi]],
        symbol: "none",
        lineStyle: { color: C.amber, type: "dashed", width: 1.5 },
        tooltip: { show: false },
      },
    ],
  };
}

function pdOption(grid: (number | string)[], response: number[], isNumeric: boolean): echarts.EChartsOption {
  return {
    tooltip: TOOLTIP,
    grid: { top: 24, bottom: 48, left: 56, right: 24 },
    xAxis: {
      type: isNumeric ? "value" : "category",
      data: isNumeric ? undefined : (grid as string[]),
      axisLabel: { ...AXIS_LABEL, rotate: !isNumeric && grid.length > 6 ? 30 : 0 },
      axisLine: AXIS_LINE,
      splitLine: SPLIT_LINE,
    },
    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [
      isNumeric
        ? { type: "line", smooth: true, symbol: "none", data: grid.map((g, i) => [g, response[i]]), lineStyle: { width: 3, color: C.cyan } }
        : { type: "bar", data: response, itemStyle: { color: C.cyan, borderRadius: [4, 4, 0, 0] } },
    ],
  };
}

function trajectoryOption(
  history: { time: string; actual: number }[],
  points: { time: string; prediction: number; lower?: number; upper?: number }[],
): echarts.EChartsOption {
  const categories = [...history.map((h) => h.time), ...points.map((p) => p.time)];
  const actualSeries: (number | null)[] = [...history.map((h) => h.actual), ...points.map(() => null)];
  const predSeries: (number | null)[] = [...history.map(() => null), ...points.map((p) => p.prediction)];
  const hasBand = points.length > 0 && points.every((p) => p.lower !== undefined && p.upper !== undefined);
  const lowerSeries: (number | null)[] = [...history.map(() => null), ...points.map((p) => p.lower ?? null)];
  const upperSeries: (number | null)[] = [...history.map(() => null), ...points.map((p) => p.upper ?? null)];

  return {
    tooltip: TOOLTIP,
    legend: { textStyle: { color: "#64748b", fontSize: 11 }, top: 4 },
    grid: { top: 40, bottom: 56, left: 56, right: 24 },
    xAxis: { type: "category", data: categories, axisLabel: { ...AXIS_LABEL, rotate: 30 }, axisLine: AXIS_LINE },
    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [
      gradientAreaSeries("Actual", actualSeries, C.blue),
      gradientAreaSeries("Forecast", predSeries, C.cyan),
      ...(hasBand
        ? [
            { name: "Lower (90%)", type: "line" as const, data: lowerSeries, symbol: "none", lineStyle: { color: C.violet, type: "dashed" as const, width: 1 } },
            { name: "Upper (90%)", type: "line" as const, data: upperSeries, symbol: "none", lineStyle: { color: C.violet, type: "dashed" as const, width: 1 } },
          ]
        : []),
    ],
  };
}

function ContributionBars({ contributions }: { contributions: { feature: string; value: string | number | null; contribution: number }[] }) {
  const maxAbs = Math.max(...contributions.map((c) => Math.abs(c.contribution)), 1e-9);
  return (
    <div className="space-y-1.5">
      {contributions.map((c) => {
        const pct = Math.round((Math.abs(c.contribution) / maxAbs) * 100);
        const positive = c.contribution >= 0;
        return (
          <div key={c.feature} className="flex items-center gap-2 text-xs">
            <div className="w-28 flex-shrink-0 truncate text-foreground" title={c.feature}>{c.feature}</div>
            <div className="w-16 flex-shrink-0 truncate text-muted-foreground" title={String(c.value ?? "—")}>= {String(c.value ?? "—")}</div>
            <div className="h-2 flex-1 overflow-hidden rounded bg-secondary/40">
              <div
                className={positive ? "h-full bg-emerald-500" : "h-full bg-rose-500 ml-auto"}
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className={`w-16 flex-shrink-0 text-right ${positive ? "text-emerald-500" : "text-rose-500"}`}>
              {positive ? "+" : ""}{c.contribution.toFixed(4)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function AutoMLRun() {
  const { id } = useParams();
  const isNew = !id || id === "new";
  const navigate = useNavigate();
  const datasets = useDatasets();

  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [task, setTask] = useState<AutoMLTaskType>("classification");
  const [targetColumn, setTargetColumn] = useState("");
  const [selectedFeatures, setSelectedFeatures] = useState<Set<string>>(new Set());
  const [thoroughness, setThoroughness] = useState<string>("balanced");
  const [selectedAlgos, setSelectedAlgos] = useState<Set<string>>(new Set());
  const [handleImbalance, setHandleImbalance] = useState(true);
  const [timeBudgetSeconds, setTimeBudgetSeconds] = useState("");
  const [searchMethod, setSearchMethod] = useState<"random" | "bayesian">("random");
  const [timeColumn, setTimeColumn] = useState("");
  const [horizon, setHorizon] = useState(7);
  const [forecastStrategy, setForecastStrategy] = useState<"direct" | "recursive">("direct");
  const [error, setError] = useState<string | null>(null);

  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [pdFeature, setPdFeature] = useState<string | null>(null);
  const [predictDatasetId, setPredictDatasetId] = useState("");
  const [driftDatasetId, setDriftDatasetId] = useState("");
  const [rowValues, setRowValues] = useState<Record<string, string>>({});

  const run = useAutoMLRun(isNew ? undefined : id);
  const algorithms = useAlgorithms(task);
  const create = useCreateAutoMLRun();

  const columns = useMemo(
    () => datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [],
    [datasets.data, datasetId],
  );
  const featureCandidates = columns.filter((c) => c !== targetColumn && c !== timeColumn);

  function onSubmit() {
    if (!datasetId || !targetColumn) {
      setError("Pick a dataset and a target column.");
      return;
    }
    if (task === "forecasting" && !timeColumn) {
      setError("Forecasting needs a time column.");
      return;
    }
    setError(null);
    create.mutate(
      {
        name: name || `${task} run`,
        dataset_id: datasetId,
        task,
        target_column: targetColumn,
        feature_columns: Array.from(selectedFeatures),
        thoroughness,
        algorithms: selectedAlgos.size ? Array.from(selectedAlgos) : null,
        handle_class_imbalance: handleImbalance,
        time_column: task === "forecasting" ? timeColumn : null,
        horizon,
        forecast_strategy: forecastStrategy,
        time_budget_seconds: timeBudgetSeconds ? Number(timeBudgetSeconds) : null,
        search_method: searchMethod,
      },
      {
        onSuccess: (r) => navigate(`/automl/${r.id}`, { replace: true }),
        onError: (e: Error) => setError(e.message),
      },
    );
  }

  const runData = run.data;
  const isPending = runData?.status === "queued" || runData?.status === "running";
  const isFailed = runData?.status === "failed";
  const isDone = runData?.status === "success";
  const leaderboard = runData?.leaderboard ?? [];
  const selectedEntry = leaderboard.find((l) => l.model_name === selectedModel) ?? leaderboard[0];
  const isRecursiveForecast = runData?.task === "forecasting" && runData.config.forecast_strategy === "recursive";
  const notes = (runData?.meta?.notes as string[] | undefined) ?? [];
  const rowFeatureColumns = (runData?.meta?.feature_columns as string[] | undefined) ?? [];
  const rowSchema = (runData?.meta?.schema as AutoMLSchema | undefined) ?? { numeric_features: [], categorical_features: [], categorical_known_values: {} };
  const supportsRowExplain = runData?.task === "classification" || runData?.task === "regression";

  const explain = useExplain(id, isDone);
  const trajectory = useForecastTrajectory(id, Boolean(isDone && isRecursiveForecast));
  const pd = usePartialDependence(id, isDone ? pdFeature : null);
  const predict = usePredict(id);
  const drift = useDriftCheck(id);
  const explainRow = useExplainRow(id);

  if (!isNew && run.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={isNew ? "New AutoML run" : runData?.name ?? "AutoML run"}
        description="Pick a dataset and a target column, then let AutoML bake off several models."
        actions={isNew ? <Button loading={create.isPending} onClick={onSubmit}>Train</Button> : undefined}
      />

      {error && <Alert variant="destructive">{error}</Alert>}

      {isNew && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Dataset &amp; task</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <Field label="Name">
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Predict late shipments" />
              </Field>
              <Field label="Dataset">
                <Select value={datasetId} onChange={(e) => { setDatasetId(e.target.value); setTargetColumn(""); setTimeColumn(""); setSelectedFeatures(new Set()); }}>
                  <option value="">Select a dataset…</option>
                  {datasets.data?.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name} ({d.row_count.toLocaleString()} rows)
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Task">
                <Select value={task} onChange={(e) => { setTask(e.target.value as AutoMLTaskType); setSelectedAlgos(new Set()); }}>
                  {TASKS.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Target column">
                <Select value={targetColumn} onChange={(e) => setTargetColumn(e.target.value)}>
                  <option value="">Select column…</option>
                  {columns.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </Select>
              </Field>
              {task === "forecasting" && (
                <>
                  <Field label="Time column">
                    <Select value={timeColumn} onChange={(e) => setTimeColumn(e.target.value)}>
                      <option value="">Select column…</option>
                      {columns.filter((c) => c !== targetColumn).map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Horizon (steps ahead)">
                    <Input type="number" min={1} value={horizon} onChange={(e) => setHorizon(Number(e.target.value) || 1)} />
                  </Field>
                  <Field label="Strategy" hint="Recursive strategy also unlocks a multi-step forecast trajectory chart after training.">
                    <Select value={forecastStrategy} onChange={(e) => setForecastStrategy(e.target.value as "direct" | "recursive")}>
                      <option value="direct">direct (single point at horizon)</option>
                      <option value="recursive">recursive (walk-forward trajectory)</option>
                    </Select>
                  </Field>
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Features</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-xs text-muted-foreground">Leave all unchecked to use every remaining column.</p>
              <ColumnChecklist columns={featureCandidates} selected={selectedFeatures} onChange={setSelectedFeatures} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Model selection</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <Field label="Thoroughness" hint="Trades training time for search rigor (more folds, bigger hyperparameter search, more models).">
                <Select value={thoroughness} onChange={(e) => setThoroughness(e.target.value)}>
                  {THOROUGHNESS.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Time budget (seconds)" hint="Optional cap on total hyperparameter search time; remaining candidates are skipped once it's exhausted. Leave blank for no cap.">
                <Input
                  type="number"
                  min={5}
                  value={timeBudgetSeconds}
                  onChange={(e) => setTimeBudgetSeconds(e.target.value)}
                  placeholder="No limit"
                />
              </Field>
              <Field label="Search method" hint="Bayesian (Optuna TPE) converges on good hyperparameters in fewer trials than random search, at a small per-trial overhead.">
                <Select value={searchMethod} onChange={(e) => setSearchMethod(e.target.value as "random" | "bayesian")}>
                  <option value="random">random</option>
                  <option value="bayesian">bayesian (Optuna)</option>
                </Select>
              </Field>
              {task === "classification" && (
                <div className="flex items-end">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={handleImbalance} onChange={(e) => setHandleImbalance(e.target.checked)} />
                    Handle class imbalance (class_weight="balanced")
                  </label>
                </div>
              )}
              <div className="sm:col-span-2 space-y-2">
                <p className="text-xs text-muted-foreground">Leave all unchecked to let the thoroughness tier pick a sensible roster.</p>
                <ColumnChecklist
                  columns={(algorithms.data ?? []).map((a) => a.id)}
                  selected={selectedAlgos}
                  onChange={setSelectedAlgos}
                />
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {!isNew && isPending && (
        <Card className="p-6 text-sm text-muted-foreground">
          Training in progress ({runData?.status})… this page updates automatically.
        </Card>
      )}

      {!isNew && isFailed && <Alert variant="destructive">{runData?.error ?? "Training failed."}</Alert>}

      {!isNew && isDone && runData && (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Card className="p-4">
              <div className="text-xs text-muted-foreground">Best model</div>
              <div className="text-lg font-semibold">{runData.best_model_name}</div>
            </Card>
            <Card className="p-4">
              <div className="text-xs text-muted-foreground">Primary metric</div>
              <div className="text-lg font-semibold">
                {runData.metric_name}={runData.metric_value?.toFixed(4)}
              </div>
            </Card>
            <Card className="p-4">
              <div className="text-xs text-muted-foreground">Task</div>
              <div className="text-lg font-semibold">{runData.task}</div>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Leaderboard</CardTitle>
            </CardHeader>
            <CardContent>
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>#</TH>
                      <TH>Model</TH>
                      <TH>Metric</TH>
                      <TH>Train time</TH>
                      <TH />
                    </TR>
                  </THead>
                  <TBody>
                    {leaderboard.map((row) => (
                      <TR key={row.model_name} className={row.model_name === selectedEntry?.model_name ? "bg-secondary/40" : undefined}>
                        <TD>{row.rank}</TD>
                        <TD className="font-medium">
                          <button className="hover:underline" onClick={() => setSelectedModel(row.model_name)}>
                            {row.label}
                          </button>
                          {row.is_winner && <Badge variant="success" className="ml-2">winner</Badge>}
                        </TD>
                        <TD className="text-muted-foreground">
                          {row.primary_metric}={(row.holdout_scores as Record<string, number>)[row.primary_metric]?.toFixed(4)}
                        </TD>
                        <TD className="text-muted-foreground">{row.train_seconds}s</TD>
                        <TD className="text-right">
                          <Button variant="secondary" size="sm" onClick={() => setSelectedModel(row.model_name)}>
                            Diagnostics
                          </Button>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
            </CardContent>
          </Card>

          {selectedEntry && (
            <Card>
              <CardHeader>
                <CardTitle>{selectedEntry.label} diagnostics</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {selectedEntry.diagnostics.type === "classification" && (
                  <EChart
                    option={confusionMatrixOption(
                      (selectedEntry.diagnostics as ClassificationDiagnostics).labels,
                      (selectedEntry.diagnostics as ClassificationDiagnostics).confusion,
                    )}
                    height={320}
                  />
                )}
                {selectedEntry.diagnostics.type === "regression" && (
                  <EChart option={regressionScatterOption((selectedEntry.diagnostics as RegressionDiagnostics).points)} height={320} />
                )}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Feature importance</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {explain.data ? (
                <>
                  <EChart
                    option={rankedBarOption({
                      categories: explain.data.top_features.filter((f) => f.importance !== null).map((f) => f.feature).reverse(),
                      values: explain.data.top_features.filter((f) => f.importance !== null).map((f) => f.importance as number).reverse(),
                      colorOf: () => C.cyan,
                      formatter: (v) => v.toFixed(4),
                    })}
                    height={Math.max(200, explain.data.top_features.length * 28)}
                  />
                  <p className="text-xs text-muted-foreground">{explain.data.narrative}</p>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">Loading…</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Partial dependence</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Field label="Feature">
                <Select value={pdFeature ?? ""} onChange={(e) => setPdFeature(e.target.value || null)}>
                  <option value="">Select a feature…</option>
                  {runData.feature_columns.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </Select>
              </Field>
              {pd.data && <EChart option={pdOption(pd.data.grid, pd.data.response, pd.data.is_numeric)} height={280} />}
            </CardContent>
          </Card>

          {supportsRowExplain && (
            <Card>
              <CardHeader>
                <CardTitle>Why this prediction</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-3">
                  {rowFeatureColumns.map((f) => {
                    const knownValues = rowSchema.categorical_known_values[f];
                    return (
                      <Field key={f} label={f}>
                        {rowSchema.categorical_features.includes(f) && knownValues?.length ? (
                          <Select value={rowValues[f] ?? ""} onChange={(e) => setRowValues((v) => ({ ...v, [f]: e.target.value }))}>
                            <option value="">Select…</option>
                            {knownValues.map((v) => (
                              <option key={v} value={v}>{v}</option>
                            ))}
                          </Select>
                        ) : (
                          <Input
                            type={rowSchema.numeric_features.includes(f) ? "number" : "text"}
                            value={rowValues[f] ?? ""}
                            onChange={(e) => setRowValues((v) => ({ ...v, [f]: e.target.value }))}
                          />
                        )}
                      </Field>
                    );
                  })}
                </div>
                <Button loading={explainRow.isPending} onClick={() => explainRow.mutate(rowValues)}>
                  Predict &amp; explain
                </Button>
                {explainRow.isError && <Alert variant="destructive">{(explainRow.error as Error).message}</Alert>}
                {explainRow.data && (
                  <div className="space-y-4 pt-2">
                    <div>
                      <div className="text-xs text-muted-foreground">Prediction</div>
                      <div className="text-2xl font-semibold text-cyan-500">{String(explainRow.data.prediction)}</div>
                      {explainRow.data.probabilities && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {Object.entries(explainRow.data.probabilities)
                            .sort((a, b) => b[1] - a[1])
                            .map(([k, v]) => (
                              <Badge key={k} variant="neutral">{k}: {(v * 100).toFixed(1)}%</Badge>
                            ))}
                        </div>
                      )}
                      {explainRow.data.confidence && "lower" in explainRow.data.confidence && (
                        <p className="mt-2 text-xs text-muted-foreground">
                          {Math.round(explainRow.data.confidence.level * 100)}% range: {explainRow.data.confidence.lower.toFixed(4)} – {explainRow.data.confidence.upper.toFixed(4)}
                          {" "}({explainRow.data.confidence.method})
                        </p>
                      )}
                      {explainRow.data.confidence && "confidence_label" in explainRow.data.confidence && (
                        <p className="mt-2 text-xs text-muted-foreground">
                          <span className="font-medium capitalize">{explainRow.data.confidence.confidence_label} confidence</span>
                          {" "}— {(explainRow.data.confidence.margin * 100).toFixed(1)}pt gap between top two classes
                        </p>
                      )}
                    </div>
                    <div>
                      <div className="mb-2 text-xs text-muted-foreground">
                        What pushed this prediction: <span className="text-emerald-500">green</span> pushes it up, <span className="text-rose-500">red</span> pushes it down
                      </div>
                      <ContributionBars contributions={explainRow.data.contributions} />
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {isRecursiveForecast && (
            <Card>
              <CardHeader>
                <CardTitle>Forecast trajectory</CardTitle>
              </CardHeader>
              <CardContent>
                {trajectory.data ? (
                  <EChart option={trajectoryOption(trajectory.data.history, trajectory.data.points)} height={340} />
                ) : (
                  <p className="text-sm text-muted-foreground">Loading…</p>
                )}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Predict</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-end gap-3">
                <Field label="Dataset" className="min-w-[16rem]">
                  <Select value={predictDatasetId} onChange={(e) => setPredictDatasetId(e.target.value)}>
                    <option value="">Select a dataset…</option>
                    {datasets.data?.map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </Select>
                </Field>
                <Button disabled={!predictDatasetId} loading={predict.isPending} onClick={() => predict.mutate(predictDatasetId)}>
                  Run prediction
                </Button>
              </div>
              {predict.data && (
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        {predict.data.columns.map((c) => (
                          <TH key={c}>{c}</TH>
                        ))}
                      </TR>
                    </THead>
                    <TBody>
                      {predict.data.preview.map((row, i) => (
                        <TR key={i}>
                          {predict.data!.columns.map((c) => (
                            <TD key={c}>{String(row[c] ?? "")}</TD>
                          ))}
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Data drift</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-end gap-3">
                <Field label="Compare against dataset" className="min-w-[16rem]">
                  <Select value={driftDatasetId} onChange={(e) => setDriftDatasetId(e.target.value)}>
                    <option value="">Select a dataset…</option>
                    {datasets.data?.map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </Select>
                </Field>
                <Button disabled={!driftDatasetId} loading={drift.isPending} onClick={() => drift.mutate(driftDatasetId)}>
                  Check drift
                </Button>
              </div>
              {drift.data && (
                <>
                  <Badge variant={drift.data.overall_severity === "high" ? "danger" : drift.data.overall_severity === "moderate" ? "warning" : "success"}>
                    overall: {drift.data.overall_severity}
                  </Badge>
                  <TableWrap>
                    <Table>
                      <THead>
                        <TR>
                          <TH>Feature</TH>
                          <TH>PSI</TH>
                          <TH>Severity</TH>
                        </TR>
                      </THead>
                      <TBody>
                        {drift.data.features.map((f) => (
                          <TR key={f.feature}>
                            <TD>{f.feature}</TD>
                            <TD>{f.psi}</TD>
                            <TD>
                              <Badge variant={f.severity === "high" ? "danger" : f.severity === "moderate" ? "warning" : "success"}>{f.severity}</Badge>
                            </TD>
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                  </TableWrap>
                </>
              )}
            </CardContent>
          </Card>

          {notes.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Notes</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                  {notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
