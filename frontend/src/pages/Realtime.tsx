import { useEffect, useState } from "react";
import { Radio } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { subscribeToSSE } from "../lib/sse";
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
}

interface StreamSnapshot {
  ts: string | null;
  stream_id: string;
  name: string;
  status: "running" | "error" | "stopped" | "not_running_in_this_process";
  error: string | null;
  rows_in_window: number;
  rows_collected: number;
  duration: string | null;
  duration_s: number | null;
  last_tick_s: number | null;
  metrics: Record<string, number>;
}

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  running: "success",
  error: "danger",
  stopped: "neutral",
  not_running_in_this_process: "warning",
};

// column_map keys StreamRunner.snapshot() reads - see app/services/realtime.py.
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
    interval_seconds: 5,
    columnMap: Object.fromEntries(COLUMN_MAP_FIELDS.map((f) => [f.key, ""])) as Record<string, string>,
    sensorMap: [] as { name: string; field: string }[],
  };
}

function LiveMetrics({ streamId, initial }: { streamId: string; initial: StreamSnapshot }) {
  const [snap, setSnap] = useState<StreamSnapshot>(initial);

  useEffect(() => {
    const unsubscribe = subscribeToSSE<StreamSnapshot>(
      `/realtime/streams/${streamId}/events`,
      (data) => setSnap(data),
    );
    return unsubscribe;
  }, [streamId]);

  const metricEntries = Object.entries(snap.metrics);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Badge variant={STATUS_VARIANT[snap.status] ?? "neutral"}>{snap.status.replace(/_/g, " ")}</Badge>
        {snap.error && <span className="text-sm text-destructive">{snap.error}</span>}
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Rows collected" value={snap.rows_collected} tone="primary" />
        <StatCard label="Rows in window" value={snap.rows_in_window} tone="accent" />
        <StatCard label="Duration" value={snap.duration ?? "—"} tone="success" />
        <StatCard label="Last tick" value={snap.last_tick_s != null ? `${snap.last_tick_s}s ago` : "—"} tone="warning" />
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
  const [liveFor, setLiveFor] = useState<StreamSnapshot | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const integrations = useQuery<IntegrationOption[]>({
    queryKey: ["integrations"],
    queryFn: async () => {
      const res = await apiFetch("/integrations");
      if (!res.ok) throw new Error("Failed to load integrations");
      return res.json();
    },
  });

  const streams = useQuery<{ streams: StreamSnapshot[] }>({
    queryKey: ["realtime-streams"],
    queryFn: async () => {
      const res = await apiFetch("/realtime/streams");
      if (!res.ok) throw new Error("Failed to load streams");
      return res.json();
    },
    refetchInterval: 5000, // list view uses polling; the live detail view below uses SSE
  });

  const create = useMutation({
    mutationFn: async () => {
      const column_map = Object.fromEntries(Object.entries(form.columnMap).filter(([, v]) => v.trim()));
      const sensor_map = Object.fromEntries(
        form.sensorMap.filter((s) => s.name.trim() && s.field.trim()).map((s) => [s.name.trim(), s.field.trim()]),
      );
      const res = await apiFetch("/realtime/streams", {
        method: "POST",
        body: JSON.stringify({
          integration_id: form.integration_id,
          name: form.name,
          machine: form.machine.trim() || null,
          interval_seconds: form.interval_seconds,
          column_map,
          sensor_map,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create stream");
      return res.json();
    },
    onSuccess: () => {
      setFormOpen(false);
      setForm(emptyForm());
      setFormError(null);
      qc.invalidateQueries({ queryKey: ["realtime-streams"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const toggle = useMutation({
    mutationFn: async (stream: StreamSnapshot) => {
      const action = stream.status === "stopped" ? "start" : "stop";
      const res = await apiFetch(`/realtime/streams/${stream.stream_id}/${action}`, { method: "POST" });
      if (!res.ok) throw new Error("Request failed");
      return res.json();
    },
    onMutate: (stream) => setBusyId(stream.stream_id),
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["realtime-streams"] });
    },
  });

  const remove = useMutation({
    mutationFn: async (streamId: string) => {
      const res = await apiFetch(`/realtime/streams/${streamId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete stream");
    },
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["realtime-streams"] });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Realtime"
        description="Poll an integration on an interval and roll up live throughput/scrap/OEE metrics. Streams keep running across a backend restart if they were left running."
        actions={<Button onClick={() => setFormOpen(true)}>New stream</Button>}
      />

      {streams.data && streams.data.streams.length === 0 ? (
        <EmptyState
          icon={<Radio />}
          title="No live streams yet"
          description="Pick an integration and poll it on an interval to see live metrics."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Status</TH>
                <TH>Rows collected</TH>
                <TH>Last tick</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {streams.data?.streams.map((s) => (
                <TR key={s.stream_id}>
                  <TD className="font-medium">{s.name}</TD>
                  <TD>
                    <Badge variant={STATUS_VARIANT[s.status] ?? "neutral"}>{s.status.replace(/_/g, " ")}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{s.rows_collected}</TD>
                  <TD className="text-muted-foreground">{s.last_tick_s != null ? `${s.last_tick_s}s ago` : "—"}</TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="secondary" size="sm" onClick={() => setLiveFor(s)}>
                        View live
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={busyId === s.stream_id && toggle.isPending}
                        onClick={() => toggle.mutate(s)}
                      >
                        {s.status === "stopped" ? "Start" : "Stop"}
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        loading={busyId === s.stream_id && remove.isPending}
                        onClick={() => {
                          setBusyId(s.stream_id);
                          remove.mutate(s.stream_id);
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

      <Dialog open={formOpen} onClose={() => setFormOpen(false)} title="New stream">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Integration">
            <Select
              value={form.integration_id}
              onChange={(e) => setForm((f) => ({ ...f, integration_id: e.target.value }))}
              required
            >
              <option value="">Select an integration…</option>
              {integrations.data?.map((i) => (
                <option key={i.id} value={i.id}>{i.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          </Field>
          <Field label="Poll interval (seconds)">
            <Input
              type="number"
              min={1}
              max={300}
              value={form.interval_seconds}
              onChange={(e) => setForm((f) => ({ ...f, interval_seconds: Number(e.target.value) }))}
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
          <Field label="Machine (for CBM, optional)" hint="Set this to feed live sensor data from this stream into Predictive Maintenance's condition-based monitoring.">
            <Input value={form.machine} onChange={(e) => setForm((f) => ({ ...f, machine: e.target.value }))} />
          </Field>
          {form.machine && (
            <Field
              label="Sensor map"
              hint="Sensor name → field to read from each polled row. For an OPC-UA stream (one node per stream), map a single sensor name to the field 'value'."
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

      <Dialog open={liveFor !== null} onClose={() => setLiveFor(null)} title={`Live — ${liveFor?.name ?? ""}`} className="max-w-2xl">
        {liveFor && <LiveMetrics streamId={liveFor.stream_id} initial={liveFor} />}
      </Dialog>
    </div>
  );
}
