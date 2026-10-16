import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useDatasets } from "@/pages/analytics/shared";
import { TYPE_LABELS, j, type AlertRule, type AlertType, type ScheduleOption, type Severity } from "./types";

function useSyncSchedules() {
  return useQuery<ScheduleOption[]>({
    queryKey: ["sync-schedules"],
    queryFn: async () => (await j<{ schedules: ScheduleOption[] }>(apiFetch("/sync-schedules"))).schedules,
  });
}

// ─── Rule form ──────────────────────────────────────────────────────────────

type FormState = Record<string, string>;

export function RuleForm({
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
