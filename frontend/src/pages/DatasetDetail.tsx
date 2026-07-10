import { FormEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface DatasetColumn {
  name: string;
  dtype: string;
}

interface DatasetDetailResponse {
  id: string;
  name: string;
  row_count: number;
  columns: DatasetColumn[];
  preview: Record<string, unknown>[];
}

interface HealthColumn {
  name: string;
  inferred_dtype: string;
  null_pct: number;
  distinct_count: number;
}

interface DatasetHealthResponse {
  row_count: number;
  column_count: number;
  duplicate_rows_pct: number;
  null_cells_pct: number;
  columns: HealthColumn[];
  warnings: string[];
}

type Agg = "" | "sum" | "avg" | "min" | "max" | "count";
type FilterOp = "eq" | "neq" | "gt" | "gte" | "lt" | "lte" | "contains" | "in";

interface FilterRow {
  column: string;
  op: FilterOp;
  value: string;
}

interface QueryResponse {
  columns: string[];
  rows: Record<string, unknown>[];
}

// Fixed set of Item fields joinable via join_items_on - matches
// dataset_query.ITEM_JOIN_COLUMNS on the backend.
const ITEM_COLUMNS = ["name", "item_type", "lead_time_days", "on_hand"];

export function DatasetDetail() {
  const { id } = useParams<{ id: string }>();
  const [aggByColumn, setAggByColumn] = useState<Record<string, Agg>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [groupBy, setGroupBy] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<FilterRow[]>([]);
  const [joinItemsOn, setJoinItemsOn] = useState("");
  const [error, setError] = useState<string | null>(null);

  const detail = useQuery<DatasetDetailResponse>({
    queryKey: ["dataset", id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${id}`);
      if (!res.ok) throw new Error("Failed to load dataset");
      return res.json();
    },
    enabled: !!id,
  });

  const health = useQuery<DatasetHealthResponse>({
    queryKey: ["dataset", id, "health"],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${id}/health`);
      if (!res.ok) throw new Error("Failed to load health");
      return res.json();
    },
    enabled: !!id,
  });

  const query = useMutation({
    mutationFn: async () => {
      const select = Array.from(selected).map((column) => {
        const agg = aggByColumn[column];
        return agg ? { column, agg } : { column };
      });
      if (select.length === 0) throw new Error("Pick at least one column to select");
      const body = {
        select,
        group_by: Array.from(groupBy),
        filters: filters.filter((f) => f.column && f.value !== ""),
        join_items_on: joinItemsOn || null,
      };
      const res = await apiFetch(`/datasets/${id}/query`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail ?? "Query failed");
      }
      return (await res.json()) as QueryResponse;
    },
    onError: (e: Error) => setError(e.message),
  });

  if (!detail.data) return <p>Loading...</p>;

  const columns = detail.data.columns.map((c) => c.name);

  function toggleSelected(column: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(column)) next.delete(column);
      else next.add(column);
      return next;
    });
  }

  function toggleGroupBy(column: string) {
    setGroupBy((prev) => {
      const next = new Set(prev);
      if (next.has(column)) next.delete(column);
      else next.add(column);
      return next;
    });
  }

  function addFilter() {
    setFilters((prev) => [...prev, { column: columns[0] ?? "", op: "eq", value: "" }]);
  }

  function updateFilter(index: number, patch: Partial<FilterRow>) {
    setFilters((prev) => prev.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  }

  function removeFilter(index: number) {
    setFilters((prev) => prev.filter((_, i) => i !== index));
  }

  function runQuery(e: FormEvent) {
    e.preventDefault();
    setError(null);
    query.mutate();
  }

  return (
    <div>
      <p>
        <Link to="/datasets">Back to datasets</Link>
      </p>
      <h1>{detail.data.name}</h1>
      <p>{detail.data.row_count} rows</p>

      <h2>Preview</h2>
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {detail.data.preview.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c}>{String(row[c] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      {health.data && (
        <>
          <h2>Health</h2>
          <p>
            {health.data.column_count} columns · {health.data.null_cells_pct}% null cells ·{" "}
            {health.data.duplicate_rows_pct}% duplicate rows
          </p>
          {health.data.warnings.map((w) => (
            <p key={w} role="alert">
              {w}
            </p>
          ))}
        </>
      )}

      <h2>Query</h2>
      {error && <p role="alert">{error}</p>}
      <form onSubmit={runQuery}>
        <fieldset>
          <legend>Columns (check to select, optionally aggregate, optionally group by)</legend>
          <table>
            <thead>
              <tr>
                <th>Select</th>
                <th>Column</th>
                <th>Aggregate</th>
                <th>Group by</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((c) => (
                <tr key={c}>
                  <td>
                    <input type="checkbox" checked={selected.has(c)} onChange={() => toggleSelected(c)} />
                  </td>
                  <td>{c}</td>
                  <td>
                    <select
                      value={aggByColumn[c] ?? ""}
                      onChange={(e) => setAggByColumn((prev) => ({ ...prev, [c]: e.target.value as Agg }))}
                    >
                      <option value="">(none)</option>
                      <option value="sum">sum</option>
                      <option value="avg">avg</option>
                      <option value="min">min</option>
                      <option value="max">max</option>
                      <option value="count">count</option>
                    </select>
                  </td>
                  <td>
                    <input type="checkbox" checked={groupBy.has(c)} onChange={() => toggleGroupBy(c)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </fieldset>

        <fieldset>
          <legend>Join to Items</legend>
          <label>
            Match dataset column
            <select value={joinItemsOn} onChange={(e) => setJoinItemsOn(e.target.value)}>
              <option value="">(no join)</option>
              {columns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            to Item.sku
          </label>
          {joinItemsOn && (
            <table>
              <thead>
                <tr>
                  <th>Select</th>
                  <th>Column</th>
                  <th>Group by</th>
                </tr>
              </thead>
              <tbody>
                {ITEM_COLUMNS.map((c) => (
                  <tr key={c}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.has(`items.${c}`)}
                        onChange={() => toggleSelected(`items.${c}`)}
                      />
                    </td>
                    <td>items.{c}</td>
                    <td>
                      <input
                        type="checkbox"
                        checked={groupBy.has(`items.${c}`)}
                        onChange={() => toggleGroupBy(`items.${c}`)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </fieldset>

        <fieldset>
          <legend>Filters</legend>
          {filters.map((f, i) => (
            <div key={i}>
              <select value={f.column} onChange={(e) => updateFilter(i, { column: e.target.value })}>
                {columns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <select value={f.op} onChange={(e) => updateFilter(i, { op: e.target.value as FilterOp })}>
                <option value="eq">=</option>
                <option value="neq">!=</option>
                <option value="gt">&gt;</option>
                <option value="gte">&gt;=</option>
                <option value="lt">&lt;</option>
                <option value="lte">&lt;=</option>
                <option value="contains">contains</option>
              </select>
              <input value={f.value} onChange={(e) => updateFilter(i, { value: e.target.value })} />
              <button type="button" onClick={() => removeFilter(i)}>
                Remove
              </button>
            </div>
          ))}
          <button type="button" onClick={addFilter}>
            Add filter
          </button>
        </fieldset>

        <button type="submit" disabled={query.isPending}>
          Run query
        </button>
      </form>

      {query.data && (
        <>
          <h2>Results</h2>
          <table>
            <thead>
              <tr>
                {query.data.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {query.data.rows.map((row, i) => (
                <tr key={i}>
                  {query.data!.columns.map((c) => (
                    <td key={c}>{String(row[c] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
