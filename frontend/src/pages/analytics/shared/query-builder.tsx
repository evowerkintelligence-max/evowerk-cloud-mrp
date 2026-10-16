import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

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
  // the empty column that was current at mount time. Adjusted during render
  // (not in an effect) so the stale selection never paints.
  const [fieldsFor, setFieldsFor] = useState(datasetId);
  if (fieldsFor !== datasetId) {
    setFieldsFor(datasetId);
    setFields([{ column: names[0] ?? "", agg: "", alias: "" }]);
    setGroupBy(new Set());
    setFilters([]);
    setJoinItemsOn("");
  }

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
