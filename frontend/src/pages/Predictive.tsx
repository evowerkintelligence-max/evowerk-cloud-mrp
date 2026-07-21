import { useMemo, useState } from "react";
import { AlertTriangle, DollarSign, TrendingUp } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { STAT_TONES, StatCard } from "@/components/StatCard";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "@/pages/analytics/shared";

interface MachineRisk {
  machine: string;
  current_oee: number;
  failure_probability: number;
  risk_level: string;
  top_driver: string;
  rul_days: number | null;
  ml_powered: boolean;
}

interface MaintenanceRec {
  machine: string;
  priority: string;
  action: string;
  reason: string;
  estimated_pm_hours: number;
  avoided_downtime_hours: number;
  net_saving: number;
}

interface PredictiveResult {
  machines: MachineRisk[];
  recommendations: MaintenanceRec[];
  cost_summary: { total_avoided_cost: number; total_pm_cost: number; net_savings: number; roi_pct: number; machines_at_risk: number };
  model_meta: { algorithm: string; accuracy_pct: number; n_training_samples: number } | null;
}

interface ScheduleItem {
  id: string;
  machine: string;
  priority: string;
  action: string;
  status: string;
}

const RISK_VARIANT: Record<string, "danger" | "warning" | "success"> = { high: "danger", medium: "warning", low: "success" };

const COLUMN_FIELDS = [
  { key: "date_col", label: "Date column", hints: [/date|time|day/i] },
  { key: "machine_col", label: "Machine column", hints: [/machine|equipment|asset/i] },
  { key: "planned_time_col", label: "Planned time", hints: [/planned/i] },
  { key: "operating_time_col", label: "Operating time", hints: [/operating|run/i] },
  { key: "total_units_col", label: "Total units", hints: [/total.*units|^total$/i] },
  { key: "good_units_col", label: "Good units", hints: [/good/i] },
] as const;

const OPTIONAL_COLUMN_FIELDS = [
  { key: "failure_col", label: "Failure flag column (optional)" },
  { key: "downtime_category_col", label: "Downtime category (optional)" },
] as const;

