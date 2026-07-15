import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useElapsed } from "./shared";

const SHIFT_STORAGE_KEY = "downtime-tracking-last-shift";
const POLL_MS = 5000;

interface DowntimeIncident {
  id: string;
  machine: string;
  reason: string;
  shift: string;
  started_at: string;
  ended_at: string | null;
}

const REASON_LABELS: Record<string, string> = {
  breakdown: "Breakdown",
  changeover: "Changeover / Setup",
  material_shortage: "Material Shortage",
  planned_maintenance: "Planned Maintenance",
  quality_hold: "Quality Hold",
  other: "Other",
};
const REASONS = Object.keys(REASON_LABELS);

function IncidentCard({
  incident,
  onEnd,
  onDiscard,
  ending,
  discarding,
}: {
  incident: DowntimeIncident;
  onEnd: () => void;
  onDiscard: () => void;
  ending: boolean;
  discarding: boolean;
}) {
  const elapsed = useElapsed(incident.started_at);
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
        <div>
          <p className="font-medium">{incident.machine}</p>
          <p className="text-sm text-muted-foreground">
            {REASON_LABELS[incident.reason] ?? incident.reason} · Shift {incident.shift} · {elapsed}
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" loading={ending} onClick={onEnd}>
            Mark up
          </Button>
          <Button size="sm" variant="destructive" loading={discarding} onClick={onDiscard}>
            Discard
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function DowntimeTracking() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [machine, setMachine] = useState("");
  const [reason, setReason] = useState(REASONS[0]);
  const [shift, setShift] = useState(() => localStorage.getItem(SHIFT_STORAGE_KEY) ?? "");
  const [error, setError] = useState<string | null>(null);

  const openIncidents = useQuery<DowntimeIncident[]>({
    queryKey: ["downtime-tracking", "open"],
    queryFn: async () => {
      const res = await apiFetch("/downtime-tracking/incidents?open=true");
      if (!res.ok) throw new Error("Failed to load open incidents");
      return res.json();
    },
    refetchInterval: POLL_MS,
  });

  const machines = useQuery<string[]>({
    queryKey: ["downtime-tracking", "machines"],
    queryFn: async () => {
      const res = await apiFetch("/downtime-tracking/machines");
      if (!res.ok) throw new Error("Failed to load machines");
      return res.json();
    },
  });

  const shifts = useQuery<string[]>({
    queryKey: ["downtime-tracking", "shifts"],
    queryFn: async () => {
      const res = await apiFetch("/downtime-tracking/shifts");
      if (!res.ok) throw new Error("Failed to load shifts");
      return res.json();
    },
  });

  const liveDataset = useQuery<{ dataset_id: string | null }>({
    queryKey: ["downtime-tracking", "live-dataset"],
    queryFn: async () => {
      const res = await apiFetch("/downtime-tracking/live-dataset");
      if (!res.ok) throw new Error("Failed to load live dataset");
      return res.json();
    },
  });

  const start = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/downtime-tracking/incidents", {
        method: "POST",
        body: JSON.stringify({ machine, reason, shift }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to mark down");
      return res.json();
    },
    onSuccess: () => {
      setError(null);
      localStorage.setItem(SHIFT_STORAGE_KEY, shift);
      setMachine("");
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "open"] });
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "machines"] });
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "shifts"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const end = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/downtime-tracking/incidents/${id}/end`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to mark up");
      return res.json();
    },
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "open"] });
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "live-dataset"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const discard = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/downtime-tracking/incidents/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to discard");
    },
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["downtime-tracking", "open"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const incidents = openIncidents.data ?? [];
  const liveDatasetId = liveDataset.data?.dataset_id ?? null;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Downtime Tracking"
        description="Live board — mark a machine down with a reason, mark it back up when it's fixed. Every closed incident feeds the Downtime analytics page automatically."
        actions={
          liveDatasetId ? (
            <Button onClick={() => navigate(`/analytics/downtime?dataset_id=${liveDatasetId}`)}>
              View live analytics
            </Button>
          ) : undefined
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Report downtime</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Machine">
              <Input
                list="downtime-machine-options"
                value={machine}
                onChange={(e) => setMachine(e.target.value)}
                placeholder="Line 3 - Press 2"
              />
              <datalist id="downtime-machine-options">
                {(machines.data ?? []).map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </Field>
            <Field label="Reason">
              <Select value={reason} onChange={(e) => setReason(e.target.value)}>
                {REASONS.map((r) => (
                  <option key={r} value={r}>
                    {REASON_LABELS[r]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Shift">
              <Input
                list="downtime-shift-options"
                value={shift}
                onChange={(e) => setShift(e.target.value)}
                placeholder="A"
              />
              <datalist id="downtime-shift-options">
                {(shifts.data ?? []).map((s) => (
                  <option key={s} value={s} />
                ))}
              </datalist>
            </Field>
          </div>
          {error && <Alert variant="destructive">{error}</Alert>}
          <Button disabled={!machine || !reason || !shift} loading={start.isPending} onClick={() => start.mutate()}>
            Mark down
          </Button>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">Currently down</h2>
          <Badge variant={incidents.length > 0 ? "warning" : "success"}>{incidents.length}</Badge>
        </div>
        {incidents.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing's down right now.</p>
        )}
        {incidents.map((incident) => (
          <IncidentCard
            key={incident.id}
            incident={incident}
            ending={end.isPending && end.variables === incident.id}
            discarding={discard.isPending && discard.variables === incident.id}
            onEnd={() => end.mutate(incident.id)}
            onDiscard={() => discard.mutate(incident.id)}
          />
        ))}
      </div>
    </div>
  );
}
