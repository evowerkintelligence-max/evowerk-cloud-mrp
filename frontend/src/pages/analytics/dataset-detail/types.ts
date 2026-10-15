import type { Dataset } from "../shared";

export interface DatasetDetail extends Dataset {
  preview: Record<string, unknown>[];
}

export interface HealthColumn {
  name: string;
  inferred_dtype: string;
  null_count: number;
  null_pct: number;
  distinct_count: number;
  top_values: { value: string; count: number }[];
  min: number | null;
  max: number | null;
  mean: number | null;
  std: number | null;
  numeric_count: number | null;
  outlier_count: number | null;
  outlier_pct: number | null;
}

export interface Health {
  row_count: number;
  column_count: number;
  duplicate_rows: number;
  duplicate_rows_pct: number;
  total_null_cells: number;
  total_cells: number;
  null_cells_pct: number;
  columns: HealthColumn[];
  warnings: string[];
}

export const TABS = ["overview", "readiness", "schema", "preview", "health", "query", "sql", "clean", "transform", "quality", "process"] as const;
export type Tab = (typeof TABS)[number];

export interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
}
