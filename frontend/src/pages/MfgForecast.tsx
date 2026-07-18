import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type * as echarts from "echarts";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import {
  AXIS_LABEL, AXIS_LINE, C, EChart, SPLIT_LINE, TOOLTIP,
  useDatasetHealth, useDatasetPreview, useDatasets,
} from "@/pages/analytics/shared";

interface ForecastPoint {
  date: string;
  actual: number | null;
  predicted: number;
  lower: number;
  upper: number;
}

interface TrackRecordPoint {
  date: string;
  actual: number;
  predicted: number;
  lower: number;
  upper: number;
  made_at: string;
  error_pct: number | null;
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
  gap_count: number;
  outliers_clipped: number;
  track_record: TrackRecordPoint[];
  track_record_mape: number | null;
}

const MIN_ROWS = 30;

function mapeGloss(pct: number): { variant: "success" | "warning" | "danger"; tier: string; label: string } {
  if (pct < 10) return { variant: "success", tier: "Good", label: `Predictions are typically within ${pct}% of actual` };
  if (pct < 25) return { variant: "warning", tier: "Fair", label: `Predictions are typically within ${pct}% of actual` };
  return { variant: "danger", tier: "Weak", label: `Predictions can be off by ${pct}% or more - treat as directional` };
}

function r2Gloss(r2: number): { variant: "success" | "warning" | "danger"; tier: string; label: string } {
  const pct = Math.round(Math.max(0, r2) * 100);
  if (r2 >= 0.7) return { variant: "success", tier: "Good", label: `Explains ${pct}% of the variation in the data` };
  if (r2 >= 0.4) return { variant: "warning", tier: "Fair", label: `Explains ${pct}% of the variation in the data` };
  return { variant: "danger", tier: "Weak", label: "Model finds little signal in this data - forecast is weak" };
}

// Line chart with a shaded confidence band, built with echarts' stacked-area
// trick: an invisible "floor" series at `lower`, then a visible area series
// sized to (upper - lower) stacked on top of it. Takes the ForecastPoint
// shape structurally so the track-record chart (same fields) can reuse it.
function forecastChartOption(
  series: { date: string; actual: number | null; predicted: number; lower: number; upper: number }[],
): echarts.EChartsOption {
  const dates = series.map((p) => p.date);
  const actual = series.map((p) => p.actual);
  const predicted = series.map((p) => p.predicted);
  const lower = series.map((p) => p.lower);
  const spread = series.map((p) => p.upper - p.lower);

  return {
    tooltip: TOOLTIP,
    legend: { data: ["Actual", "Predicted"], textStyle: { color: "#64748b", fontSize: 11 }, top: 4 },
    grid: { top: 40, bottom: 40, left: 56, right: 24 },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: { ...AXIS_LABEL, rotate: dates.length > 20 ? 45 : 0 },
      axisLine: AXIS_LINE,
    },
    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [
      {
        name: "Confidence range",
        type: "line", stack: "band", symbol: "none",
        data: lower, lineStyle: { opacity: 0 },
      },
      {
        name: "Confidence range",
        type: "line", stack: "band", symbol: "none",
        data: spread, lineStyle: { opacity: 0 },
        areaStyle: { color: "rgba(108,140,255,0.15)" },
        tooltip: { show: false },
      },
      {
        name: "Actual", type: "line", symbol: "none",
        data: actual, lineStyle: { width: 2, color: C.blue }, itemStyle: { color: C.blue },
      },
      {
        name: "Predicted", type: "line", symbol: "none",
        data: predicted, lineStyle: { width: 2, color: C.green, type: "dashed" }, itemStyle: { color: C.green },
      },
    ],
  };
}

