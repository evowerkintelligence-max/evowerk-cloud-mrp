export const STEP_TYPES = [
  "sql", "filter", "aggregate", "load_dataset",
  "export_result", "import_csv", "import_excel", "import_json",
] as const;
export type StepType = (typeof STEP_TYPES)[number];

export const FILTER_OPS = ["eq", "neq", "contains", "in", "gt", "gte", "lt", "lte"] as const;
export type FilterOp = (typeof FILTER_OPS)[number];

export const AGG_FNS = ["sum", "avg", "min", "max", "count"] as const;
export type AggFn = (typeof AGG_FNS)[number];

export interface FilterRow {
  column: string;
  op: FilterOp;
  value: string; // comma-separated when op === "in"
}

export interface SelectRow {
  column: string;
  agg: AggFn;
  alias: string;
}

export interface StepConfigDraft {
  source: "dataset" | "upstream"; // filter/aggregate only
  datasetId: string; // load_dataset, sql, filter(dataset), aggregate(dataset)
  inputStep: string; // filter(upstream), aggregate(upstream), export_result
  sql: string; // sql
  limit: string; // load_dataset, sql, filter, aggregate - numeric string
  filters: FilterRow[]; // filter
  groupBy: string[]; // aggregate
  select: SelectRow[]; // aggregate
  format: "csv" | "json"; // export_result
  filename: string; // import_*
  contentBase64: string; // import_*
  name: string; // import_* (dataset name override)
}

export function blankConfigDraft(): StepConfigDraft {
  return {
    source: "dataset", datasetId: "", inputStep: "", sql: "", limit: "500",
    filters: [], groupBy: [], select: [], format: "csv", filename: "", contentBase64: "", name: "",
  };
}

function filterRowToQueryFilter(row: FilterRow): { column: string; op: FilterOp; value: string | string[] } {
  if (row.op === "in") {
    return { column: row.column, op: row.op, value: row.value.split(",").map((v) => v.trim()).filter(Boolean) };
  }
  return { column: row.column, op: row.op, value: row.value };
}

function queryFilterToRow(f: { column: string; op: string; value: unknown }): FilterRow {
  const op = (FILTER_OPS as readonly string[]).includes(f.op) ? (f.op as FilterOp) : "eq";
  const value = Array.isArray(f.value) ? f.value.join(", ") : f.value == null ? "" : String(f.value);
  return { column: f.column, op, value };
}

function selectRowToSelectField(row: SelectRow): { column: string; agg: AggFn; alias?: string } {
  return { column: row.column, agg: row.agg, alias: row.alias || undefined };
}

function selectFieldToRow(s: { column: string; agg?: string | null; alias?: string | null }): SelectRow {
  const agg = (AGG_FNS as readonly string[]).includes(s.agg ?? "") ? (s.agg as AggFn) : "sum";
  return { column: s.column, agg, alias: s.alias ?? "" };
}

function numOrUndefined(s: string): number | undefined {
  const n = Number(s);
  return s.trim() !== "" && Number.isFinite(n) ? n : undefined;
}

export function draftToConfig(type: StepType, d: StepConfigDraft): Record<string, unknown> {
  const strip = (o: Record<string, unknown>) =>
    Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));

  switch (type) {
    case "load_dataset":
      return strip({ dataset_id: d.datasetId, limit: numOrUndefined(d.limit) });
    case "sql":
      return strip({ dataset_id: d.datasetId, sql: d.sql, limit: numOrUndefined(d.limit) });
    case "filter": {
      const base = { filters: d.filters.map(filterRowToQueryFilter), limit: numOrUndefined(d.limit) };
      return strip(d.source === "upstream" ? { ...base, input_step: d.inputStep } : { ...base, dataset_id: d.datasetId });
    }
    case "aggregate": {
      const base = {
        group_by: d.groupBy,
        select: d.select.map(selectRowToSelectField),
        limit: numOrUndefined(d.limit),
      };
      return strip(d.source === "upstream" ? { ...base, input_step: d.inputStep } : { ...base, dataset_id: d.datasetId });
    }
    case "import_csv":
    case "import_excel":
    case "import_json":
      return strip({ content_base64: d.contentBase64, filename: d.filename, name: d.name || undefined });
    case "export_result":
      return { input_step: d.inputStep, format: d.format };
    default:
      return {};
  }
}

export function configToDraft(type: StepType, config: Record<string, unknown>): StepConfigDraft {
  const draft = blankConfigDraft();
  const str = (v: unknown) => (v == null ? "" : String(v));

  switch (type) {
    case "load_dataset":
      return { ...draft, datasetId: str(config.dataset_id), limit: str(config.limit ?? "500") };
    case "sql":
      return { ...draft, datasetId: str(config.dataset_id), sql: str(config.sql), limit: str(config.limit ?? "500") };
    case "filter":
    case "aggregate": {
      const source: StepConfigDraft["source"] = config.input_step ? "upstream" : "dataset";
      const filters = Array.isArray(config.filters)
        ? config.filters.map((f) => queryFilterToRow(f as { column: string; op: string; value: unknown }))
        : [];
      const select = Array.isArray(config.select)
        ? config.select.map((s) => selectFieldToRow(s as { column: string; agg?: string | null; alias?: string | null }))
        : [];
      const groupBy = Array.isArray(config.group_by) ? (config.group_by as string[]) : [];
      return {
        ...draft, source, datasetId: str(config.dataset_id), inputStep: str(config.input_step),
        limit: str(config.limit ?? "500"), filters, select, groupBy,
      };
    }
    case "import_csv":
    case "import_excel":
    case "import_json":
      return { ...draft, contentBase64: str(config.content_base64), filename: str(config.filename), name: str(config.name) };
    case "export_result":
      return { ...draft, inputStep: str(config.input_step), format: (config.format as "csv" | "json") ?? "csv" };
    default:
      return draft;
  }
}
