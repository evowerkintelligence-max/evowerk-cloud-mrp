import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { StatCard } from "@/components/StatCard";
import { EChart, controlChart, fmt } from "../shared";
import type { DatasetDetail } from "./types";

interface QualityBatchStat {
  batch_id: string;
  total_records: number;
  defect_rate: number;
}

interface QualityCorrelation {
  feature: string;
  correlation: number;
  direction: string;
  strength: string;
}

interface QualityTrendPoint {
  date: string;
  defect_rate: number;
  in_control?: boolean;
  violations?: number[];
  ucl?: number;
  lcl?: number;
}

interface QualityTrendControl {
  chart_type: "individuals" | "p";
  centerline: number;
  ucl: number;
  lcl: number;
  out_of_control_count: number;
  low_sample_warning: boolean;
  min_baseline: number;
}

interface QualityAnalyzeResponse {
  summary: {
    total_records: number;
    total_defects: number;
    metric_label: string;
    metric_suffix: string;
    defect_metric: number;
    worst_batch: string;
    dpmo: number | null;
    sigma_level: number | null;
    cost_per_defect: number | null;
    total_cost_of_quality: number | null;
  };
  batch_stats: QualityBatchStat[];
  correlations: QualityCorrelation[];
  trend: QualityTrendPoint[];
  trend_control: QualityTrendControl | null;
  warnings?: string[];
}

