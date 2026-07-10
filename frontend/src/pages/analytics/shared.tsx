import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as echarts from "echarts";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

// ─── Chart palette / defaults (copied from the desktop app) ──────────────────

export const C = {
  green: "#34d399",
  blue: "#6c8cff",
  amber: "#f59e0b",
  red: "#f87171",
  cyan: "#22d3ee",
  pink: "#ec4899",
  indigo: "#6366f1",
  violet: "#a78bfa",
};

export const TOOLTIP = {
  trigger: "axis" as const,
  backgroundColor: "#1e293b",
  borderColor: "#334155",
  textStyle: { color: "#e2e8f0", fontSize: 12 },
};

export const AXIS_LABEL = { color: "#64748b", fontSize: 10 };
export const SPLIT_LINE = { lineStyle: { color: "rgba(100,116,139,0.15)" } };
export const AXIS_LINE = { lineStyle: { color: "rgba(100,116,139,0.25)" } };

export function oeeColor(pct: number): string {
  if (pct >= 85) return C.green;
  if (pct >= 65) return C.blue;
  if (pct >= 45) return C.amber;
  return C.red;
}

function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
}

// Single-series line with a gradient fill fading to transparent — the trend-chart look shared by Downtime, Throughput, and Scrap.
export function gradientAreaSeries(name: string, data: (number | null)[], color: string): echarts.LineSeriesOption {
  const rgb = hexToRgb(color);
  return {
    name,
    type: "line",
    smooth: true,
    symbol: "none",
    data,
    lineStyle: { width: 3, color },
    itemStyle: { color },
    areaStyle: {
      color: {
        type: "linear",
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: `rgba(${rgb},0.20)` },
          { offset: 1, color: `rgba(${rgb},0.01)` },
        ],
      },
    },
  };
}

