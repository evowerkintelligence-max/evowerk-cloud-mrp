import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "@/pages/analytics/shared";

interface ForecastPoint {
  date: string;
  actual: number | null;
  predicted: number;
  lower: number;
  upper: number;
}

interface PrescriptiveInsight {
  rank: number;
  label: string;
  detail: string;
  importance_pct: number;
  direction: string;
  icon: string;
}

interface ForecastResult {
  target_col: string;
  horizon: number;
  mape_pct: number;
  r2: number;
  n_training_points: number;
  series: ForecastPoint[];
  prescriptive: PrescriptiveInsight[];
  summary: string;
  machines: string[];
}

export function MfgForecast() {
  const datasets = useDatasets();
  const [datasetId, setDatasetId] = useState("");
  const [dateCol, setDateCol] = useState("");
  const [targetCol, setTargetCol] = useState("");
  const [machineCol, setMachineCol] = useState("");
  const [horizon, setHorizon] = useState("14");
  const [aggregate, setAggregate] = useState("mean");

  const columns = useMemo(
    () => datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [],
    [datasets.data, datasetId],
  );

  const forecast = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${datasetId}/mfg-forecast/run`, {
        method: "POST",
        body: JSON.stringify({
          date_col: dateCol, target_col: targetCol, machine_col: machineCol || undefined,
          horizon: Number(horizon) || 14, aggregate,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Forecast failed");
      return res.json() as Promise<ForecastResult>;
    },
  });

  const ready = Boolean(datasetId && dateCol && targetCol);
  const futurePoints = forecast.data?.series.filter((p) => p.actual === null) ?? [];
  const historyPoints = forecast.data?.series.filter((p) => p.actual !== null) ?? [];

  return (
    <div className="space-y-6">
      <PageHeader title="Manufacturing Forecast" description="Feature-engineered time-series forecast with honest, horizon-widening confidence intervals and prescriptive insights." />

      <Card>
        <CardHeader>
          <CardTitle>Dataset & configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Dataset" className="max-w-md">
            <Select value={datasetId} onChange={(e) => { setDatasetId(e.target.value); setDateCol(""); setTargetCol(""); setMachineCol(""); }}>
              <option value="">Select a dataset…</option>
              {(datasets.data ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.row_count.toLocaleString()} rows)
                </option>
              ))}
            </Select>
          </Field>

          {datasetId && (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <Field label="Date column">
                  <Select value={dateCol} onChange={(e) => setDateCol(e.target.value)}>
                    <option value="">Select column…</option>
                    {columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Target column">
                  <Select value={targetCol} onChange={(e) => setTargetCol(e.target.value)}>
                    <option value="">Select column…</option>
                    {columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Machine column (optional)">
                  <Select value={machineCol} onChange={(e) => setMachineCol(e.target.value)}>
                    <option value="">(none)</option>
                    {columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Horizon (days)">
                  <Input type="number" value={horizon} onChange={(e) => setHorizon(e.target.value)} />
                </Field>
                <Field label="Daily aggregate">
                  <Select value={aggregate} onChange={(e) => setAggregate(e.target.value)}>
                    <option value="mean">Mean</option>
                    <option value="sum">Sum</option>
                  </Select>
                </Field>
              </div>
              <Button disabled={!ready} loading={forecast.isPending} onClick={() => forecast.mutate()}>
                Run forecast
              </Button>
            </>
          )}
          {forecast.isError && <Alert variant="destructive">{(forecast.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {forecast.isSuccess && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Forecast</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Alert>{forecast.data.summary}</Alert>
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="rounded-md border border-border p-3">
                  <div className="text-xs text-muted-foreground">MAPE</div>
                  <div className="text-xl font-semibold">{forecast.data.mape_pct}%</div>
                </div>
                <div className="rounded-md border border-border p-3">
                  <div className="text-xs text-muted-foreground">R²</div>
                  <div className="text-xl font-semibold">{forecast.data.r2}</div>
                </div>
                <div className="rounded-md border border-border p-3">
                  <div className="text-xs text-muted-foreground">Training points</div>
                  <div className="text-xl font-semibold">{forecast.data.n_training_points}</div>
                </div>
              </div>

              <div className="text-sm font-medium">Next {forecast.data.horizon} days</div>
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>Date</TH>
                      <TH>Predicted</TH>
                      <TH>Range</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {futurePoints.map((p) => (
                      <TR key={p.date}>
                        <TD>{p.date}</TD>
                        <TD className="font-medium">{p.predicted.toLocaleString()}</TD>
                        <TD className="text-muted-foreground">
                          {p.lower.toLocaleString()} – {p.upper.toLocaleString()}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
              <div className="text-xs text-muted-foreground">{historyPoints.length} historical days shown in the model, not displayed here.</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Prescriptive insights</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {forecast.data.prescriptive.map((p) => (
                <div key={p.rank} className="rounded-r-md border-l-4 border-primary bg-primary/5 px-4 py-2.5 text-sm leading-relaxed">
                  <span className="mr-2">{p.icon}</span>
                  <span className="font-medium">{p.label}</span> ({p.importance_pct}%) — {p.detail}
                </div>
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
