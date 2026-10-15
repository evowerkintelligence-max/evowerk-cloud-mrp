import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { StatCard } from "@/components/StatCard";
import { C, fileSize, fmt } from "../shared";
import { computeReadiness } from "../readiness";
import type { DatasetDetail, Health, Tab } from "./types";

export function Overview({ d }: { d: DatasetDetail }) {
  const rows: [string, string][] = [
    ["Name", d.name],
    ["Original filename", d.original_filename],
    ["Format", d.fmt],
    ["Rows", fmt(d.row_count)],
    ["Columns", String(d.columns.length)],
    ["Size", fileSize(d.size_bytes)],
    ["Uploaded", new Date(d.created_at).toLocaleString()],
    ["ID", d.id],
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Metadata</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-3 sm:grid-cols-2">
          {rows.map(([label, val]) => (
            <div key={label} className="flex items-center justify-between gap-4 border-b border-border/50 pb-2 text-sm">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="truncate font-medium" title={val}>
                {val}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

export function ReadinessPanel({ d, onOpenTab }: { d: DatasetDetail; onOpenTab: (tab: Tab) => void }) {
  const navigate = useNavigate();
  const health = useQuery<Health>({
    queryKey: ["dataset-health", d.id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
  });

  if (health.isLoading) return <Skeleton className="h-64 w-full" />;
  if (!health.data) return <Alert variant="destructive">Failed to compute health.</Alert>;

  const results = computeReadiness(health.data.columns);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Feature readiness</CardTitle>
      </CardHeader>
      <CardContent>
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Feature</TH>
                <TH>Status</TH>
                <TH>Why</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {results.map((r) => (
                <TR key={r.key}>
                  <TD className="font-medium">{r.label}</TD>
                  <TD>
                    <Badge variant={r.ready ? "success" : "neutral"}>{r.ready ? "Ready" : "Not ready"}</Badge>
                  </TD>
                  <TD className="text-xs text-muted-foreground">{r.missing.join("; ") || "—"}</TD>
                  <TD>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={!r.ready}
                      onClick={() => (r.isTab ? onOpenTab(r.key as Tab) : navigate(r.to))}
                    >
                      Open
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      </CardContent>
    </Card>
  );
}

export function Schema({ d }: { d: DatasetDetail }) {
  // CSV/TSV datasets are sniffed ALL_VARCHAR=1 at ingest, so c.dtype is always
  // "VARCHAR" for them - inferred_dtype (from the health endpoint, based on
  // actual cell content) is the real type. Same query key as HealthPanel/
  // QueryPanel, so this is a no-op fetch if either tab was already visited.
  const health = useQuery<Health>({
    queryKey: ["dataset-health", d.id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
  });
  const inferredByColumn = new Map(health.data?.columns.map((c) => [c.name, c.inferred_dtype]));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Columns</CardTitle>
      </CardHeader>
      <CardContent>
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Type</TH>
              </TR>
            </THead>
            <TBody>
              {d.columns.map((c) => (
                <TR key={c.name}>
                  <TD className="font-medium">{c.name}</TD>
                  <TD>
                    <Badge variant="neutral">{inferredByColumn.get(c.name) ?? c.dtype}</Badge>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      </CardContent>
    </Card>
  );
}

export function Preview({ d }: { d: DatasetDetail }) {
  const cols = d.columns.map((c) => c.name);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Preview (first {d.preview.length} rows)</CardTitle>
      </CardHeader>
      <CardContent>
        <TableWrap className="max-h-[32rem] overflow-y-auto">
          <Table>
            <THead>
              <TR>
                {cols.map((c) => (
                  <TH key={c}>{c}</TH>
                ))}
              </TR>
            </THead>
            <TBody>
              {d.preview.map((row, i) => (
                <TR key={i}>
                  {cols.map((c) => (
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
  );
}

export function HealthPanel({ id }: { id: string }) {
  const health = useQuery<Health>({
    queryKey: ["dataset-health", id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${id}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
  });

  if (health.isLoading) return <Skeleton className="h-64 w-full" />;
  if (!health.data) return <Alert variant="destructive">Failed to compute health.</Alert>;
  const h = health.data;

  return (
    <div className="space-y-6">
      {h.warnings.map((w) => (
        <Alert key={w} variant="warning">
          {w}
        </Alert>
      ))}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Rows" value={fmt(h.row_count)} tone="primary" />
        <StatCard
          label="Duplicate rows"
          tone="accent"
          value={
            <span className={h.duplicate_rows_pct > 5 ? "text-destructive" : undefined}>
              {fmt(h.duplicate_rows)} ({h.duplicate_rows_pct}%)
            </span>
          }
        />
        <StatCard
          label="Null cells"
          tone="warning"
          value={
            <span className={h.null_cells_pct > 25 ? "text-destructive" : undefined}>
              {fmt(h.total_null_cells)} ({h.null_cells_pct}%)
            </span>
          }
        />
        <StatCard label="Columns" value={h.column_count} tone="success" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Per-column profile</CardTitle>
        </CardHeader>
        <CardContent>
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Column</TH>
                  <TH>Type</TH>
                  <TH>Null %</TH>
                  <TH>Distinct</TH>
                  <TH>Numeric range</TH>
                  <TH>Outliers</TH>
                  <TH>Top values</TH>
                </TR>
              </THead>
              <TBody>
                {h.columns.map((c) => (
                  <TR key={c.name}>
                    <TD className="font-medium">{c.name}</TD>
                    <TD>
                      <Badge variant="neutral">{c.inferred_dtype}</Badge>
                    </TD>
                    <TD style={{ color: c.null_pct > 25 ? C.red : undefined }}>{c.null_pct}%</TD>
                    <TD>{fmt(c.distinct_count)}</TD>
                    <TD>{c.min !== null ? `${c.min} – ${c.max}` : "—"}</TD>
                    <TD style={{ color: (c.outlier_pct ?? 0) > 5 ? C.red : undefined }}>
                      {c.outlier_pct !== null ? `${c.outlier_count} (${c.outlier_pct}%)` : "—"}
                    </TD>
                    <TD className="max-w-xs truncate text-xs text-muted-foreground">
                      {c.top_values.map((v) => v.value).join(", ") || "—"}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        </CardContent>
      </Card>
    </div>
  );
}
