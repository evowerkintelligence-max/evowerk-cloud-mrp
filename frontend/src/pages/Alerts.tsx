import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "@/pages/analytics/shared";
import { cn } from "@/lib/utils";

// Ported from the desktop app's renderer/js/alerts.js + routes/alerts.py:
// threshold rules scanned on demand against a Dataset or a scheduled sync's
// latest metrics, instead of a background job - the user clicks Scan (or
// opens this page) to evaluate every enabled rule right now.

type AlertType = "machine_downtime" | "quality" | "scrap" | "inventory" | "production_target" | "stream_metric";
type Severity = "critical" | "warning" | "info";

interface AlertRule {
  id: string;
  dataset_id: string | null;
  name: string;
  type: AlertType;
  severity: Severity;
  enabled: boolean;
  config: Record<string, unknown>;
}

interface AlertTrigger {
  rule_id: string;
  rule_name: string;
  type: AlertType;
  severity: Severity;
  triggered: boolean;
  value: number;
  threshold: number;
  message: string;
  offenders: string[];
  evaluated_at: string;
}

interface ScanResponse {
  results: AlertTrigger[];
  triggered_count: number;
  evaluated_count: number;
}

interface ScheduleOption {
  schedule_id: string;
  name: string;
}

async function j<T>(resPromise: Promise<Response>): Promise<T> {
  const res = await resPromise;
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Request failed");
  return res.json();
}

const TYPE_LABELS: Record<AlertType, string> = {
  machine_downtime: "Machine Downtime",
  quality: "Quality",
  scrap: "Scrap",
  inventory: "Inventory",
  production_target: "Production Target",
  stream_metric: "Stream Metric",
};

const SEV_VARIANT: Record<Severity, "danger" | "warning" | "info"> = {
  critical: "danger",
  warning: "warning",
  info: "info",
};

function useSyncSchedules() {
  return useQuery<ScheduleOption[]>({
    queryKey: ["sync-schedules"],
    queryFn: async () => (await j<{ schedules: ScheduleOption[] }>(apiFetch("/sync-schedules"))).schedules,
  });
}

// ─── Rule form ──────────────────────────────────────────────────────────────

type FormState = Record<string, string>;

