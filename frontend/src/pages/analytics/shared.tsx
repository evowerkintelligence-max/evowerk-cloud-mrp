import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import * as echarts from "echarts";
import { Maximize2, Minimize2 } from "lucide-react";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";

// ponytail: naive (no "Z"/offset) ISO string under SQLite (aiosqlite drops
// tzinfo on DateTime(timezone=True) columns even though the value is UTC;
// Postgres in prod keeps the offset) - new Date(...) would otherwise parse a
// naive string as local time and throw the elapsed clock off by the
// browser's UTC offset. Force UTC when no offset is present.
export function asUtcMs(iso: string): number {
  return new Date(/Z$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
}

export function useElapsed(startedAt: string | undefined): string {
  const [elapsed, setElapsed] = useState("00:00:00");
  useEffect(() => {
    if (!startedAt) return;
    const started = asUtcMs(startedAt);
    const tick = () => {
      const s = Math.max(0, Math.floor((Date.now() - started) / 1000));
      const h = String(Math.floor(s / 3600)).padStart(2, "0");
      const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
      const sec = String(s % 60).padStart(2, "0");
      setElapsed(`${h}:${m}:${sec}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return elapsed;
}

const TV_REFRESH_MS = 30_000;

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

export interface ReportSectionSpecPayload {
  type:
    | "title" | "exec_summary" | "table" | "bi_chart"
    | "findings" | "risks" | "recommendations" | "caveats" | "text"
    | "engine" | "kind_report" | "rca_case" | "sql";
  title?: string;
  text?: string;
  items?: string[];
  dataset_id?: string;
  // unknown, not Record<string, unknown>: callers pass through whatever
  // useQueryBuilder().toRequestBody() or a Scenario's base_query already
  // produced (inferred object-literal shapes), and this button only ever
  // JSON.stringifies the whole spec - it never reads into `query` itself.
  query?: unknown;
  chart_id?: string;
  engine_path?: string;
  engine_body?: Record<string, unknown>;
  report_kind?: string;
  report_params?: Record<string, unknown>;
  case_id?: string;
  sql_text?: string;
}

export interface InboxItem {
  id: string;
  label: string;
  spec: ReportSectionSpecPayload;
  created_by: string;
  created_at: string;
}

export function useInbox() {
  return useQuery<InboxItem[]>({
    queryKey: ["report-inbox"],
    queryFn: async () => {
      const res = await apiFetch("/reports/inbox");
      if (!res.ok) throw new Error("Failed to load report inbox");
      return res.json();
    },
  });
}

export interface CurrentUser {
  user_id: string;
  role: string;
}

// Shares the ["org-me"] cache with AppShell's org query - same endpoint, no extra fetch.
export function useCurrentUser() {
  return useQuery<CurrentUser>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load current user");
      return res.json();
    },
  });
}

export function SendToReportButton({ spec, label }: { spec: ReportSectionSpecPayload; label: string }) {
  const qc = useQueryClient();
  const send = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/reports/inbox", {
        method: "POST",
        body: JSON.stringify({ label, ...spec }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to send to report");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["report-inbox"] }),
  });

  if (send.isSuccess) {
    return <span className="text-sm text-muted-foreground">Sent to report inbox.</span>;
  }
  return (
    <div className="flex items-center gap-2">
      <Button variant="secondary" size="sm" loading={send.isPending} onClick={() => send.mutate()}>
        Send to report
      </Button>
      {send.isError && <span className="text-sm text-destructive">{(send.error as Error).message}</span>}
    </div>
  );
}

export interface ChartSummary {
  id: string;
  name: string;
}

export function useCharts() {
  return useQuery<ChartSummary[]>({
    queryKey: ["charts"],
    queryFn: async () => {
      const res = await apiFetch("/bi/charts");
      if (!res.ok) throw new Error("Failed to load charts");
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
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const resultsRef = useRef<HTMLDivElement>(null);

  const datasets = useDatasets();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    const fromUrl = searchParams.get("dataset_id");
    if (fromUrl && datasets.data && !datasetId) pickDataset(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, datasets.data]);

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

  const lastComputeBody = useRef<Record<string, unknown> | null>(null);

  const compute = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = { dataset_id: datasetId };
      for (const f of fields) {
        const v = form[f.key];
        if (f.kind === "num") body[f.key] = v ? Number(v) : null;
        else if (f.kind === "select") body[f.key] = v || f.options?.[0];
        else if (v) body[f.key] = v;
      }
      lastComputeBody.current = body;
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

  // TV mode: fullscreen the results container via the native Fullscreen API,
  // optionally polling the last-run compute body so a wall-mounted screen stays live.
  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      if (lastComputeBody.current) compute.mutate();
    }, TV_REFRESH_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh]);

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else resultsRef.current?.requestFullscreen();
  }

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

      {result !== null && (
        <div ref={resultsRef} className={isFullscreen ? "bg-background p-8 overflow-y-auto" : ""}>
          <div className="mb-4 flex items-center justify-end gap-4">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh (30s)
            </label>
            <Button variant="ghost" size="sm" onClick={toggleFullscreen}>
              {isFullscreen ? <Minimize2 /> : <Maximize2 />}
              {isFullscreen ? "Exit TV mode" : "TV mode"}
            </Button>
          </div>
          {children(result)}
          <SendToReportButton
            spec={{ type: "engine", engine_path: path, engine_body: lastComputeBody.current ?? {} }}
            label={`${title} — ${new Date().toLocaleString()}`}
          />
        </div>
      )}
    </div>
  );
}

// ─── Query builder: select/group-by/filter UI shared by the dataset Query tab
// and the BI chart builder (both post a DatasetQueryRequest-shaped body) ──────

export function ColumnChecklist({
  columns,
  selected,
  onChange,
}: {
  columns: string[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  return (
    <div className="grid max-h-64 grid-cols-2 gap-1.5 overflow-y-auto rounded-md border border-border p-3 sm:grid-cols-3">
      {columns.map((c) => (
        <label key={c} className="flex items-center gap-2 truncate text-sm">
          <Checkbox
            checked={selected.has(c)}
            onChange={(e) => {
              const next = new Set(selected);
              if (e.target.checked) next.add(c);
              else next.delete(c);
              onChange(next);
            }}
          />
          <span className="truncate" title={c}>
            {c}
          </span>
        </label>
      ))}
    </div>
  );
}

export const AGG_OPTIONS = ["", "sum", "avg", "min", "max", "count"] as const;
export const FILTER_OPS = ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "in"] as const;
export const ITEM_JOIN_COLUMNS = ["sku", "name", "item_type", "lead_time_days", "on_hand"];
// Matches ITEM_JOIN_COLUMNS' backend types (see _items_relation_sql's col_types) -
// the rest of ITEM_JOIN_COLUMNS is VARCHAR.
export const ITEM_JOIN_NUMERIC_COLUMNS = new Set(["lead_time_days", "on_hand"]);

export interface SelectFieldRow {
  column: string;
  agg: string;
  alias: string;
}

export interface FilterRow {
  column: string;
  op: string;
  value: string;
}

interface HealthColumnDtype {
  name: string;
  inferred_dtype: string;
}

/** Owns all state for a select/group-by/filter/order-by query builder, plus
 * the numeric-column detection (via the dataset health endpoint - CSV/TSV
 * columns are sniffed ALL_VARCHAR, so dtype must come from cell content, not
 * the schema). Shared by the dataset Query tab and the BI chart builder. */
export function useQueryBuilder(datasetId: string, names: string[]) {
  const health = useQuery<{ columns: HealthColumnDtype[] }>({
    queryKey: ["dataset-health", datasetId],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${datasetId}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
    enabled: !!datasetId,
  });
  const dtypeByColumn = new Map(health.data?.columns.map((c) => [c.name, c.inferred_dtype]));

  const [joinItemsOn, setJoinItemsOn] = useState("");
  const allColumns = joinItemsOn ? [...names, ...ITEM_JOIN_COLUMNS.map((c) => `items.${c}`)] : names;

  function isNumericColumn(column: string): boolean {
    if (column.startsWith("items.")) return ITEM_JOIN_NUMERIC_COLUMNS.has(column.slice("items.".length));
    return dtypeByColumn.get(column) === "numeric";
  }

  const [fields, setFields] = useState<SelectFieldRow[]>([{ column: names[0] ?? "", agg: "", alias: "" }]);
  const [groupBy, setGroupBy] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<FilterRow[]>([]);
  const [orderBy, setOrderBy] = useState("");
  const [orderDesc, setOrderDesc] = useState(false);
  const [limit, setLimit] = useState("500");

  // The `fields` useState initializer above only runs once, but a caller like
  // ChartBuilder mounts this hook with datasetId="" (before any dataset is
  // picked) - without this reset, the first field stays permanently stuck on
  // the empty column that was current at mount time.
  useEffect(() => {
    setFields([{ column: names[0] ?? "", agg: "", alias: "" }]);
    setGroupBy(new Set());
    setFilters([]);
    setJoinItemsOn("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId]);

  function updateField(i: number, patch: Partial<SelectFieldRow>) {
    setFields((prev) => prev.map((f, idx) => (idx === i ? { ...f, ...patch } : f)));
  }
  function updateFilter(i: number, patch: Partial<FilterRow>) {
    setFilters((prev) => prev.map((f, idx) => (idx === i ? { ...f, ...patch } : f)));
  }

  function toRequestBody() {
    return {
      select: fields.map((f) => ({
        column: f.column,
        agg: f.agg || undefined,
        alias: f.alias.trim() || undefined,
      })),
      group_by: Array.from(groupBy),
      filters: filters.map((f) => {
        const value =
          f.op === "in"
            ? f.value.split(",").map((v) => v.trim()).filter(Boolean)
            : f.op !== "contains" && f.value !== "" && isNumericColumn(f.column)
              ? Number(f.value)
              : f.value;
        return { column: f.column, op: f.op, value };
      }),
      join_items_on: joinItemsOn || undefined,
      order_by: orderBy || undefined,
      order_desc: orderDesc,
      limit: Number(limit) || 500,
    };
  }

  return {
    joinItemsOn, setJoinItemsOn,
    allColumns, isNumericColumn, healthLoading: health.isLoading,
    fields, setFields, updateField,
    groupBy, setGroupBy,
    filters, setFilters, updateFilter,
    orderBy, setOrderBy,
    orderDesc, setOrderDesc,
    limit, setLimit,
    toRequestBody,
  };
}

export type QueryBuilder = ReturnType<typeof useQueryBuilder>;

/** Presentational UI for a QueryBuilder - join picker, select fields, group by,
 * filters, order by/limit. `showOrderLimit=false` hides order-by/limit for
 * callers (like the BI chart builder) that don't need row-level ordering. */
export function QueryBuilderFields({
  qb,
  names,
  showOrderLimit = true,
}: {
  qb: QueryBuilder;
  names: string[];
  showOrderLimit?: boolean;
}) {
  return (
    <>
      <Field label="Join Items by column (optional)">
        <Select value={qb.joinItemsOn} onChange={(e) => qb.setJoinItemsOn(e.target.value)}>
          <option value="">No join</option>
          {names.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      </Field>

      <div className="space-y-2">
        <div className="text-sm font-medium">Select fields</div>
        {qb.fields.map((f, i) => (
          <div key={i} className="flex flex-wrap items-end gap-2">
            <Field label="Column" className="min-w-[10rem]">
              <Select value={f.column} onChange={(e) => qb.updateField(i, { column: e.target.value })}>
                {qb.allColumns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Aggregate" className="min-w-[8rem]">
              <Select value={f.agg} onChange={(e) => qb.updateField(i, { agg: e.target.value })}>
                {AGG_OPTIONS.map((a) => (
                  <option key={a} value={a}>
                    {a || "none"}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Alias" className="min-w-[8rem]">
              <Input value={f.alias} onChange={(e) => qb.updateField(i, { alias: e.target.value })} placeholder="optional" />
            </Field>
            <Button
              variant="ghost"
              size="sm"
              disabled={qb.fields.length === 1}
              onClick={() => qb.setFields((prev) => prev.filter((_, idx) => idx !== i))}
            >
              Remove
            </Button>
          </div>
        ))}
        <Button
          variant="secondary"
          size="sm"
          onClick={() => qb.setFields((prev) => [...prev, { column: qb.allColumns[0] ?? "", agg: "", alias: "" }])}
        >
          + Add field
        </Button>
      </div>

      <div className="space-y-2">
        <div className="text-sm font-medium">Group by (optional)</div>
        <ColumnChecklist columns={qb.allColumns} selected={qb.groupBy} onChange={qb.setGroupBy} />
      </div>

      <div className="space-y-2">
        <div className="text-sm font-medium">Filters</div>
        {qb.filters.map((f, i) => (
          <div key={i} className="flex flex-wrap items-end gap-2">
            <Field label="Column" className="min-w-[10rem]">
              <Select value={f.column} onChange={(e) => qb.updateFilter(i, { column: e.target.value })}>
                {qb.allColumns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Op" className="min-w-[8rem]">
              <Select value={f.op} onChange={(e) => qb.updateFilter(i, { op: e.target.value })}>
                {FILTER_OPS.map((op) => (
                  <option key={op} value={op}>
                    {op}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Value" className="min-w-[10rem]">
              <Input
                value={f.value}
                onChange={(e) => qb.updateFilter(i, { value: e.target.value })}
                placeholder={f.op === "in" ? "comma,separated,values" : ""}
              />
            </Field>
            <Button variant="ghost" size="sm" onClick={() => qb.setFilters((prev) => prev.filter((_, idx) => idx !== i))}>
              Remove
            </Button>
          </div>
        ))}
        <Button
          variant="secondary"
          size="sm"
          onClick={() => qb.setFilters((prev) => [...prev, { column: qb.allColumns[0] ?? "", op: "eq", value: "" }])}
        >
          + Add filter
        </Button>
      </div>

      {showOrderLimit && (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Order by" className="min-w-[10rem]">
            <Select value={qb.orderBy} onChange={(e) => qb.setOrderBy(e.target.value)}>
              <option value="">None</option>
              {qb.allColumns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </Field>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <Checkbox checked={qb.orderDesc} onChange={(e) => qb.setOrderDesc(e.target.checked)} />
            Descending
          </label>
          <Field label="Limit" className="w-28">
            <Input type="number" value={qb.limit} onChange={(e) => qb.setLimit(e.target.value)} />
          </Field>
        </div>
      )}
    </>
  );
}
