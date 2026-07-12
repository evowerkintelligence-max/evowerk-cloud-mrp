import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "@/pages/analytics/shared";

interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
}

const DEFAULT_SQL = "SELECT * FROM dataset LIMIT 100";

export function SqlEditor() {
  const datasets = useDatasets();
  const [datasetId, setDatasetId] = useState("");
  const [sql, setSql] = useState(DEFAULT_SQL);

  const activeDataset = datasets.data?.find((d) => d.id === datasetId) ?? datasets.data?.[0];
  const effectiveDatasetId = datasetId || activeDataset?.id || "";

  const query = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${effectiveDatasetId}/sql`, {
        method: "POST",
        body: JSON.stringify({ sql, limit: 500 }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Query failed");
      return res.json() as Promise<QueryResult>;
    },
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
            <div className="flex flex-wrap items-end gap-4">
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
              {activeDataset && (
                <div className="flex flex-wrap gap-1.5 pb-2">
                  {activeDataset.columns.map((c) => (
                    <Badge
                      key={c.name}
                      variant="neutral"
                      className="cursor-pointer"
                      title={`Insert ${c.name}`}
                      onClick={() => setSql((s) => (s ? `${s} ${c.name}` : c.name))}
                    >
                      {c.name}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          )}

          <textarea
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            rows={8}
            spellCheck={false}
            className="w-full rounded-md border border-border bg-background p-3 font-mono text-sm outline-none focus:ring-2 focus:ring-primary/40"
          />
          <p className="text-xs text-muted-foreground">
            Runs against a single read-only table named <code>dataset</code> — SELECT only, no joins to other
            files. Columns are text (VARCHAR); cast numeric columns explicitly, e.g. <code>CAST(qty AS DOUBLE)</code>.
          </p>
          <Button disabled={!effectiveDatasetId} loading={query.isPending} onClick={() => query.mutate()}>
            Run SQL
          </Button>
          {query.isError && <Alert variant="destructive">{(query.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {query.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle>Results ({query.data.rows.length} rows)</CardTitle>
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
    </div>
  );
}