function RuleForm({
  editing,
  onDone,
}: {
  editing: AlertRule | null;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const schedules = useSyncSchedules();

  const [name, setName] = useState(editing?.name ?? "");
  const [type, setType] = useState<AlertType | "">(editing?.type ?? "");
  const [severity, setSeverity] = useState<Severity>(editing?.severity ?? "warning");
  const [datasetId, setDatasetId] = useState(editing?.dataset_id ?? "");
  const [form, setForm] = useState<FormState>(() => {
    const cfg = editing?.config ?? {};
    const out: FormState = {};
    for (const [k, v] of Object.entries(cfg)) if (v != null) out[k] = String(v);
    return out;
  });

  const columns = useMemo(
    () => datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [],
    [datasets.data, datasetId],
  );

  const set = (key: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const save = useMutation({
    mutationFn: async () => {
      if (!name || !type) throw new Error("Fill in Name and Type.");
      if (type !== "stream_metric" && !datasetId) throw new Error("Choose a dataset.");
      const config = buildConfig(type, form);
      const payload = { name, type, severity, dataset_id: type === "stream_metric" ? null : datasetId, config };
      if (editing) {
        await j(apiFetch(`/alerts/rules/${editing.id}`, { method: "PUT", body: JSON.stringify(payload) }));
      } else {
        await j(apiFetch("/alerts/rules", { method: "POST", body: JSON.stringify(payload) }));
      }
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["alert-rules"] });
      onDone();
    },
  });

  const colField = (key: string, label: string, required = false) => (
    <Field label={label + (required ? " *" : "")} key={key}>
      <Select value={form[key] ?? ""} onChange={set(key)}>
        <option value="">Select column…</option>
        {columns.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </Select>
    </Field>
  );

  const numField = (key: string, label: string, placeholder: string, required = false) => (
    <Field label={label + (required ? " *" : "")} key={key}>
      <Input type="number" step="any" value={form[key] ?? ""} onChange={set(key)} placeholder={placeholder} />
    </Field>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>{editing ? "Edit rule" : "New alert rule"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Line 2 downtime" />
          </Field>
          <Field label="Type">
            <Select value={type} onChange={(e) => setType(e.target.value as AlertType)} disabled={!!editing}>
              <option value="">Select type…</option>
              {Object.entries(TYPE_LABELS).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Severity">
            <Select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
              <option value="critical">Critical</option>
              <option value="warning">Warning</option>
              <option value="info">Info</option>
            </Select>
          </Field>
          {type && type !== "stream_metric" && (
            <Field label="Dataset">
              <Select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
                <option value="">Select dataset…</option>
                {(datasets.data ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          {type === "stream_metric" && (
            <Field label="Scheduled sync">
              <Select value={form.schedule_id ?? ""} onChange={set("schedule_id")}>
                <option value="">Select schedule…</option>
                {(schedules.data ?? []).map((s) => (
                  <option key={s.schedule_id} value={s.schedule_id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
        </div>

        {type === "machine_downtime" && (
          <div className="grid gap-4 sm:grid-cols-3">
            {colField("duration_col", "Duration Column", true)}
            {colField("machine_col", "Machine Column (optional)")}
            {numField("threshold", "Threshold (minutes)", "e.g. 60", true)}
          </div>
        )}
        {type === "quality" && (
          <div className="grid gap-4 sm:grid-cols-2">
            {colField("good_units_col", "Good Units Column", true)}
            {colField("total_units_col", "Total Units Column", true)}
            {colField("machine_col", "Machine Column (optional)")}
            {numField("defect_threshold_pct", "Defect Threshold %", "e.g. 5", true)}
          </div>
        )}
        {type === "scrap" && (
          <div className="grid gap-4 sm:grid-cols-2">
            {colField("scrap_col", "Scrap Units Column", true)}
            {colField("total_units_col", "Total Units Column", true)}
            {colField("group_col", "Machine/Reason Column (optional)")}
            {numField("scrap_threshold_pct", "Scrap Threshold %", "e.g. 5", true)}
          </div>
        )}
        {type === "inventory" && (
          <div className="grid gap-4 sm:grid-cols-3">
            {colField("quantity_col", "Quantity Column", true)}
            {colField("item_col", "Item Column (optional)")}
            {numField("min_threshold", "Min Threshold (units)", "e.g. 100", true)}
          </div>
        )}
        {type === "production_target" && (
          <div className="grid gap-4 sm:grid-cols-2">
            {colField("actual_col", "Actual Output Column", true)}
            {colField("target_col", "Target Column (or use flat value)")}
            {numField("target_value", "Flat Target Value", "e.g. 500")}
            {colField("group_col", "Group Column (optional)")}
            {numField("miss_threshold_pct", "Miss Threshold %", "e.g. 5", true)}
          </div>
        )}
        {type === "stream_metric" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Metric Name *">
              <Input value={form.metric_name ?? ""} onChange={set("metric_name")} placeholder="e.g. oee_pct" />
            </Field>
            <Field label="Comparison">
              <Select value={form.comparison ?? "gt"} onChange={set("comparison")}>
                <option value="gt">Above threshold</option>
                <option value="lt">Below threshold</option>
              </Select>
            </Field>
            {numField("threshold", "Threshold", "e.g. 80", true)}
          </div>
        )}

        {type && type !== "stream_metric" && (
          <div className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2">
            {colField("time_col", "Timestamp Column (optional — limits data window)")}
            {numField("lookback_days", "Lookback (days)", "e.g. 30")}
          </div>
        )}

        {type && (
          <div className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2">
            <Field label="Andon / relay webhook URL (optional)">
              <Input
                value={form.webhook_url ?? ""}
                onChange={set("webhook_url")}
                placeholder="https://..."
              />
            </Field>
            <p className="self-end pb-2 text-xs text-muted-foreground">
              Posted with the live triggered state on every "Scan All" — on while triggered, off once it clears.
            </p>
          </div>
        )}

        {save.isError && <Alert variant="destructive">{(save.error as Error).message}</Alert>}

        <div className="flex gap-2">
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            {editing ? "Update rule" : "Save rule"}
          </Button>
          <Button variant="secondary" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function buildConfig(type: AlertType, form: FormState): Record<string, unknown> {
  const num = (key: string) => (form[key] === undefined || form[key] === "" ? undefined : Number(form[key]));
  const str = (key: string) => form[key] || undefined;
  const lookback = { time_col: str("time_col"), lookback_days: num("lookback_days") };
  const webhook = { webhook_url: str("webhook_url") };

  switch (type) {
    case "machine_downtime":
      return { duration_col: form.duration_col ?? "", machine_col: str("machine_col"), threshold: num("threshold") ?? 60, ...lookback, ...webhook };
    case "quality":
      return {
        good_units_col: form.good_units_col ?? "", total_units_col: form.total_units_col ?? "",
        machine_col: str("machine_col"), defect_threshold_pct: num("defect_threshold_pct") ?? 5, ...lookback, ...webhook,
      };
    case "scrap":
      return {
        scrap_col: form.scrap_col ?? "", total_units_col: form.total_units_col ?? "",
        group_col: str("group_col"), scrap_threshold_pct: num("scrap_threshold_pct") ?? 5, ...lookback, ...webhook,
      };
    case "inventory":
      return { quantity_col: form.quantity_col ?? "", item_col: str("item_col"), min_threshold: num("min_threshold") ?? 100, ...lookback, ...webhook };
    case "production_target":
      return {
        actual_col: form.actual_col ?? "", target_col: str("target_col"), target_value: num("target_value"),
        group_col: str("group_col"), miss_threshold_pct: num("miss_threshold_pct") ?? 5, ...lookback, ...webhook,
      };
    case "stream_metric":
      return {
        schedule_id: form.schedule_id ?? "", metric_name: form.metric_name ?? "",
        comparison: form.comparison || "gt", threshold: num("threshold") ?? 0, ...webhook,
      };
    default:
      return {};
  }
}

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
      apiFetch(`/alerts/rules/${rule.id}`, { method: "PUT", body: JSON.stringify({ enabled: !rule.enabled }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-rules"] }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`/alerts/rules/${id}`, { method: "DELETE" }),
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
