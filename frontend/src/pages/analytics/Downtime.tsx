import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { StatCard } from "@/components/StatCard";
import { useCurrentUser } from "@/lib/currentUser";
import {
  AXIS_LABEL,
  AXIS_LINE,
  C,
  EChart,
  EngineScreen,
  SPLIT_LINE,
  TOOLTIP,
  fmt,
  gradientAreaSeries,
  paretoOption,
  useElapsed,
} from "./shared";

// ─── Track (live board, ported from the former DowntimeTracking page) ──────

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

function TrackPanel({ onViewAnalytics }: { onViewAnalytics: (datasetId: string) => void }) {
  const qc = useQueryClient();
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
    <div className="space-y-6">
      {liveDatasetId && (
        <div className="flex justify-end">
          <Button variant="secondary" onClick={() => onViewAnalytics(liveDatasetId)}>
            View live analytics
          </Button>
        </div>
      )}

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

// ─── Analyze (Pareto engine over a dataset) ─────────────────────────────────

interface DowntimeResult {
  total_incidents: number;
  total_minutes: number;
  mttr_minutes: number;
  top_category: string | null;
  by_category: { category: string; incidents: number; total_minutes: number; pct_of_total: number; cumulative_pct: number }[];
  by_machine: { machine: string; incidents: number; total_minutes: number; mttr_minutes: number }[];
  trend: { date: string; incidents: number; total_minutes: number }[];
}

const FIELDS = [
  { key: "duration_col", label: "Duration", required: true, hints: [/duration/i, /downtime/i, /minutes/i, /hours/i] },
  { key: "duration_unit", label: "Duration unit", kind: "select" as const, options: ["minutes", "hours"] },
  { key: "category_col", label: "Category (optional)", hints: [/categor/i, /reason/i, /cause/i, /type/i] },
  { key: "machine_col", label: "Machine (optional)", hints: [/machine/i, /line/i, /asset/i, /equip/i, /station/i] },
  { key: "timestamp_col", label: "Timestamp (optional)", hints: [/date/i, /time/i, /start/i] },
];

const hours = (min: number) => (min >= 120 ? `${(min / 60).toFixed(1)} h` : `${fmt(Math.round(min))} min`);

function AnalyzePanel() {
  return (
    <EngineScreen<DowntimeResult>
      title="Downtime"
      description="Pareto analysis of downtime — where the minutes go, which machines bleed the most, and MTTR."
      path="/analytics/downtime"
      fields={FIELDS}
      runLabel="Analyze downtime"
      showHeader={false}
    >
      {(r) => (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Incidents" value={fmt(r.total_incidents)} tone="primary" />
            <StatCard label="Total downtime" value={hours(r.total_minutes)} tone="accent" />
            <StatCard label="MTTR" value={`${r.mttr_minutes} min`} hint="mean time to repair" tone="success" />
            <StatCard label="Top category" value={r.top_category ?? "—"} tone="warning" />
          </div>

          {r.by_category.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Pareto by category</CardTitle>
              </CardHeader>
              <CardContent>
                <EChart
                  height={300}
                  option={paretoOption({
                    categories: r.by_category.map((c) => c.category),
                    values: r.by_category.map((c) => c.total_minutes),
                    cumulativePct: r.by_category.map((c) => c.cumulative_pct),
                    barName: "Minutes",
                    barColor: C.red,
                    yAxisName: "minutes",
                  })}
                />
              </CardContent>
            </Card>
          )}

          {r.trend.length > 1 && (
            <Card>
              <CardHeader>
                <CardTitle>Trend</CardTitle>
              </CardHeader>
              <CardContent>
                <EChart
                  height={260}
                  option={{
                    tooltip: TOOLTIP,
                    grid: { top: 24, bottom: 28, left: 56, right: 20 },
                    xAxis: {
                      type: "category",
                      data: r.trend.map((t) => t.date),
                      axisLabel: AXIS_LABEL,
                      axisLine: AXIS_LINE,
                      boundaryGap: false,
                    },
                    yAxis: { type: "value", name: "minutes", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                    series: [gradientAreaSeries("Downtime (min)", r.trend.map((t) => t.total_minutes), C.red)],
                  }}
                />
              </CardContent>
            </Card>
          )}

          {r.by_machine.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>By machine</CardTitle>
              </CardHeader>
              <CardContent>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Machine</TH>
                        <TH>Incidents</TH>
                        <TH>Total minutes</TH>
                        <TH>MTTR (min)</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {r.by_machine.map((m) => (
                        <TR key={m.machine}>
                          <TD className="font-medium">{m.machine}</TD>
                          <TD>{fmt(m.incidents)}</TD>
                          <TD>{fmt(m.total_minutes)}</TD>
                          <TD>{m.mttr_minutes}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </EngineScreen>
  );
}

// ─── Page (Track + Analyze tabs) ────────────────────────────────────────────

type Tab = "track" | "analyze";

export function Downtime() {
  const org = useCurrentUser();
  // enabled_features isn't known until org-me resolves - default optimistically to
  // "track" (the common case) and only bump off it once loading has actually
  // settled and the org turns out not to have the feature.
  const hasTracking = (org.data?.enabled_features ?? []).includes("downtime_tracking");
  const [tab, setTab] = useState<Tab>("track");
  const [, setSearchParams] = useSearchParams();

  useEffect(() => {
    if (!org.isLoading && !hasTracking && tab === "track") setTab("analyze");
  }, [org.isLoading, hasTracking, tab]);

  return (
    <div className="space-y-8">
      <PageHeader
        title="Downtime"
        description="Track machine downtime live, and analyze where the minutes go."
        actions={
          hasTracking ? (
            <PageTabs
              tabs={[
                { value: "track", label: "Track" },
                { value: "analyze", label: "Analyze" },
              ]}
              active={tab}
              onChange={setTab}
            />
          ) : undefined
        }
      />
      {tab === "track" && hasTracking && (
        <TrackPanel
          onViewAnalytics={(datasetId) => {
            setSearchParams({ dataset_id: datasetId });
            setTab("analyze");
          }}
        />
      )}
      {tab === "analyze" && <AnalyzePanel />}
    </div>
  );
}