// Pareto: bar + cumulative-% line on a dual axis with an 80% guide line — shared by Downtime and Scrap.
export function paretoOption({
  categories,
  values,
  cumulativePct,
  barName,
  barColor,
  yAxisName,
}: {
  categories: string[];
  values: number[];
  cumulativePct: number[];
  barName: string;
  barColor: string;
  yAxisName: string;
}): echarts.EChartsOption {
  return {
    tooltip: TOOLTIP,
    legend: { textStyle: { color: "#64748b", fontSize: 11 }, top: 4 },
    grid: { top: 40, bottom: 40, left: 56, right: 56 },
    xAxis: {
      type: "category",
      data: categories,
      axisLabel: { ...AXIS_LABEL, rotate: categories.length > 6 ? 30 : 0 },
      axisLine: AXIS_LINE,
    },
    yAxis: [
      { type: "value", name: yAxisName, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
      { type: "value", min: 0, max: 100, axisLabel: { ...AXIS_LABEL, formatter: "{value}%" }, splitLine: { show: false } },
    ],
    series: [
      {
        name: barName,
        type: "bar",
        barMaxWidth: 36,
        data: values,
        itemStyle: { color: barColor, borderRadius: [4, 4, 0, 0], opacity: 0.85 },
      },
      {
        name: "Cumulative %",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        symbol: "circle",
        symbolSize: 6,
        data: cumulativePct,
        lineStyle: { color: C.amber, width: 2 },
        itemStyle: { color: C.amber },
        markLine: {
          silent: true,
          symbol: "none",
          data: [{ yAxis: 80 }],
          lineStyle: { color: C.amber, type: "dashed", opacity: 0.4 },
          label: { position: "insideEndTop", color: C.amber, fontSize: 10, formatter: "80%" },
        },
      },
    ],
  };
}

// Horizontal ranked bar — a category per row, value bar, label on the right —
// shared by OEE's machine breakdown, Throughput's machine breakdown, and Shifts.
export function rankedBarOption({
  categories,
  values,
  colorOf,
  formatter,
  isPct = false,
  barMaxWidth = 22,
  rightMargin = 64,
  markLineAt,
}: {
  categories: string[];
  values: number[];
  colorOf: (value: number, index: number) => string;
  formatter: (value: number, index: number) => string;
  isPct?: boolean;
  barMaxWidth?: number;
  rightMargin?: number;
  markLineAt?: number;
}): echarts.EChartsOption {
  return {
    tooltip: TOOLTIP,
    grid: { left: 88, right: rightMargin, top: 8, bottom: 12 },
    xAxis: {
      type: "value",
      ...(isPct ? { max: 100 } : {}),
      axisLabel: { ...AXIS_LABEL, ...(isPct ? { formatter: "{value}%" } : {}) },
      splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: "category",
      data: categories,
      axisLabel: { color: "#94a3b8", fontSize: 12, fontWeight: 600 },
      axisLine: AXIS_LINE,
      axisTick: { show: false },
    },
    series: [
      {
        type: "bar",
        barMaxWidth,
        data: values.map((v, i) => ({ value: v, itemStyle: { color: colorOf(v, i), borderRadius: [0, 4, 4, 0] } })),
        label: {
          show: true,
          position: "right",
          color: "#94a3b8",
          fontSize: 11,
          fontWeight: 600,
          formatter: (p: { dataIndex?: number }) => formatter(values[p.dataIndex ?? 0], p.dataIndex ?? 0),
        },
        ...(markLineAt !== undefined && {
          markLine: {
            silent: true,
            symbol: "none",
            data: [{ xAxis: markLineAt }],
            lineStyle: { color: C.green, type: "dashed", opacity: 0.4 },
            label: { show: false },
          },
        }),
      },
    ],
  };
}

export const fmt = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : n.toLocaleString("en-US");
export const money = (n: number) => "$" + Math.round(n).toLocaleString("en-US");
export const pct = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${n}%`);
export const fileSize = (b: number) =>
  b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;

// ─── EChart: tiny React wrapper (init + resize + dispose) ────────────────────

export function EChart({
  option,
  height = 300,
  className,
}: {
  option: echarts.EChartsOption;
  height?: number | string;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts>();

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, null, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = undefined;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption({ backgroundColor: "transparent", ...option }, true);
  }, [option]);

  return <div ref={ref} className={className} style={{ height }} />;
}

// ─── Engine screen scaffold ──────────────────────────────────────────────────

export interface Dataset {
  id: string;
  name: string;
  original_filename: string;
  fmt: string;
  row_count: number;
  size_bytes: number;
  created_at: string;
  columns: { name: string; dtype: string }[];
}

export type EngineField = {
  key: string;
  label: string;
  kind?: "col" | "num" | "select";
  required?: boolean;
  options?: string[];
  hints?: RegExp[]; // auto-map dataset columns by naming convention (from desktop)
};

export function useDatasets() {
  return useQuery<Dataset[]>({
    queryKey: ["datasets"],
    queryFn: async () => {
      const res = await apiFetch("/datasets");
      if (!res.ok) throw new Error("Failed to load datasets");
      return res.json();
    },
  });
}

export function EngineScreen<T>({
  title,
  description,
  path,
  fields,
  runLabel,
  children,
}: {
  title: string;
  description: string;
  path: string;
  fields: EngineField[];
  runLabel?: string;
  children: (result: T) => ReactNode;
}) {
  const qc = useQueryClient();
  const [datasetId, setDatasetId] = useState("");
  const [form, setForm] = useState<Record<string, string>>({});
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const datasets = useDatasets();
  const columns = useMemo(
    () => datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [],
    [datasets.data, datasetId],
  );

  function pickDataset(id: string) {
    setDatasetId(id);
    setResult(null);
    // Auto-suggest column mappings by naming convention, like the desktop app.
    const cols = datasets.data?.find((d) => d.id === id)?.columns.map((c) => c.name) ?? [];
    const next: Record<string, string> = {};
    for (const f of fields) {
      if (f.kind && f.kind !== "col") continue;
      const match = f.hints?.map((h) => cols.find((c) => h.test(c))).find(Boolean);
      if (match) next[f.key] = match;
    }
    setForm(next);
  }

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a file first");
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiFetch("/datasets", { method: "POST", body: fd });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Upload failed");
      return res.json() as Promise<Dataset>;
    },
    onSuccess: async (ds) => {
      setError(null);
      await qc.invalidateQueries({ queryKey: ["datasets"] });
      setDatasetId(ds.id);
      const cols = ds.columns.map((c) => c.name);
      const next: Record<string, string> = {};
      for (const f of fields) {
        if (f.kind && f.kind !== "col") continue;
        const match = f.hints?.map((h) => cols.find((c) => h.test(c))).find(Boolean);
        if (match) next[f.key] = match;
      }
      setForm(next);
    },
    onError: (e: Error) => setError(e.message),
  });

  const compute = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = { dataset_id: datasetId };
      for (const f of fields) {
        const v = form[f.key];
        if (f.kind === "num") body[f.key] = v ? Number(v) : null;
        else if (f.kind === "select") body[f.key] = v || f.options?.[0];
        else if (v) body[f.key] = v;
      }
      const res = await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Compute failed");
      return res.json() as Promise<T>;
    },
    onSuccess: (r) => {
      setError(null);
      setResult(r);
    },
    onError: (e: Error) => setError(e.message),
  });

  const ready = Boolean(datasetId) && fields.every((f) => !f.required || form[f.key]);

  return (
    <div className="space-y-8">
      <PageHeader title={title} description={description} />

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Dataset</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Dataset">
            <Select value={datasetId} onChange={(e) => pickDataset(e.target.value)}>
              <option value="">Select a dataset…</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.row_count.toLocaleString()} rows)
                </option>
              ))}
            </Select>
          </Field>
          <Field label="…or upload CSV / Excel">
            <div className="flex gap-2">
              <Input
                type="file"
                accept=".csv,.tsv,.xlsx,.xls,.json,.parquet"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="file:mr-3 file:rounded file:border-0 file:bg-secondary file:px-3 file:py-1 file:text-foreground"
              />
              <Button variant="secondary" loading={upload.isPending} onClick={() => upload.mutate()}>
                Upload
              </Button>
            </div>
          </Field>
        </CardContent>
      </Card>

      {datasetId && (
        <Card>
          <CardHeader>
            <CardTitle>Column mapping</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {fields.map((f) => (
              <Field key={f.key} label={f.label}>
                {f.kind === "num" ? (
                  <Input
                    type="number"
                    step="any"
                    value={form[f.key] ?? ""}
                    onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                  />
                ) : f.kind === "select" ? (
                  <Select value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                    {f.options?.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Select value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                    <option value="">{f.required ? "Select column…" : "(none)"}</option>
                    {columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            ))}
            <div className="flex items-end">
              <Button disabled={!ready} loading={compute.isPending} onClick={() => compute.mutate()}>
                {runLabel ?? "Compute"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {result !== null && children(result)}
    </div>
  );
}
