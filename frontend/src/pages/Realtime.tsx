import { useState } from "react";
import { Radio } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { STAT_TONES, StatCard } from "@/components/StatCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface IntegrationOption {
  id: string;
  name: string;
  type: string;
}

interface ScheduleSnapshot {
  ts: string | null;
  schedule_id: string;
  name: string;
  status: "running" | "error" | "stopped" | "not_synced_yet";
  error: string | null;
  row_count: number;
  metrics: Record<string, number>;
}

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  running: "success",
  error: "danger",
  stopped: "neutral",
  not_synced_yet: "warning",
};

// The subset of Integration types whose connector implements fetch_since() -
// keep this in sync with app/api/routes/sync_schedules.py's _INCREMENTAL_TYPES.
const INCREMENTAL_TYPES = new Set(["sqlserver", "oracle", "postgres", "mysql"]);

// column_map keys app/services/metrics_calc.py's compute_window_metrics reads.
const COLUMN_MAP_FIELDS: { key: string; label: string }[] = [
  { key: "units_col", label: "Units column" },
  { key: "total_units_col", label: "Total units column" },
  { key: "scrap_col", label: "Scrap column" },
  { key: "downtime_col", label: "Downtime (min) column" },
  { key: "planned_time_col", label: "Planned time column" },
  { key: "operating_time_col", label: "Operating time column" },
  { key: "good_units_col", label: "Good units column" },
  { key: "target_rate_col", label: "Target rate column (optional)" },
];

function emptyForm() {
  return {
    integration_id: "",
    name: "",
    machine: "",
    interval_seconds: 300,
    cursor_column: "",
    columnMap: Object.fromEntries(COLUMN_MAP_FIELDS.map((f) => [f.key, ""])) as Record<string, string>,
    sensorMap: [] as { name: string; field: string }[],
  };
}

