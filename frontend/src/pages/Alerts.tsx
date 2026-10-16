import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "@/pages/analytics/shared";
import { cn } from "@/lib/utils";
import { SEV_VARIANT, TYPE_LABELS, j, type AlertRule, type AlertTrigger, type ScanResponse } from "./alerts/types";
import { RuleForm } from "./alerts/RuleForm";

// Ported from the desktop app's renderer/js/alerts.js + routes/alerts.py:
// threshold rules scanned on demand against a Dataset or a scheduled sync's
// latest metrics, instead of a background job - the user clicks Scan (or
// opens this page) to evaluate every enabled rule right now.

// ─── Scan results ───────────────────────────────────────────────────────────

function ScanResults({ scan }: { scan: ScanResponse | null }) {
  if (!scan) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {scan.evaluated_count
            ? `${scan.triggered_count} triggered / ${scan.evaluated_count} evaluated`
            : "No enabled rules evaluated."}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {scan.results.length === 0 && (
          <p className="text-sm text-muted-foreground">Enable at least one rule and click Scan All.</p>
        )}
        {scan.results.map((r) => (
          <div
            key={r.rule_id}
            className={cn(
              "rounded-md border px-4 py-3 text-sm",
              r.triggered ? "border-warning/30 bg-warning/10" : "border-border bg-secondary/30",
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{r.triggered ? "⚠" : "✓"} {r.rule_name}</span>
              <Badge variant={SEV_VARIANT[r.severity]}>{r.severity}</Badge>
              <Badge variant="neutral">{TYPE_LABELS[r.type] ?? r.type}</Badge>
              <span className="ml-auto text-xs text-muted-foreground">
                {new Date(r.evaluated_at).toLocaleTimeString()}
              </span>
            </div>
            <p className="mt-1">{r.message}</p>
            {r.offenders.length > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">{r.offenders.join(" · ")}</p>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

// ─── Page ───────────────────────────────────────────────────────────────────

export function Alerts() {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<AlertRule | null>(null);
  const [scan, setScan] = useState<ScanResponse | null>(null);

  const rules = useQuery<AlertRule[]>({
    queryKey: ["alert-rules"],
    queryFn: () => j(apiFetch("/alerts/rules")),
  });

  const scanAll = useMutation({
    mutationFn: () => j<ScanResponse>(apiFetch("/alerts/scan", { method: "POST" })),
    onSuccess: setScan,
  });

  const toggle = useMutation({
    mutationFn: (rule: AlertRule) =>
      j(apiFetch(`/alerts/rules/${rule.id}`, { method: "PUT", body: JSON.stringify({ enabled: !rule.enabled }) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-rules"] }),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/alerts/rules/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Request failed");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-rules"] }),
  });

  const scanOne = useMutation({
    mutationFn: (id: string) => j<AlertTrigger>(apiFetch(`/alerts/scan/${id}`, { method: "POST" })),
    onSuccess: (result) => {
      setScan((prev) => {
        const results = prev ? prev.results.filter((r) => r.rule_id !== result.rule_id) : [];
        const next = [...results, result];
        return { results: next, triggered_count: next.filter((r) => r.triggered).length, evaluated_count: next.length };
      });
    },
  });

  const datasetName = (id: string | null) => (id ? datasets.data?.find((d) => d.id === id)?.name ?? id : "—");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Alerts"
        description="Define threshold rules and scan datasets or scheduled syncs for breaches."
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" loading={scanAll.isPending} onClick={() => scanAll.mutate()}>
              Scan All
            </Button>
            <Button
              onClick={() => {
                setEditing(null);
                setShowForm((s) => !s);
              }}
            >
              {showForm ? "Cancel" : "+ New Rule"}
            </Button>
          </div>
        }
      />

      {showForm && (
        <RuleForm
          key={editing?.id ?? "new"}
          editing={editing}
          onDone={() => {
            setShowForm(false);
            setEditing(null);
          }}
        />
      )}

      {toggle.isError && <Alert variant="destructive">{toggle.error.message}</Alert>}
      {remove.isError && <Alert variant="destructive">{remove.error.message}</Alert>}

      <Card>
        <CardContent className="p-0">
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Rule</TH>
                  <TH>Severity</TH>
                  <TH>Dataset</TH>
                  <TH>Enabled</TH>
                  <TH>Actions</TH>
                </TR>
              </THead>
              <TBody>
                {(rules.data ?? []).map((r) => (
                  <TR key={r.id}>
                    <TD>
                      <span className="font-medium">{r.name}</span>{" "}
                      <Badge variant="neutral">{TYPE_LABELS[r.type] ?? r.type}</Badge>
                    </TD>
                    <TD>
                      <Badge variant={SEV_VARIANT[r.severity]}>{r.severity}</Badge>
                    </TD>
                    <TD className="text-xs text-muted-foreground">{datasetName(r.dataset_id)}</TD>
                    <TD>
                      <Checkbox checked={r.enabled} onChange={() => toggle.mutate(r)} />
                    </TD>
                    <TD className="space-x-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEditing(r);
                          setShowForm(true);
                        }}
                      >
                        Edit
                      </Button>
                      <Button variant="ghost" size="sm" loading={scanOne.isPending} onClick={() => scanOne.mutate(r.id)}>
                        Test
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => remove.mutate(r.id)}>
                        Delete
                      </Button>
                    </TD>
                  </TR>
                ))}
                {rules.data?.length === 0 && (
                  <TR>
                    <TD colSpan={5} className="text-center text-muted-foreground">
                      No alert rules yet. Click + New Rule to add one.
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          </TableWrap>
        </CardContent>
      </Card>

      <ScanResults scan={scan} />
    </div>
  );
}
