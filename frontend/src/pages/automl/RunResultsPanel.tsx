// Leaderboard / diagnostics / SHAP / predict / drift UI for a successful AutoML
// run - extracted out of AutoMLRun.tsx so the guided-template ResultsView can
// reuse the exact same "Advanced" detail view instead of duplicating it.
import { useState } from "react";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets, EChart, rankedBarOption, C } from "../analytics/shared";
import { cn } from "@/lib/utils";
import {
  type AutoMLRunDetail,
  type ClassificationDiagnostics,
  type RegressionDiagnostics,
  useExplain,
  useExplainRow,
  usePartialDependence,
  useForecastTrajectory,
  usePredict,
  useDriftCheck,
} from "./api";
import { confusionMatrixOption, regressionScatterOption, pdOption, trajectoryOption } from "./chart-options";

interface AutoMLSchema {
  numeric_features: string[];
  categorical_features: string[];
  categorical_known_values: Record<string, string[]>;
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
            <div className={cn("w-16 flex-shrink-0 text-right", positive ? "text-emerald-500" : "text-rose-500")}>
              {positive ? "+" : ""}{c.contribution.toFixed(4)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Renders the full leaderboard/diagnostics/SHAP/predict/drift view for a
 * successful (`status === "success"`) AutoML run. Shared by the "Advanced"
 * manual run page (AutoMLRun.tsx) and the guided-template ResultsView. */
export function RunResultsPanel({ runData }: { runData: AutoMLRunDetail }) {
  const id = runData.id;
  const datasets = useDatasets();
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [pdFeature, setPdFeature] = useState<string | null>(null);
  const [predictDatasetId, setPredictDatasetId] = useState("");
  const [driftDatasetId, setDriftDatasetId] = useState("");
  const [rowValues, setRowValues] = useState<Record<string, string>>({});

  const leaderboard = runData.leaderboard ?? [];
  const selectedEntry = leaderboard.find((l) => l.model_name === selectedModel) ?? leaderboard[0];
  const isRecursiveForecast = runData.task === "forecasting" && runData.config.forecast_strategy === "recursive";
  const notes = (runData.meta?.notes as string[] | undefined) ?? [];
  const rowFeatureColumns = (runData.meta?.feature_columns as string[] | undefined) ?? [];
  const rowSchema = (runData.meta?.schema as AutoMLSchema | undefined) ?? { numeric_features: [], categorical_features: [], categorical_known_values: {} };
  const supportsRowExplain = runData.task === "classification" || runData.task === "regression";

  const explain = useExplain(id, true);
  const trajectory = useForecastTrajectory(id, isRecursiveForecast);
  const pd = usePartialDependence(id, pdFeature);
  const predict = usePredict(id);
  const drift = useDriftCheck(id);
  const explainRow = useExplainRow(id);

  return (
    <div className="space-y-6">
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
    </div>
  );
}
