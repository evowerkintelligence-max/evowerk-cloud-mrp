import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Brain } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "./analytics/shared";
import { useAutoMLRuns, useAutoMLRunsDetail, useDeleteAutoMLRun } from "./automl/api";

const STATUS_VARIANT: Record<string, "neutral" | "success" | "danger" | "info"> = {
  queued: "neutral",
  running: "info",
  success: "success",
  failed: "danger",
};

interface CompareMeta {
  scores?: Record<string, number>;
  rows_used?: number;
  class_balance?: { imbalance_ratio: number | null; imbalance_detected: boolean; handling_applied: boolean } | null;
}

function CompareTable({ ids }: { ids: string[] }) {
  const details = useAutoMLRunsDetail(ids);
  const datasets = useDatasets();
  const runs = details.map((q) => q.data).filter((r): r is NonNullable<typeof r> => !!r);

  if (runs.length < ids.length) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  const meta = (r: (typeof runs)[number]) => (r.meta ?? {}) as CompareMeta;
  const scoreKeys = [...new Set(runs.flatMap((r) => Object.keys(meta(r).scores ?? {})))];
  const datasetName = (r: (typeof runs)[number]) => datasets.data?.find((d) => d.id === r.dataset_id)?.name ?? r.dataset_id;
  const classBalance = (r: (typeof runs)[number]) => {
    const cb = meta(r).class_balance;
    if (!cb || cb.imbalance_ratio == null) return "—";
    return `${cb.imbalance_ratio}:1${cb.imbalance_detected ? ` (handled: ${cb.handling_applied ? "yes" : "no"})` : ""}`;
  };

  const rows: [string, (r: (typeof runs)[number]) => string][] = [
    ["Best model", (r) => r.best_model_name ?? "—"],
    ["Task", (r) => r.task],
    ["Primary metric", (r) => (r.metric_name && r.metric_value !== null ? `${r.metric_name}=${r.metric_value.toFixed(4)}` : "—")],
    ...scoreKeys.map((k): [string, (r: (typeof runs)[number]) => string] => [
      k.toUpperCase(),
      (r) => { const v = meta(r).scores?.[k]; return v == null ? "—" : v.toFixed(4); },
    ]),
    ["Dataset", (r) => datasetName(r)],
    ["Target", (r) => r.target_column],
    ["Models tried", (r) => String(r.leaderboard?.length ?? "—")],
    ["Rows used", (r) => String(meta(r).rows_used ?? "—")],
    ["Search method", (r) => r.config.search_method ?? "random"],
    ["Thoroughness", (r) => r.config.thoroughness],
    ["Time budget", (r) => (r.config.time_budget_seconds ? `${r.config.time_budget_seconds}s` : "—")],
    ["Class balance", classBalance],
    ["Trained", (r) => new Date(r.created_at).toLocaleString()],
  ];

  return (
    <TableWrap>
      <Table>
        <THead>
          <TR>
            <TH>Field</TH>
            {runs.map((r, i) => <TH key={r.id}>Run {i + 1}: {r.name}</TH>)}
          </TR>
        </THead>
        <TBody>
          {rows.map(([label, fn]) => (
            <TR key={label}>
              <TD className="font-medium">{label}</TD>
              {runs.map((r) => <TD key={r.id} className="text-muted-foreground">{fn(r)}</TD>)}
            </TR>
          ))}
        </TBody>
      </Table>
    </TableWrap>
  );
}

export function AutoML() {
  const navigate = useNavigate();
  const runs = useAutoMLRuns();
  const remove = useDeleteAutoMLRun();
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [compareIds, setCompareIds] = useState<Set<string>>(new Set());

  function toggleCompareMode() {
    setCompareMode((v) => !v);
    setCompareIds(new Set());
  }

  function toggleCompareId(id: string) {
    setCompareIds((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="AutoML"
        description="Train and compare sklearn models over a dataset — classification, regression, or forecasting."
        actions={
          <div className="flex gap-2">
            {runs.data && runs.data.length > 1 && (
              <Button variant={compareMode ? "primary" : "secondary"} onClick={toggleCompareMode}>
                {compareMode ? "Exit compare" : "Compare"}
              </Button>
            )}
            <Button onClick={() => navigate("/automl/new")}>New run</Button>
          </div>
        }
      />

      {runs.data && runs.data.length === 0 ? (
        <EmptyState
          icon={<Brain />}
          title="No AutoML runs yet"
          description="Pick a dataset and a target column, and let AutoML bake off several models for you."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                {compareMode && <TH />}
                <TH>Name</TH>
                <TH>Task</TH>
                <TH>Status</TH>
                <TH>Best model</TH>
                <TH>Metric</TH>
                <TH>Created</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {runs.data?.map((r) => (
                <TR key={r.id}>
                  {compareMode && (
                    <TD>
                      <Checkbox
                        checked={compareIds.has(r.id)}
                        onChange={() => toggleCompareId(r.id)}
                        disabled={r.status !== "success"}
                      />
                    </TD>
                  )}
                  <TD className="font-medium">
                    <Link to={`/automl/${r.id}`} className="hover:underline">
                      {r.name}
                    </Link>
                  </TD>
                  <TD className="text-muted-foreground">{r.task}</TD>
                  <TD>
                    <Badge variant={STATUS_VARIANT[r.status] ?? "neutral"}>{r.status}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{r.best_model_name ?? "—"}</TD>
                  <TD className="text-muted-foreground">
                    {r.metric_name && r.metric_value !== null ? `${r.metric_name}=${r.metric_value.toFixed(4)}` : "—"}
                  </TD>
                  <TD className="text-muted-foreground">{new Date(r.created_at).toLocaleString()}</TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="secondary" size="sm" onClick={() => navigate(`/automl/${r.id}`)}>
                        Open
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        loading={deletingId === r.id && remove.isPending}
                        onClick={() => {
                          setDeletingId(r.id);
                          remove.mutate(r.id, { onSettled: () => setDeletingId(null) });
                        }}
                      >
                        Delete
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      {compareMode && (
        <Card className="p-4">
          <div className="mb-3 text-sm font-medium">Run comparison</div>
          {compareIds.size < 2 ? (
            <p className="text-sm text-muted-foreground">Select 2 or more successful runs above to compare.</p>
          ) : (
            <CompareTable ids={Array.from(compareIds)} />
          )}
        </Card>
      )}

      {!compareMode && runs.data && runs.data.length > 0 && (
        <Card className="p-4 text-sm text-muted-foreground">
          Click a run to see its leaderboard, feature importances, and diagnostics.
        </Card>
      )}
    </div>
  );
}