export function MfgForecast() {
  const datasets = useDatasets();
  const [datasetId, setDatasetId] = useState("");
  const [dateCol, setDateCol] = useState("");
  const [targetCol, setTargetCol] = useState("");
  const [machineCol, setMachineCol] = useState("");
  const [horizon, setHorizon] = useState("14");
  const [aggregate, setAggregate] = useState("mean");

  const dataset = datasets.data?.find((d) => d.id === datasetId);
  const columns = useMemo(() => dataset?.columns.map((c) => c.name) ?? [], [dataset]);
  const health = useDatasetHealth(datasetId);
  const preview = useDatasetPreview(datasetId);

  // Filter dropdowns to plausible columns by inferred dtype so the wrong
  // column can't even be selected - falls back to the full list while health
  // is still loading, or if nothing was confidently inferred as that type.
  const dtypeOf = new Map(health.data?.columns.map((c) => [c.name, c.inferred_dtype]));
  const dateColumns = useMemo(() => {
    const dateLike = columns.filter((c) => dtypeOf.get(c) === "datetime");
    return dateLike.length ? dateLike : columns;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, health.data]);
  const numericColumns = useMemo(() => {
    const numeric = columns.filter((c) => dtypeOf.get(c) === "numeric");
    return numeric.length ? numeric : columns;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, health.data]);

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

  const tooFewRows = Boolean(dataset && dataset.row_count < MIN_ROWS);
  const ready = Boolean(datasetId && dateCol && targetCol && !tooFewRows);
  const futurePoints = forecast.data?.series.filter((p) => p.actual === null) ?? [];
  const mape = forecast.data ? mapeGloss(forecast.data.mape_pct) : null;
  const r2 = forecast.data ? r2Gloss(forecast.data.r2) : null;

  const previewCols = [
    dateCol && { field: "Date column", col: dateCol },
    targetCol && { field: "Target column", col: targetCol },
    machineCol && { field: "Machine column", col: machineCol },
  ].filter((c): c is { field: string; col: string } => Boolean(c));

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

          {tooFewRows && (
            <Alert variant="destructive">
              Need at least {MIN_ROWS} rows to forecast — this dataset has {dataset!.row_count.toLocaleString()}. Pick a larger dataset.
            </Alert>
          )}

          {datasetId && !tooFewRows && (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <Field label="Date column">
                  <Select value={dateCol} onChange={(e) => setDateCol(e.target.value)}>
                    <option value="">Select column…</option>
                    {dateColumns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Target column">
                  <Select value={targetCol} onChange={(e) => setTargetCol(e.target.value)}>
                    <option value="">Select column…</option>
                    {numericColumns.map((c) => (
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

              {previewCols.length > 0 && preview.data && preview.data.preview.length > 0 && (
                <div>
                  <p className="mb-2 text-sm text-muted-foreground">
                    First {Math.min(5, preview.data.preview.length)} rows for the columns you picked — check these look right before running.
                  </p>
                  <TableWrap>
                    <Table>
                      <THead>
                        <TR>
                          {previewCols.map((c) => (
                            <TH key={c.field}>
                              {c.field}
                              <span className="ml-1 font-normal text-muted-foreground">({c.col})</span>
                            </TH>
                          ))}
                        </TR>
                      </THead>
                      <TBody>
                        {preview.data.preview.slice(0, 5).map((row, i) => (
                          <TR key={i}>
                            {previewCols.map((c) => (
                              <TD key={c.field}>{String(row[c.col] ?? "")}</TD>
                            ))}
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                  </TableWrap>
                </div>
              )}

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
              {(forecast.data.gap_count > 0 || forecast.data.outliers_clipped > 0) && (
                <p className="text-xs text-muted-foreground">
                  Data quality: {forecast.data.gap_count > 0 && `${forecast.data.gap_count} missing calendar day(s) filled`}
                  {forecast.data.gap_count > 0 && forecast.data.outliers_clipped > 0 && ", "}
                  {forecast.data.outliers_clipped > 0 && `${forecast.data.outliers_clipped} outlier day(s) clipped`} before training.
                </p>
              )}
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="rounded-md border border-border p-3">
                  <div className="text-xs text-muted-foreground">MAPE</div>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="text-xl font-semibold">{forecast.data.mape_pct}%</span>
                    {mape && <Badge variant={mape.variant}>{mape.tier}</Badge>}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">{mape?.label}</div>
                </div>
                <div className="rounded-md border border-border p-3">
                  <div className="text-xs text-muted-foreground">R²</div>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="text-xl font-semibold">{forecast.data.r2}</span>
                    {r2 && <Badge variant={r2.variant}>{r2.tier}</Badge>}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">{r2?.label}</div>
                </div>
                <div className="rounded-md border border-border p-3">
                  <div className="text-xs text-muted-foreground">Training points</div>
                  <div className="text-xl font-semibold">{forecast.data.n_training_points}</div>
                </div>
              </div>

              <EChart option={forecastChartOption(forecast.data.series)} height={320} />

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
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Track record</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {forecast.data.track_record.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No track record yet — this model's MAPE/R² above are cross-validated on history, not real
                  out-of-sample accuracy. Run this forecast again once new data covers the dates it just predicted,
                  and this section will show how those predictions actually held up.
                </p>
              ) : (
                <>
                  <p className="text-sm text-muted-foreground">
                    How earlier predictions for {forecast.data.target_col} compared to what actually happened, once
                    those dates had real data.
                    {forecast.data.track_record_mape !== null && (
                      <> Real-world accuracy so far: <span className="font-medium text-foreground">{forecast.data.track_record_mape}% average error</span>.</>
                    )}
                  </p>
                  <EChart option={forecastChartOption(forecast.data.track_record)} height={260} />
                  <TableWrap>
                    <Table>
                      <THead>
                        <TR>
                          <TH>Date</TH>
                          <TH>Actual</TH>
                          <TH>Predicted (at the time)</TH>
                          <TH>Error</TH>
                        </TR>
                      </THead>
                      <TBody>
                        {forecast.data.track_record.map((p) => (
                          <TR key={p.date}>
                            <TD>{p.date}</TD>
                            <TD>{p.actual.toLocaleString()}</TD>
                            <TD className="text-muted-foreground">{p.predicted.toLocaleString()}</TD>
                            <TD className={p.error_pct !== null && p.error_pct > 25 ? "text-destructive" : ""}>
                              {p.error_pct !== null ? `${p.error_pct}%` : "—"}
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
