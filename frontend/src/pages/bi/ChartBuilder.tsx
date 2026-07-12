import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { EChart, QueryBuilderFields, useDatasets, useQueryBuilder, type SelectFieldRow } from "../analytics/shared";
import { CHART_TYPES, chartOption, toCategoryValueRows, type ChartType } from "./shared";

interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
}

interface Dashboard {
  id: string;
  name: string;
}

// Mirrors dataset_query.run_query's default-alias computation, so the
// category/value pickers list the same column names the query will actually
// produce (see backend/app/services/dataset_query.py).
function fieldAlias(f: SelectFieldRow): string {
  const defaultAlias = f.agg ? `${f.agg}_${f.column.replace(/\./g, "_")}` : f.column.replace(/\./g, "_");
  return f.alias.trim() || defaultAlias;
}

export function ChartBuilder() {
  const navigate = useNavigate();
  const datasets = useDatasets();
  const [datasetId, setDatasetId] = useState("");
  const names = datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [];
  const qb = useQueryBuilder(datasetId, names);

  const [chartType, setChartType] = useState<ChartType>("bar");
  const [categoryField, setCategoryField] = useState("");
  const [valueField, setValueField] = useState("");
  const [name, setName] = useState("");

  const dashboards = useQuery<Dashboard[]>({
    queryKey: ["bi-dashboards"],
    queryFn: async () => {
      const res = await apiFetch("/bi/dashboards");
      if (!res.ok) throw new Error("Failed to load dashboards");
      return res.json();
    },
  });
  const [dashboardId, setDashboardId] = useState("");

  const aliasOptions = Array.from(new Set(qb.fields.map(fieldAlias)));

  const preview = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/bi/charts/preview", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: datasetId,
          chart_type: chartType,
          query: qb.toRequestBody(),
          category_field: categoryField,
          value_field: valueField,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Preview failed");
      return res.json() as Promise<QueryResult>;
    },
  });

  const save = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/bi/charts", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: datasetId,
          chart_type: chartType,
          query: qb.toRequestBody(),
          category_field: categoryField,
          value_field: valueField,
          name: name.trim() || "Untitled chart",
          dashboard_id: dashboardId || null,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Save failed");
      return res.json() as Promise<{ id: string; dashboard_id: string | null }>;
    },
    onSuccess: (chart) => {
      if (chart.dashboard_id) navigate(`/bi/${chart.dashboard_id}`);
    },
  });

  const ready = Boolean(datasetId) && Boolean(categoryField) && Boolean(valueField);
  const rows = preview.data ? toCategoryValueRows(preview.data.columns, preview.data.rows, categoryField, valueField) : [];

  return (
    <div className="space-y-6">
      <PageHeader title="New chart" description="Pick a dataset and fields, choose a chart type, then preview and save." />

      <Card>
        <CardHeader>
          <CardTitle>Dataset</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Dataset">
            <Select
              value={datasetId}
              onChange={(e) => {
                setDatasetId(e.target.value);
                setCategoryField("");
                setValueField("");
                preview.reset();
              }}
            >
              <option value="">Select a dataset…</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.row_count.toLocaleString()} rows)
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Chart type">
            <Select value={chartType} onChange={(e) => setChartType(e.target.value as ChartType)}>
              {CHART_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </Field>
        </CardContent>
      </Card>

      {datasetId && (
        <Card>
          <CardHeader>
            <CardTitle>Query</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <QueryBuilderFields qb={qb} names={names} showOrderLimit={false} />

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Category field (chart x-axis / labels)">
                <Select value={categoryField} onChange={(e) => setCategoryField(e.target.value)}>
                  <option value="">Select…</option>
                  {aliasOptions.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Value field (chart values)">
                <Select value={valueField} onChange={(e) => setValueField(e.target.value)}>
                  <option value="">Select…</option>
                  {aliasOptions.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <Button disabled={!ready || qb.healthLoading} loading={preview.isPending} onClick={() => preview.mutate()}>
              Preview
            </Button>
            {qb.healthLoading && (
              <p className="text-xs text-muted-foreground">Determining column types before filtering is allowed…</p>
            )}
            {preview.isError && <Alert variant="destructive">{(preview.error as Error).message}</Alert>}
          </CardContent>
        </Card>
      )}

      {preview.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle>Preview</CardTitle>
          </CardHeader>
          <CardContent>
            {rows.length === 0 ? (
              <Alert variant="warning">No rows to chart — check your category/value field selection.</Alert>
            ) : (
              <EChart option={chartOption(chartType, rows)} height={320} />
            )}
          </CardContent>
        </Card>
      )}

      {preview.isSuccess && rows.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Save</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-3">
            <Field label="Chart name" className="min-w-[16rem]">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Qty by SKU" />
            </Field>
            <Field label="Add to dashboard (optional)" className="min-w-[16rem]">
              <Select value={dashboardId} onChange={(e) => setDashboardId(e.target.value)}>
                <option value="">Don't add to a dashboard yet</option>
                {dashboards.data?.map((db) => (
                  <option key={db.id} value={db.id}>
                    {db.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Button loading={save.isPending} onClick={() => save.mutate()}>
              Save chart
            </Button>
            {save.isError && <Alert variant="destructive">{(save.error as Error).message}</Alert>}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
