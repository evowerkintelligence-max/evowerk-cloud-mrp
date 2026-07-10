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

// ── OEE ──────────────────────────────────────────────────────────────────

interface OeeTrendPoint {
  date: string;
  availability: number;
  performance: number;
  quality: number;
  oee: number;
}

interface OeeMachineResult {
  machine: string;
  availability: number;
  performance: number;
  quality: number;
  oee: number;
  total_units: number;
}

interface OeeComputeResponse {
  availability: number;
  performance: number;
  quality: number;
  oee: number;
  performance_estimated: boolean;
  row_count: number;
  trend: OeeTrendPoint[];
  machines: OeeMachineResult[];
}

// ── Quality ──────────────────────────────────────────────────────────────

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

interface QualityAnalyzeResponse {
  summary: {
    total_records: number;
    total_defects: number;
    metric_label: string;
    metric_suffix: string;
    defect_metric: number;
    worst_batch: string;
  };
  batch_stats: QualityBatchStat[];
  correlations: QualityCorrelation[];
}

// ── Process mining ───────────────────────────────────────────────────────

interface ProcessActivity {
  name: string;
  cases: number;
  executions: number;
  avg_duration_min: number;
  avg_wait_min: number;
}

interface ProcessBottleneck extends ProcessActivity {
  severity: string;
}

interface ProcessMiningResponse {
  summary: {
    total_cases: number;
    total_activities: number;
    avg_case_duration_min: number;
    longest_bottleneck: string;
  };
  activities: ProcessActivity[];
  bottlenecks: ProcessBottleneck[];
  conformance: {
    reference_sequence: string[];
    conformance_pct: number;
    conforming_cases: number;
    deviating_cases: number;
  };
  warnings: string[];
}

