import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { SqlCodeEditor, type SqlColumn, type SqlColumnDtype } from "@/components/SqlCodeEditor";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { QueryBuilderFields, SendToReportButton, useQueryBuilder } from "../shared";
import type { DatasetDetail, Health, QueryResult } from "./types";

export function QueryPanel({ d }: { d: DatasetDetail }) {
  const names = d.columns.map((c) => c.name);
  const qb = useQueryBuilder(d.id, names);

  const query = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/query`, {
        method: "POST",
        body: JSON.stringify(qb.toRequestBody()),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Query failed");
      return res.json() as Promise<QueryResult>;
    },
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Query</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <QueryBuilderFields qb={qb} names={names} />

          <Button disabled={qb.healthLoading} loading={query.isPending} onClick={() => query.mutate()}>
            Run query
          </Button>
          {qb.healthLoading && (
            <p className="text-xs text-muted-foreground">Determining column types before filtering is allowed…</p>
          )}

          {query.isError && <Alert variant="destructive">{(query.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {query.isSuccess && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle>Results ({query.data.rows.length} rows)</CardTitle>
            <SendToReportButton spec={{ type: "table", dataset_id: d.id, query: qb.toRequestBody() }} label={d.name} />
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

// Strips DuckDB's internal error-class jargon ("Binder Error:", "Parser Error:", ...)
// from the message the backend passes through as-is (see sql_query.py) so the
// user sees the useful part (what's wrong with their query) without the label.
export function friendlySqlError(detail: string): string {
  return detail.replace(/^Query failed: (Binder|Parser|Catalog|Conversion|Syntax) Error:\s*/i, "Query failed: ");
}

export function SqlPanel({ d }: { d: DatasetDetail }) {
  const [sql, setSql] = useState(`SELECT * FROM dataset LIMIT 100`);

  // Same query key as Schema/HealthPanel, so this is a no-op fetch if either tab
  // was already visited. inferred_dtype (not c.dtype, which is always VARCHAR for
  // CSV/TSV) is what tells us a column actually needs CAST(... AS DOUBLE) in SQL.
  const health = useQuery<Health>({
    queryKey: ["dataset-health", d.id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
  });
  const inferredByColumn = new Map(health.data?.columns.map((c) => [c.name, c.inferred_dtype]));
  const sqlColumns: SqlColumn[] = d.columns.map((c) => ({
    name: c.name,
    dtype: (inferredByColumn.get(c.name) as SqlColumnDtype | undefined) ?? "text",
  }));

  const query = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/sql`, {
        method: "POST",
        body: JSON.stringify({ sql, limit: 500 }),
      });
      if (!res.ok) throw new Error(friendlySqlError((await res.json()).detail ?? "Query failed"));
      return res.json() as Promise<QueryResult>;
    },
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>SQL</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <SqlCodeEditor value={sql} onChange={setSql} columns={sqlColumns} />
          <p className="text-xs text-muted-foreground">
            Runs against a single read-only table named <code>dataset</code> — SELECT only, no joins to other
            files. Start typing a column name for autocomplete — numeric/date columns insert pre-cast (
            <code>CAST(... AS DOUBLE)</code>).
          </p>
          <Button loading={query.isPending} onClick={() => query.mutate()}>
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