function LiveMetrics({ scheduleId, initial }: { scheduleId: string; initial: ScheduleSnapshot }) {
  const { data: snap } = useQuery<ScheduleSnapshot>({
    queryKey: ["sync-schedule-snapshot", scheduleId],
    queryFn: async () => {
      const res = await apiFetch(`/sync-schedules/${scheduleId}/snapshot`);
      if (!res.ok) throw new Error("Failed to load snapshot");
      return res.json();
    },
    initialData: initial,
    refetchInterval: 30_000, // polling replaces the old SSE push
  });

  const current = snap ?? initial;
  const metricEntries = Object.entries(current.metrics);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Badge variant={STATUS_VARIANT[current.status] ?? "neutral"}>{current.status.replace(/_/g, " ")}</Badge>
        {current.error && <span className="text-sm text-destructive">{current.error}</span>}
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Rows in last sync" value={current.row_count} tone="primary" />
        <StatCard label="Last synced" value={current.ts ? new Date(current.ts).toLocaleTimeString() : "—"} tone="accent" />
      </div>
      {metricEntries.length > 0 && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {metricEntries.map(([key, value], i) => (
            <StatCard
              key={key}
              label={key.replace(/_/g, " ")}
              value={value}
              tone={STAT_TONES[i % STAT_TONES.length]}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function Realtime() {
  const qc = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(emptyForm());
  const [formError, setFormError] = useState<string | null>(null);
  const [liveFor, setLiveFor] = useState<ScheduleSnapshot | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const integrations = useQuery<IntegrationOption[]>({
    queryKey: ["integrations"],
    queryFn: async () => {
      const res = await apiFetch("/integrations");
      if (!res.ok) throw new Error("Failed to load integrations");
      return res.json();
    },
  });
  const incrementalIntegrations = integrations.data?.filter((i) => INCREMENTAL_TYPES.has(i.type)) ?? [];

  const schedules = useQuery<{ schedules: ScheduleSnapshot[] }>({
    queryKey: ["sync-schedules"],
    queryFn: async () => {
      const res = await apiFetch("/sync-schedules");
      if (!res.ok) throw new Error("Failed to load schedules");
      return res.json();
    },
    refetchInterval: 30_000,
  });

  const create = useMutation({
    mutationFn: async () => {
      const column_map = Object.fromEntries(Object.entries(form.columnMap).filter(([, v]) => v.trim()));
      const sensor_map = Object.fromEntries(
        form.sensorMap.filter((s) => s.name.trim() && s.field.trim()).map((s) => [s.name.trim(), s.field.trim()]),
      );
      const res = await apiFetch("/sync-schedules", {
        method: "POST",
        body: JSON.stringify({
          integration_id: form.integration_id,
          name: form.name,
          machine: form.machine.trim() || null,
          interval_seconds: form.interval_seconds,
          cursor_column: form.cursor_column,
          column_map,
          sensor_map,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create schedule");
      return res.json();
    },
    onSuccess: () => {
      setFormOpen(false);
      setForm(emptyForm());
      setFormError(null);
      qc.invalidateQueries({ queryKey: ["sync-schedules"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const toggle = useMutation({
    mutationFn: async (schedule: ScheduleSnapshot) => {
      const action = schedule.status === "stopped" ? "start" : "stop";
      const res = await apiFetch(`/sync-schedules/${schedule.schedule_id}/${action}`, { method: "POST" });
      if (!res.ok) throw new Error("Request failed");
      return res.json();
    },
    onMutate: (schedule) => setBusyId(schedule.schedule_id),
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["sync-schedules"] });
    },
  });

  const remove = useMutation({
    mutationFn: async (scheduleId: string) => {
      const res = await apiFetch(`/sync-schedules/${scheduleId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete schedule");
    },
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["sync-schedules"] });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Scheduled sync"
        description="Pull new rows from a cloud database integration on an interval and roll up throughput/scrap/OEE metrics from each batch."
        actions={<Button onClick={() => setFormOpen(true)}>New schedule</Button>}
      />

      {schedules.data && schedules.data.schedules.length === 0 ? (
        <EmptyState
          icon={<Radio />}
          title="No scheduled syncs yet"
          description="Pick a cloud database integration and sync it on an interval to see rolled-up metrics."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Status</TH>
                <TH>Rows (last sync)</TH>
                <TH>Last synced</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {schedules.data?.schedules.map((s) => (
                <TR key={s.schedule_id}>
                  <TD className="font-medium">{s.name}</TD>
                  <TD>
                    <Badge variant={STATUS_VARIANT[s.status] ?? "neutral"}>{s.status.replace(/_/g, " ")}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{s.row_count}</TD>
                  <TD className="text-muted-foreground">{s.ts ? new Date(s.ts).toLocaleTimeString() : "—"}</TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="secondary" size="sm" onClick={() => setLiveFor(s)}>
                        View details
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={busyId === s.schedule_id && toggle.isPending}
                        onClick={() => toggle.mutate(s)}
                      >
                        {s.status === "stopped" ? "Start" : "Stop"}
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        loading={busyId === s.schedule_id && remove.isPending}
                        onClick={() => {
                          setBusyId(s.schedule_id);
                          remove.mutate(s.schedule_id);
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

      <Dialog open={formOpen} onClose={() => setFormOpen(false)} title="New scheduled sync">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Integration" hint="Only cloud-database integrations that support incremental sync are listed.">
            <Select
              value={form.integration_id}
              onChange={(e) => setForm((f) => ({ ...f, integration_id: e.target.value }))}
              required
            >
              <option value="">Select an integration…</option>
              {incrementalIntegrations.map((i) => (
                <option key={i.id} value={i.id}>{i.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          </Field>
          <Field label="Sync interval (seconds)" hint="Minimum 60 seconds — this runs on a schedule, not a live poll loop.">
            <Input
              type="number"
              min={60}
              max={86400}
              value={form.interval_seconds}
              onChange={(e) => setForm((f) => ({ ...f, interval_seconds: Number(e.target.value) }))}
            />
          </Field>
          <Field label="Cursor column" hint="A timestamp or auto-increment id column in your source query — used to fetch only new rows each sync.">
            <Input
              value={form.cursor_column}
              onChange={(e) => setForm((f) => ({ ...f, cursor_column: e.target.value }))}
              required
            />
          </Field>
          <Field label="Column map" hint="Map your source's column names to the metrics engine reads. Leave blank to skip a metric.">
            <div className="grid grid-cols-2 gap-3">
              {COLUMN_MAP_FIELDS.map((f) => (
                <Input
                  key={f.key}
                  placeholder={f.label}
                  value={form.columnMap[f.key]}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, columnMap: { ...prev.columnMap, [f.key]: e.target.value } }))
                  }
                />
              ))}
            </div>
          </Field>
          <Field label="Machine (for CBM, optional)" hint="Set this to feed synced sensor data into Predictive Maintenance's condition-based monitoring.">
            <Input value={form.machine} onChange={(e) => setForm((f) => ({ ...f, machine: e.target.value }))} />
          </Field>
          {form.machine && (
            <Field
              label="Sensor map"
              hint="Sensor name → field to read from each synced row."
            >
              <div className="space-y-2">
                {form.sensorMap.map((s, i) => (
                  <div key={i} className="flex gap-2">
                    <Input
                      placeholder="Sensor name (e.g. vib_x)"
                      value={s.name}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          sensorMap: f.sensorMap.map((row, j) => (j === i ? { ...row, name: e.target.value } : row)),
                        }))
                      }
                    />
                    <Input
                      placeholder="Source field name"
                      value={s.field}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          sensorMap: f.sensorMap.map((row, j) => (j === i ? { ...row, field: e.target.value } : row)),
                        }))
                      }
                    />
                    <Button
                      type="button" variant="secondary" size="sm"
                      onClick={() => setForm((f) => ({ ...f, sensorMap: f.sensorMap.filter((_, j) => j !== i) }))}
                    >
                      Remove
                    </Button>
                  </div>
                ))}
                <Button
                  type="button" variant="secondary" size="sm"
                  onClick={() => setForm((f) => ({ ...f, sensorMap: [...f.sensorMap, { name: "", field: "" }] }))}
                >
                  Add sensor
                </Button>
              </div>
            </Field>
          )}
          {formError && <p className="text-sm text-destructive">{formError}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button type="submit" loading={create.isPending}>Create</Button>
          </div>
        </form>
      </Dialog>

      <Dialog open={liveFor !== null} onClose={() => setLiveFor(null)} title={`Details — ${liveFor?.name ?? ""}`} className="max-w-2xl">
        {liveFor && <LiveMetrics scheduleId={liveFor.schedule_id} initial={liveFor} />}
      </Dialog>
    </div>
  );
}