export function DatasetDetail() {
  const { id } = useParams<{ id: string }>();
  const [aggByColumn, setAggByColumn] = useState<Record<string, Agg>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [groupBy, setGroupBy] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<FilterRow[]>([]);
  const [joinItemsOn, setJoinItemsOn] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [oeeCols, setOeeCols] = useState({
    planned_time_col: "",
    operating_time_col: "",
    total_units_col: "",
    good_units_col: "",
    date_col: "",
    machine_col: "",
  });
  const [oeeError, setOeeError] = useState<string | null>(null);

  const [qualityCols, setQualityCols] = useState({ defect_column: "", batch_column: "" });
  const [qualityError, setQualityError] = useState<string | null>(null);

  const [processCols, setProcessCols] = useState({ case_column: "", activity_column: "", timestamp_column: "" });
  const [processError, setProcessError] = useState<string | null>(null);

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

  const oee = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = {};
      for (const [k, v] of Object.entries(oeeCols)) if (v) body[k] = v;
      const res = await apiFetch(`/datasets/${id}/oee/compute`, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail ?? "OEE compute failed");
      }
      return (await res.json()) as OeeComputeResponse;
    },
    onError: (e: Error) => setOeeError(e.message),
  });

  const quality = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = {};
      for (const [k, v] of Object.entries(qualityCols)) if (v) body[k] = v;
      const res = await apiFetch(`/datasets/${id}/quality/analyze`, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail ?? "Quality analysis failed");
      }
      return (await res.json()) as QualityAnalyzeResponse;
    },
    onError: (e: Error) => setQualityError(e.message),
  });

  const processMining = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = {};
      for (const [k, v] of Object.entries(processCols)) if (v) body[k] = v;
      const res = await apiFetch(`/datasets/${id}/process-mining/analyze`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail ?? "Process mining analysis failed");
      }
      return (await res.json()) as ProcessMiningResponse;
    },
    onError: (e: Error) => setProcessError(e.message),
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

      <h2>OEE (Overall Equipment Effectiveness)</h2>
      {oeeError && <p role="alert">{oeeError}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setOeeError(null);
          oee.mutate();
        }}
      >
        {(
          [
            ["planned_time_col", "Planned time (required)"],
            ["operating_time_col", "Operating time (required)"],
            ["total_units_col", "Total units (required)"],
            ["good_units_col", "Good units (required)"],
            ["date_col", "Date (optional, enables trend)"],
            ["machine_col", "Machine (optional, enables breakdown)"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} style={{ display: "block" }}>
            {label}
            <select
              value={oeeCols[key]}
              onChange={(e) => setOeeCols((prev) => ({ ...prev, [key]: e.target.value }))}
            >
              <option value="">(select column)</option>
              {columns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        ))}
        <button type="submit" disabled={oee.isPending}>
          Compute OEE
        </button>
      </form>

      {oee.data && (
        <>
          <p>
            <strong>OEE {oee.data.oee}%</strong> = Availability {oee.data.availability}% × Performance{" "}
            {oee.data.performance}% × Quality {oee.data.quality}%
            {oee.data.performance_estimated && " (no target rate given — Performance assumed 100%)"}
          </p>
          {oee.data.machines.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Machine</th>
                  <th>Availability</th>
                  <th>Performance</th>
                  <th>Quality</th>
                  <th>OEE</th>
                  <th>Total units</th>
                </tr>
              </thead>
              <tbody>
                {oee.data.machines.map((m) => (
                  <tr key={m.machine}>
                    <td>{m.machine}</td>
                    <td>{m.availability}%</td>
                    <td>{m.performance}%</td>
                    <td>{m.quality}%</td>
                    <td>{m.oee}%</td>
                    <td>{m.total_units}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {oee.data.trend.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Availability</th>
                  <th>Performance</th>
                  <th>Quality</th>
                  <th>OEE</th>
                </tr>
              </thead>
              <tbody>
                {oee.data.trend.map((t) => (
                  <tr key={t.date}>
                    <td>{t.date}</td>
                    <td>{t.availability}%</td>
                    <td>{t.performance}%</td>
                    <td>{t.quality}%</td>
                    <td>{t.oee}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <h2>Quality Analytics</h2>
      {qualityError && <p role="alert">{qualityError}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setQualityError(null);
          quality.mutate();
        }}
      >
        <label style={{ display: "block" }}>
          Defect column (0/1 flag or defect count, required)
          <select
            value={qualityCols.defect_column}
            onChange={(e) => setQualityCols((prev) => ({ ...prev, defect_column: e.target.value }))}
          >
            <option value="">(select column)</option>
            {columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "block" }}>
          Batch/lot column (optional)
          <select
            value={qualityCols.batch_column}
            onChange={(e) => setQualityCols((prev) => ({ ...prev, batch_column: e.target.value }))}
          >
            <option value="">(none)</option>
            {columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={quality.isPending}>
          Analyze quality
        </button>
      </form>

      {quality.data && (
        <>
          <p>
            <strong>
              {quality.data.summary.defect_metric}
              {quality.data.summary.metric_suffix} {quality.data.summary.metric_label}
            </strong>{" "}
            — {quality.data.summary.total_defects} defects across {quality.data.summary.total_records} records.
            Worst batch: {quality.data.summary.worst_batch}
          </p>
          {quality.data.correlations.length > 0 && (
            <>
              <h3>Feature correlations with defects</h3>
              <table>
                <thead>
                  <tr>
                    <th>Feature</th>
                    <th>Correlation</th>
                    <th>Direction</th>
                    <th>Strength</th>
                  </tr>
                </thead>
                <tbody>
                  {quality.data.correlations.map((c) => (
                    <tr key={c.feature}>
                      <td>{c.feature}</td>
                      <td>{c.correlation}</td>
                      <td>{c.direction}</td>
                      <td>{c.strength}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {quality.data.batch_stats.length > 0 && (
            <>
              <h3>Batch breakdown</h3>
              <table>
                <thead>
                  <tr>
                    <th>Batch</th>
                    <th>Records</th>
                    <th>Defect rate</th>
                  </tr>
                </thead>
                <tbody>
                  {quality.data.batch_stats.map((b) => (
                    <tr key={b.batch_id}>
                      <td>{b.batch_id}</td>
                      <td>{b.total_records}</td>
                      <td>{b.defect_rate}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}

      <h2>Process Mining</h2>
      {processError && <p role="alert">{processError}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setProcessError(null);
          processMining.mutate();
        }}
      >
        {(
          [
            ["case_column", "Case/job/order ID (required)"],
            ["activity_column", "Activity/step name (required)"],
            ["timestamp_column", "Event timestamp (required)"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} style={{ display: "block" }}>
            {label}
            <select
              value={processCols[key]}
              onChange={(e) => setProcessCols((prev) => ({ ...prev, [key]: e.target.value }))}
            >
              <option value="">(select column)</option>
              {columns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        ))}
        <button type="submit" disabled={processMining.isPending}>
          Discover process
        </button>
      </form>

      {processMining.data && (
        <>
          <p>
            {processMining.data.summary.total_cases} cases · {processMining.data.summary.total_activities}{" "}
            activities · avg case duration {processMining.data.summary.avg_case_duration_min} min · biggest
            bottleneck: {processMining.data.summary.longest_bottleneck}
          </p>
          <p>
            Conformance to majority path ({processMining.data.conformance.reference_sequence.join(" → ")}):{" "}
            {processMining.data.conformance.conformance_pct}% ({processMining.data.conformance.conforming_cases}{" "}
            conforming, {processMining.data.conformance.deviating_cases} deviating)
          </p>
          {processMining.data.warnings.map((w) => (
            <p key={w} role="alert">
              {w}
            </p>
          ))}
          {processMining.data.bottlenecks.length > 0 && (
            <>
              <h3>Bottlenecks (highest avg wait time)</h3>
              <table>
                <thead>
                  <tr>
                    <th>Activity</th>
                    <th>Avg wait (min)</th>
                    <th>Severity</th>
                    <th>Cases</th>
                  </tr>
                </thead>
                <tbody>
                  {processMining.data.bottlenecks.map((b) => (
                    <tr key={b.name}>
                      <td>{b.name}</td>
                      <td>{b.avg_wait_min}</td>
                      <td>{b.severity}</td>
                      <td>{b.cases}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </div>
  );
}