export function QualityPanel({ d }: { d: DatasetDetail }) {
  const names = d.columns.map((c) => c.name);
  const [defectColumn, setDefectColumn] = useState("");
  const [batchColumn, setBatchColumn] = useState("");
  const [timestampColumn, setTimestampColumn] = useState("");
  const [opportunitiesPerUnit, setOpportunitiesPerUnit] = useState("1");
  const [costPerDefect, setCostPerDefect] = useState("");

  const quality = useMutation({
    mutationFn: async () => {
      const body: Record<string, string | number> = { defect_column: defectColumn };
      if (batchColumn) body.batch_column = batchColumn;
      if (timestampColumn) body.timestamp_column = timestampColumn;
      if (opportunitiesPerUnit) body.opportunities_per_unit = Number(opportunitiesPerUnit);
      if (costPerDefect) body.cost_per_defect = Number(costPerDefect);
      const res = await apiFetch(`/datasets/${d.id}/quality/analyze`, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Quality analysis failed");
      return res.json() as Promise<QualityAnalyzeResponse>;
    },
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Quality analytics</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Defect column (0/1 flag or count, required)">
              <Select value={defectColumn} onChange={(e) => setDefectColumn(e.target.value)}>
                <option value="">Select column…</option>
                {names.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Batch/lot column (optional)">
              <Select value={batchColumn} onChange={(e) => setBatchColumn(e.target.value)}>
                <option value="">(none)</option>
                {names.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Timestamp column (optional, enables trend chart)">
              <Select value={timestampColumn} onChange={(e) => setTimestampColumn(e.target.value)}>
                <option value="">(none)</option>
                {names.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Defect opportunities per unit (for DPMO)">
              <Input
                type="number"
                min="0.1"
                step="0.1"
                value={opportunitiesPerUnit}
                onChange={(e) => setOpportunitiesPerUnit(e.target.value)}
              />
            </Field>
            <Field label="Cost per defect, $ (optional, for Cost of Quality)">
              <Input
                type="number"
                min="0"
                step="0.01"
                placeholder="(none)"
                value={costPerDefect}
                onChange={(e) => setCostPerDefect(e.target.value)}
              />
            </Field>
          </div>
          <Button disabled={!defectColumn} loading={quality.isPending} onClick={() => quality.mutate()}>
            Analyze quality
          </Button>
          {quality.isError && <Alert variant="destructive">{(quality.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {quality.data && (
        <>
          {quality.data.warnings?.map((w) => (
            <Alert key={w} variant="warning">
              {w}
            </Alert>
          ))}
          <Card>
            <CardContent className="pt-6 text-sm">
              <strong>
                {quality.data.summary.defect_metric}
                {quality.data.summary.metric_suffix} {quality.data.summary.metric_label}
              </strong>{" "}
              — {fmt(quality.data.summary.total_defects)} defects across {fmt(quality.data.summary.total_records)}{" "}
              records. Worst batch: {quality.data.summary.worst_batch}
            </CardContent>
          </Card>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="DPMO" value={quality.data.summary.dpmo != null ? fmt(quality.data.summary.dpmo) : "—"} tone="primary" />
            <StatCard
              label="Sigma level"
              value={quality.data.summary.sigma_level != null ? quality.data.summary.sigma_level : "—"}
              hint="incl. 1.5σ shift"
              tone="accent"
            />
            <StatCard
              label="Cost per defect"
              value={quality.data.summary.cost_per_defect != null ? `$${quality.data.summary.cost_per_defect}` : "—"}
              tone="warning"
            />
            <StatCard
              label="Cost of quality"
              value={
                quality.data.summary.total_cost_of_quality != null
                  ? `$${fmt(quality.data.summary.total_cost_of_quality)}`
                  : "—"
              }
              tone="success"
            />
          </div>

          {quality.data.correlations.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Feature correlations with defects</CardTitle>
              </CardHeader>
              <CardContent>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Feature</TH>
                        <TH>Correlation</TH>
                        <TH>Direction</TH>
                        <TH>Strength</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {quality.data.correlations.map((c) => (
                        <TR key={c.feature}>
                          <TD className="font-medium">{c.feature}</TD>
                          <TD>{c.correlation}</TD>
                          <TD>{c.direction}</TD>
                          <TD>{c.strength}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
                <p className="mt-2 text-xs text-muted-foreground">
                  Correlation flags candidates to investigate — it does not prove a feature causes defects.
                </p>
              </CardContent>
            </Card>
          )}

          {quality.data.trend.length >= 2 && quality.data.trend_control && (
            <Card>
              <CardHeader>
                <CardTitle>Defect rate trend (SPC)</CardTitle>
              </CardHeader>
              <CardContent>
                {quality.data.trend_control.low_sample_warning && (
                  <Alert variant="warning">
                    Only {quality.data.trend.length} period(s) — control limits need at least{" "}
                    {quality.data.trend_control.min_baseline} to be statistically reliable. Treat as provisional.
                  </Alert>
                )}
                {quality.data.trend_control.out_of_control_count > 0 && (
                  <Alert variant="destructive">
                    {quality.data.trend_control.out_of_control_count} period(s) outside control limits —
                    special-cause variation, not just normal noise. Hover a red point for which rule it broke.
                  </Alert>
                )}
                <EChart
                  height={280}
                  option={controlChart(
                    quality.data.trend.map((t) => ({
                      label: t.date,
                      value: t.defect_rate,
                      in_control: t.in_control ?? true,
                      violations: t.violations,
                    })),
                    (p) => p.value,
                    quality.data.trend_control.centerline,
                    quality.data.trend_control.ucl,
                    quality.data.trend_control.lcl,
                    "Defect rate",
                  )}
                />
                {quality.data.trend_control.chart_type === "p" && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Proportion (p) chart — each period's in/out-of-control status accounts for that period's own
                    sample size; the UCL/LCL lines shown are an average across periods for display.
                  </p>
                )}
              </CardContent>
            </Card>
          )}

          {quality.data.batch_stats.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Batch breakdown</CardTitle>
              </CardHeader>
              <CardContent>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Batch</TH>
                        <TH>Records</TH>
                        <TH>Defect rate</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {quality.data.batch_stats.map((b) => (
                        <TR key={b.batch_id}>
                          <TD className="font-medium">{b.batch_id}</TD>
                          <TD>{fmt(b.total_records)}</TD>
                          <TD>{b.defect_rate}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
