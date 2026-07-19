import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { SqlCodeEditor, type SqlColumn, type SqlColumnDtype } from "@/components/SqlCodeEditor";
import { SendToReportButton, useCurrentUser, useDatasets } from "@/pages/analytics/shared";

interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
}

interface HistoryEntry {
  id: string;
  action: string;
  dataset_id: string | null;
  sql: string | null;
  created_at: string;
}

interface HealthColumn {
  name: string;
  inferred_dtype: string;
}

const DEFAULT_SQL = "SELECT * FROM dataset LIMIT 100";

export function SqlEditor() {
  const qc = useQueryClient();
  const datasets = useDatasets();
  // Query history is org-wide (every member sees everyone's queries), but the
  // backend restricts deleting/clearing it to owners - hide the controls for
  // everyone else rather than let them click a button that 403s.
  const currentUser = useCurrentUser();
  const isOwner = currentUser.data?.role === "owner";
  const [datasetId, setDatasetId] = useState("");
  const [sql, setSql] = useState(DEFAULT_SQL);

  const activeDataset = datasets.data?.find((d) => d.id === datasetId) ?? datasets.data?.[0];
  const effectiveDatasetId = datasetId || activeDataset?.id || "";

  // Same query key as DatasetDetail's Schema/SQL tabs, so switching to a dataset
  // already viewed there is a no-op fetch. inferred_dtype (not column.dtype, which
  // is always VARCHAR for CSV/TSV) is what tells us a column needs CAST(...AS DOUBLE).
  const health = useQuery<{ columns: HealthColumn[] }>({
    queryKey: ["dataset-health", effectiveDatasetId],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${effectiveDatasetId}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
    enabled: !!effectiveDatasetId,
  });
  const inferredByColumn = new Map(health.data?.columns.map((c) => [c.name, c.inferred_dtype]));
  const sqlColumns: SqlColumn[] = (activeDataset?.columns ?? []).map((c) => ({
    name: c.name,
    dtype: (inferredByColumn.get(c.name) as SqlColumnDtype | undefined) ?? "text",
  }));

  const history = useQuery<HistoryEntry[]>({
    queryKey: ["query-history"],
    queryFn: async () => {
      const res = await apiFetch("/datasets/query-history?limit=20");
      if (!res.ok) throw new Error("Failed to load history");
      return res.json();
    },
  });
  const sqlHistory = (history.data ?? []).filter((h) => h.action === "dataset.sql_query");

  const query = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${effectiveDatasetId}/sql`, {
        method: "POST",
        body: JSON.stringify({ sql, limit: 500 }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Query failed");
      return res.json() as Promise<QueryResult>;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["query-history"] }),
  });

  function rerun(entry: HistoryEntry) {
    if (entry.dataset_id) setDatasetId(entry.dataset_id);
    if (entry.sql) setSql(entry.sql);
  }

  const deleteEntry = useMutation({
    mutationFn: (id: string) => apiFetch(`/datasets/query-history/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["query-history"] }),
  });

  const clearHistory = useMutation({
    mutationFn: () => apiFetch("/datasets/query-history?action=dataset.sql_query", { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["query-history"] }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="SQL Editor"
        description="Run a read-only SELECT against any uploaded dataset. Backed by a sandboxed DuckDB table named `dataset`."
      />

      <Card>
        <CardHeader>
          <CardTitle>Query</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {datasets.isLoading ? (
            <Skeleton className="h-10 w-64" />
          ) : (
            <div className="min-w-[16rem] space-y-1.5">
              <label className="text-sm font-medium text-muted-foreground">Dataset</label>
              <Select value={effectiveDatasetId} onChange={(e) => setDatasetId(e.target.value)}>
                {(datasets.data ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </div>
          )}

          <SqlCodeEditor value={sql} onChange={setSql} columns={sqlColumns} />
          <p className="text-xs text-muted-foreground">
            Runs against a single read-only table named <code>dataset</code> — SELECT only, no joins to other
            files. Start typing a column name for autocomplete — numeric/date columns insert pre-cast (
            <code>CAST(... AS DOUBLE)</code>).
          </p>
          <Button disabled={!effectiveDatasetId} loading={query.isPending} onClick={() => query.mutate()}>
            Run SQL
          </Button>
          {query.isError && <Alert variant="destructive">{(query.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {query.isSuccess && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle>Results ({query.data.rows.length} rows)</CardTitle>
            <SendToReportButton
              spec={{ type: "sql", dataset_id: effectiveDatasetId, sql_text: sql }}
              label={activeDataset?.name ?? "SQL query"}
            />
          </CardHeader>
          <CardContent>
            <TableWrap className="max-h-[32rem] overflow-y-auto">
              <Table>
                <THead>
                  <TR>
                    {query.data.columns.map((c) => (
                      <TH key={c}>{c}</TH>
                    ))}
                  </TR>
                </THead>
                <TBody>
                  {query.data.rows.map((row, i) => (
                    <TR key={i}>
                      {query.data!.columns.map((c) => (
                        <TD key={c} className="whitespace-nowrap">
                          {row[c] === null || row[c] === undefined ? "—" : String(row[c])}
                        </TD>
                      ))}
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>History</CardTitle>
          {sqlHistory.length > 0 && isOwner && (
            <Button variant="ghost" size="sm" onClick={() => clearHistory.mutate()}>
              Clear all
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-2">
          {sqlHistory.length === 0 && (
            <p className="text-sm text-muted-foreground">Queries you run will show up here for quick rerun.</p>
          )}
          {sqlHistory.map((h) => (
            <div key={h.id} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
              <div className="min-w-0">
                <code className="block truncate text-xs">{h.sql}</code>
                <span className="text-xs text-muted-foreground">{new Date(h.created_at).toLocaleString()}</span>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button variant="secondary" size="sm" onClick={() => rerun(h)}>
                  Rerun
                </Button>
                {isOwner && (
                  <Button variant="ghost" size="sm" onClick={() => deleteEntry.mutate(h.id)}>
                    Delete
                  </Button>
                )}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
