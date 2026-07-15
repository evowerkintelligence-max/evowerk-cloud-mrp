import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { StatCard } from "@/components/StatCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useElapsed } from "./shared";

const STORAGE_KEY = "pace-tracking-session-id";
const POLL_MS = 5000;

interface PaceSession {
  id: string;
  line: string;
  operator: string;
  target_rate: number | null;
  status: "active" | "paused" | "stopped";
  pause_reason: "breakdown" | "setup" | "other" | null;
  started_at: string;
  stopped_at: string | null;
  dataset_id: string | null;
  good_count: number;
  reject_count: number;
  unplanned_dt_min: number;
  planned_dt_min: number;
  other_dt_min: number;
}

const PAUSE_LABELS: Record<string, string> = {
  breakdown: "Breakdown",
  setup: "Setup / Changeover",
  other: "Other",
};

export function PaceTracking() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [sessionId, setSessionId] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY));
  const [form, setForm] = useState({ line: "", operator: "", target_rate: "" });
  const [formError, setFormError] = useState<string | null>(null);
  const [pauseReason, setPauseReason] = useState("breakdown");
  const [showPausePicker, setShowPausePicker] = useState(false);

  const session = useQuery<PaceSession>({
    queryKey: ["pace-session", sessionId],
    queryFn: async () => {
      const res = await apiFetch(`/pace/sessions/${sessionId}`);
      if (!res.ok) throw new Error("Session not found");
      return res.json();
    },
    enabled: !!sessionId,
    // ponytail: a stopped session is terminal - its server-side state can never
    // change again, so stop polling once we see it rather than hitting the API every 5s forever.
    refetchInterval: (query) => (query.state.data?.status === "stopped" ? false : POLL_MS),
    retry: false,
  });

  function persist(id: string | null) {
    setSessionId(id);
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  }

  useEffect(() => {
    if (sessionId && session.isError) persist(null);
  }, [sessionId, session.isError]);

  const start = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/pace/sessions", {
        method: "POST",
        body: JSON.stringify({
          line: form.line,
          operator: form.operator,
          target_rate: form.target_rate ? Number(form.target_rate) : null,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to start session");
      return res.json() as Promise<PaceSession>;
    },
    onSuccess: (s) => {
      setFormError(null);
      persist(s.id);
      qc.setQueryData(["pace-session", s.id], s);
    },
    onError: (e: Error) => setFormError(e.message),
  });

  function action(path: string, body?: unknown) {
    return apiFetch(`/pace/sessions/${sessionId}/${path}`, {
      method: "POST",
      ...(body ? { body: JSON.stringify(body) } : {}),
    }).then(async (res) => {
      if (!res.ok) throw new Error((await res.json()).detail ?? "Request failed");
      return res.json() as Promise<PaceSession>;
    });
  }

  const tap = useMutation({
    mutationFn: (kind: "good" | "reject") => action("tap", { kind }),
    onSuccess: (s) => qc.setQueryData(["pace-session", sessionId], s),
  });
  const pause = useMutation({
    mutationFn: (reason: string) => action("pause", { reason }),
    onSuccess: (s) => {
      qc.setQueryData(["pace-session", sessionId], s);
      setShowPausePicker(false);
    },
  });
  const resume = useMutation({
    mutationFn: () => action("resume"),
    onSuccess: (s) => qc.setQueryData(["pace-session", sessionId], s),
  });
  const stop = useMutation({
    mutationFn: () => action("stop"),
    onSuccess: (s) => qc.setQueryData(["pace-session", sessionId], s),
  });

  const elapsed = useElapsed(session.data?.started_at);

  if (!sessionId || (!session.data && !session.isLoading)) {
    return (
      <div className="space-y-8">
        <PageHeader
          title="Pace Tracking"
          description="No sensors, no time-tracking system - just an operator tapping a button per completed unit. Stop turns the session into a normal dataset that every OEE feature already understands."
        />
        <Card>
          <CardHeader>
            <CardTitle>Start a session</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Line">
                <Input
                  value={form.line}
                  onChange={(e) => setForm({ ...form, line: e.target.value })}
                  placeholder="Assembly Line 3"
                />
              </Field>
              <Field label="Operator">
                <Input
                  value={form.operator}
                  onChange={(e) => setForm({ ...form, operator: e.target.value })}
                  placeholder="J. Alvarez"
                />
              </Field>
              <Field label="Target rate (units/min, optional)">
                <Input
                  type="number"
                  step="any"
                  value={form.target_rate}
                  onChange={(e) => setForm({ ...form, target_rate: e.target.value })}
                />
              </Field>
            </div>
            {formError && <Alert variant="destructive">{formError}</Alert>}
            <Button disabled={!form.line || !form.operator} loading={start.isPending} onClick={() => start.mutate()}>
              Start session
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!session.data) {
    return (
      <div className="space-y-8">
        <PageHeader title="Pace Tracking" description="Loading…" />
      </div>
    );
  }

  const s = session.data;

  if (s.status === "stopped") {
    return (
      <div className="space-y-8">
        <PageHeader title="Pace Tracking" description="Session complete." />
        <Card>
          <CardHeader>
            <CardTitle>Session stopped</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {s.line} — {s.operator}: {s.good_count} good, {s.reject_count} reject.
            </p>
            <div className="flex gap-2">
              {s.dataset_id && (
                <Button onClick={() => navigate(`/analytics/oee?dataset_id=${s.dataset_id}`)}>
                  View in OEE dashboard
                </Button>
              )}
              <Button variant="secondary" onClick={() => persist(null)}>
                Start another session
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Pace Tracking"
        description={`${s.line} — ${s.operator}`}
        actions={<Badge variant={s.status === "paused" ? "warning" : "success"}>{s.status}</Badge>}
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Elapsed" value={elapsed} tone="primary" />
        <StatCard label="Good" value={s.good_count} tone="success" />
        <StatCard label="Reject" value={s.reject_count} tone="warning" />
        <StatCard label="Total" value={s.good_count + s.reject_count} tone="accent" />
      </div>

      {s.status === "paused" && (
        <Alert variant="warning">Paused — {PAUSE_LABELS[s.pause_reason ?? ""] ?? s.pause_reason}</Alert>
      )}

      <div className="grid grid-cols-2 gap-4">
        <Button
          size="lg"
          className="h-24 text-xl"
          disabled={s.status !== "active"}
          loading={tap.isPending}
          onClick={() => tap.mutate("good")}
        >
          Good unit
        </Button>
        <Button
          size="lg"
          variant="destructive"
          className="h-24 text-xl"
          disabled={s.status !== "active"}
          loading={tap.isPending}
          onClick={() => tap.mutate("reject")}
        >
          Reject
        </Button>
      </div>

      <div className="flex flex-wrap gap-2">
        {s.status === "active" && (
          <Button variant="secondary" onClick={() => setShowPausePicker((v) => !v)}>
            Pause ▾
          </Button>
        )}
        {s.status === "paused" && (
          <Button variant="secondary" loading={resume.isPending} onClick={() => resume.mutate()}>
            Resume
          </Button>
        )}
        <Button variant="destructive" loading={stop.isPending} onClick={() => stop.mutate()}>
          Stop
        </Button>
      </div>

      {showPausePicker && (
        <Card>
          <CardContent className="flex flex-wrap items-end gap-3 pt-6">
            <Field label="Reason">
              <Select value={pauseReason} onChange={(e) => setPauseReason(e.target.value)}>
                <option value="breakdown">Breakdown</option>
                <option value="setup">Setup / Changeover</option>
                <option value="other">Other</option>
              </Select>
            </Field>
            <Button loading={pause.isPending} onClick={() => pause.mutate(pauseReason)}>
              Confirm pause
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