function ScheduleTable() {
  const qc = useQueryClient();
  const schedules = useQuery<ScheduleItem[]>({
    queryKey: ["maintenance-schedules"],
    queryFn: async () => {
      const res = await apiFetch("/predictive/schedules");
      if (!res.ok) throw new Error("Failed to load schedules");
      return res.json();
    },
  });

  const markDone = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/predictive/schedules/${id}`, { method: "PATCH", body: JSON.stringify({ status: "done" }) });
      if (!res.ok) throw new Error("Update failed");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-schedules"] }),
  });

  if (!schedules.data?.length) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Maintenance schedule</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Machine</TH>
                <TH>Priority</TH>
                <TH>Action</TH>
                <TH>Status</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {schedules.data.map((s) => (
                <TR key={s.id}>
                  <TD className="font-medium">{s.machine}</TD>
                  <TD>{s.priority}</TD>
                  <TD>{s.action}</TD>
                  <TD>
                    <Badge variant={s.status === "done" ? "success" : "neutral"}>{s.status}</Badge>
                  </TD>
                  <TD>
                    {s.status !== "done" && (
                      <Button size="sm" variant="outline" loading={markDone.isPending} onClick={() => markDone.mutate(s.id)}>
                        Mark done
                      </Button>
                    )}
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

export function Predictive() {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const [datasetId, setDatasetId] = useState("");
  const [cols, setCols] = useState<Record<string, string>>({});
  const [hourlyValue, setHourlyValue] = useState("1000");

  const columns = useMemo(
    () => datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [],
    [datasets.data, datasetId],
  );

  function pickDataset(id: string) {
    setDatasetId(id);
    const dsColumns = datasets.data?.find((d) => d.id === id)?.columns.map((c) => c.name) ?? [];
    const next: Record<string, string> = {};
    for (const f of COLUMN_FIELDS) {
      const match = f.hints.map((h) => dsColumns.find((c) => h.test(c))).find(Boolean);
      if (match) next[f.key] = match;
    }
    setCols(next);
  }

  const analyze = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = { ...cols, hourly_value: Number(hourlyValue) || 1000 };
      const res = await apiFetch(`/datasets/${datasetId}/predictive/analyze`, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Analysis failed");
      return res.json() as Promise<PredictiveResult>;
    },
  });

  const schedule = useMutation({
    mutationFn: async (rec: MaintenanceRec) => {
      const res = await apiFetch("/predictive/schedules", {
        method: "POST",
        body: JSON.stringify({ machine: rec.machine, priority: rec.priority, action: rec.action, reason: rec.reason, pm_hours: rec.estimated_pm_hours }),
      });
      if (!res.ok) throw new Error("Failed to schedule");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-schedules"] }),
  });

  const ready = Boolean(datasetId) && COLUMN_FIELDS.every((f) => cols[f.key]);

  return (
    <div className="space-y-6">
      <PageHeader title="Predictive Maintenance" description="Per-machine failure probability, remaining-useful-life projection, and maintenance recommendations." />

      <Card>
        <CardHeader>
          <CardTitle>Dataset & columns</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Dataset" className="max-w-md">
            <Select value={datasetId} onChange={(e) => pickDataset(e.target.value)}>
              <option value="">Select a dataset…</option>
              {(datasets.data ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.row_count.toLocaleString()} rows)
                </option>
              ))}
            </Select>
          </Field>

          {datasetId && (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {COLUMN_FIELDS.map((f) => (
                  <Field key={f.key} label={f.label}>
                    <Select value={cols[f.key] ?? ""} onChange={(e) => setCols({ ...cols, [f.key]: e.target.value })}>
                      <option value="">Select column…</option>
                      {columns.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </Select>
                  </Field>
                ))}
                {OPTIONAL_COLUMN_FIELDS.map((f) => (
                  <Field key={f.key} label={f.label}>
                    <Select value={cols[f.key] ?? ""} onChange={(e) => setCols({ ...cols, [f.key]: e.target.value })}>
                      <option value="">(none)</option>
                      {columns.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </Select>
                  </Field>
                ))}
                <Field label="Hourly value ($)">
                  <Input type="number" value={hourlyValue} onChange={(e) => setHourlyValue(e.target.value)} />
                </Field>
              </div>
              <Button disabled={!ready} loading={analyze.isPending} onClick={() => analyze.mutate()}>
                Analyze
              </Button>
            </>
          )}
          {analyze.isError && <Alert variant="destructive">{(analyze.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {analyze.isSuccess && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>
                Machine risk {analyze.data.model_meta && <span className="text-xs font-normal text-muted-foreground">({analyze.data.model_meta.algorithm}, {analyze.data.model_meta.accuracy_pct}% accuracy)</span>}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>Machine</TH>
                      <TH>OEE</TH>
                      <TH>Failure probability</TH>
                      <TH>Risk</TH>
                      <TH>Top driver</TH>
                      <TH>RUL (days)</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {analyze.data.machines.map((m) => (
                      <TR key={m.machine}>
                        <TD className="font-medium">{m.machine}</TD>
                        <TD>{(m.current_oee * 100).toFixed(1)}%</TD>
                        <TD>{(m.failure_probability * 100).toFixed(1)}%</TD>
                        <TD>
                          <Badge variant={RISK_VARIANT[m.risk_level] ?? "neutral"}>{m.risk_level}</Badge>
                        </TD>
                        <TD className="capitalize">{m.top_driver}</TD>
                        <TD>{m.rul_days ?? "Stable"}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recommendations</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-3">
                {[
                  { label: "Net savings", value: `$${analyze.data.cost_summary.net_savings.toLocaleString()}`, icon: <DollarSign /> },
                  { label: "ROI", value: `${analyze.data.cost_summary.roi_pct}%`, icon: <TrendingUp /> },
                  { label: "Machines at risk", value: analyze.data.cost_summary.machines_at_risk, icon: <AlertTriangle /> },
                ].map((s, i) => (
                  <StatCard key={s.label} label={s.label} value={s.value} icon={s.icon} tone={STAT_TONES[i % STAT_TONES.length]} />
                ))}
              </div>
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>Machine</TH>
                      <TH>Priority</TH>
                      <TH>Action</TH>
                      <TH>Net saving</TH>
                      <TH />
                    </TR>
                  </THead>
                  <TBody>
                    {analyze.data.recommendations.map((r, i) => (
                      <TR key={i}>
                        <TD className="font-medium">{r.machine}</TD>
                        <TD>{r.priority}</TD>
                        <TD>{r.action}</TD>
                        <TD className={r.net_saving >= 0 ? "text-emerald-400" : "text-rose-400"}>${r.net_saving.toLocaleString()}</TD>
                        <TD>
                          <Button size="sm" variant="secondary" loading={schedule.isPending} onClick={() => schedule.mutate(r)}>
                            Schedule
                          </Button>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
            </CardContent>
          </Card>
        </>
      )}

      <ScheduleTable />
    </div>
  );
}
