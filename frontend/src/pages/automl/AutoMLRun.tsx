import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import type * as echarts from "echarts";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { useDatasets, ColumnChecklist } from "../analytics/shared";
import { RunResultsPanel } from "./RunResultsPanel";
import { TASKS, THOROUGHNESS, type AutoMLTaskType, useAutoMLRun, useAlgorithms, useCreateAutoMLRun } from "./api";

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
  const [autoRetrain, setAutoRetrain] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        auto_retrain: autoRetrain,
      },
      {
        onSuccess: (r) => navigate(`/automl/advanced/${r.id}`, { replace: true }),
        onError: (e: Error) => setError(e.message),
      },
    );
  }

  const runData = run.data;
  const isPending = runData?.status === "queued" || runData?.status === "running";
  const isFailed = runData?.status === "failed";
  const isDone = runData?.status === "success";

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
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-sm" title="When this dataset's data changes and drift is detected, automatically retrain and promote if the new model isn't worse.">
                  <input type="checkbox" checked={autoRetrain} onChange={(e) => setAutoRetrain(e.target.checked)} />
                  Auto-retrain on drift
                </label>
              </div>
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

      {!isNew && runData?.retrained_from_run_id && (
        <p className="text-xs text-muted-foreground">
          Auto-retrained from{" "}
          <Link to={`/automl/${runData.retrained_from_run_id}`} className="underline underline-offset-2">
            an earlier run
          </Link>
          .
        </p>
      )}

      {!isNew && isDone && runData && <RunResultsPanel runData={runData} />}
    </div>
  );
}
