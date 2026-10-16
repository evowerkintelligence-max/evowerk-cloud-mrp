import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";

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

export function autoMapColumns(fields: EngineField[], cols: string[]): Record<string, string> {
  const next: Record<string, string> = {};
  for (const f of fields) {
    if (f.kind && f.kind !== "col") continue;
    const match = f.hints?.map((h) => cols.find((c) => h.test(c))).find(Boolean);
    if (match) next[f.key] = match;
  }
  return next;
}

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

export { useCurrentUser, type CurrentUser } from "@/lib/currentUser";

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
