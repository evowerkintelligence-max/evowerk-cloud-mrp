import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface RunSummary {
  id: string;
  workflow_id: string;
  workflow_name: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

interface RunEvent {
  id: string;
  step_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

interface RunDetail extends RunSummary {
  events: RunEvent[];
}

const STATUS_VARIANT: Record<string, "neutral" | "success" | "danger" | "warning" | "info"> = {
  queued: "neutral", running: "info", success: "success", failed: "danger", partial: "warning",
};

const ACTIVE_STATUSES = new Set(["queued", "running"]);

const TABS = [
  { value: "active", label: "Active" },
  { value: "history", label: "History" },
] as const;

type Tab = (typeof TABS)[number]["value"];

export function RunHistory() {
  const [tab, setTab] = useState<Tab>("active");
  const [expandedRun, setExpandedRun] = useState<{ workflowId: string; runId: string } | null>(null);

  const runs = useQuery<RunSummary[]>({
    queryKey: ["run-history"],
    queryFn: async () => {
      const res = await apiFetch("/workflows/runs?limit=200");
      if (!res.ok) throw new Error("Failed to load run history");
      return res.json();
    },
    refetchInterval: (query) =>
      query.state.data?.some((r) => ACTIVE_STATUSES.has(r.status)) ? 2000 : false,
  });

  const runDetail = useQuery<RunDetail>({
    queryKey: ["run-history-detail", expandedRun?.workflowId, expandedRun?.runId],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${expandedRun!.workflowId}/runs/${expandedRun!.runId}`);
      if (!res.ok) throw new Error("Failed to load run");
      return res.json();
    },
    enabled: !!expandedRun,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });

  const filtered = (runs.data ?? []).filter((r) =>
    tab === "active" ? ACTIVE_STATUSES.has(r.status) : !ACTIVE_STATUSES.has(r.status),
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Run History"
        description="Every workflow run across the org, newest first. Active runs poll live; History holds finished/failed runs."
        actions={<PageTabs tabs={[...TABS]} active={tab} onChange={setTab} />}
      />

      <Card key={tab} className="animate-fade-in">
        <CardContent className="space-y-3 p-4">
          {filtered.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {tab === "active" ? "No runs in progress." : "No finished runs yet."}
            </p>
          )}
          {filtered.map((r) => {
            const isOpen = expandedRun?.runId === r.id;
            return (
              <div key={r.id} className="rounded-md border border-border">
                <button
                  className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-2.5 text-left text-sm"
                  onClick={() => setExpandedRun(isOpen ? null : { workflowId: r.workflow_id, runId: r.id })}
                >
                  <span className="flex items-center gap-3">
                    <Badge variant={STATUS_VARIANT[r.status] ?? "neutral"}>{r.status}</Badge>
                    <Link
                      to={`/workflows/${r.workflow_id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="font-medium hover:underline"
                    >
                      {r.workflow_name}
                    </Link>
                    <span className="text-muted-foreground">{new Date(r.created_at).toLocaleString()}</span>
                  </span>
                  <span className="text-muted-foreground">{isOpen ? "Hide" : "Show timeline"}</span>
                </button>
                {isOpen && runDetail.data && (
                  <div className="space-y-1.5 border-t border-border px-4 py-3">
                    {runDetail.data.events.length === 0 && (
                      <p className="text-xs text-muted-foreground">No events recorded yet.</p>
                    )}
                    {runDetail.data.events.map((e) => (
                      <div key={e.id} className="flex items-center gap-2 text-xs">
                        <span className="w-32 shrink-0 text-muted-foreground">
                          {new Date(e.created_at).toLocaleTimeString()}
                        </span>
                        <Badge variant="neutral">{e.event_type}</Badge>
                        {e.step_id && <span className="font-mono">{e.step_id}</span>}
                        {typeof e.payload.status === "string" && (
                          <Badge variant={e.payload.status === "success" ? "success" : "danger"}>
                            {String(e.payload.status)}
                          </Badge>
                        )}
                        {typeof e.payload.error === "string" && (
                          <span className="truncate text-rose-300">{e.payload.error}</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}
