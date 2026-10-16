import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SendToReportButton, useDatasets } from "@/pages/analytics/shared";
import { PageTabs } from "@/components/PageTabs";
import type { RcaCase, RcaEvent } from "./types";
import { j, errorMessage, STATUS_VARIANT, STATUS_TRANSITIONS } from "./types";
import { CauseTree } from "./CauseTree";
import { Actions } from "./Actions";
import { Analysis } from "./Analysis";

function EventsLog({ caseId }: { caseId: string }) {
  const events = useQuery<RcaEvent[]>({
    queryKey: ["rca-events", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}/events`)),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>History</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {(events.data ?? []).map((e) => (
          <div key={e.id} className="text-sm text-muted-foreground">
            <span className="text-foreground">{e.summary}</span> — {e.actor ?? "system"}, {new Date(e.created_at).toLocaleString()}
          </div>
        ))}
        {events.data?.length === 0 && <div className="text-sm text-muted-foreground">No history yet.</div>}
      </CardContent>
    </Card>
  );
}

const TABS = ["cause tree", "actions", "analysis", "history"] as const;

const TAB_LABELS: Record<(typeof TABS)[number], string> = {
  "cause tree": "Cause tree",
  actions: "Actions",
  analysis: "Analysis",
  history: "History",
};

export function CaseDetail({ caseId, onBack }: { caseId: string; onBack: () => void }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]>("cause tree");
  const datasets = useDatasets();

  const caseQuery = useQuery<RcaCase>({
    queryKey: ["rca-case", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}`)),
  });

  const columns =
    datasets.data?.find((d) => d.id === caseQuery.data?.dataset_id)?.columns.map((c) => c.name) ?? [];

  const updateCase = useMutation({
    mutationFn: (patch: Partial<Pick<RcaCase, "status">>) =>
      j<RcaCase>(apiFetch(`/rca-cases/cases/${caseId}`, { method: "PATCH", body: JSON.stringify(patch) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-case", caseId] }),
  });

  const deleteCase = useMutation({
    mutationFn: () => apiFetch(`/rca-cases/cases/${caseId}`, { method: "DELETE" }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["rca-cases"] });
      onBack();
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={caseQuery.data?.title ?? "Case"}
        description={caseQuery.data?.problem_statement}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {caseQuery.data && (
              <SendToReportButton spec={{ type: "rca_case", case_id: caseId }} label={caseQuery.data.title} />
            )}
            <Button variant="secondary" onClick={onBack}>
              Back to cases
            </Button>
            <Button
              variant="ghost"
              className="text-rose-400 hover:text-rose-300"
              loading={deleteCase.isPending}
              onClick={() => {
                if (confirm(`Delete case "${caseQuery.data?.title}"? This cannot be undone.`)) deleteCase.mutate();
              }}
            >
              Delete case
            </Button>
          </div>
        }
      />

      {caseQuery.data && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={STATUS_VARIANT[caseQuery.data.status] ?? "neutral"}>{caseQuery.data.status}</Badge>
          {STATUS_TRANSITIONS[caseQuery.data.status]?.map((t) => (
            <Button
              key={t.value}
              size="sm"
              variant="outline"
              loading={updateCase.isPending}
              onClick={() => updateCase.mutate({ status: t.value })}
            >
              {t.label}
            </Button>
          ))}
        </div>
      )}
      {updateCase.isError && <Alert variant="destructive">{errorMessage(updateCase.error)}</Alert>}
      {deleteCase.isError && <Alert variant="destructive">{errorMessage(deleteCase.error)}</Alert>}

      <PageTabs
        tabs={TABS.map((t) => ({ value: t, label: TAB_LABELS[t] }))}
        active={tab}
        onChange={setTab}
      />

      {tab === "cause tree" && <CauseTree caseId={caseId} />}
      {tab === "actions" && <Actions caseId={caseId} columns={columns} />}
      {tab === "analysis" && <Analysis caseId={caseId} columns={columns} />}
      {tab === "history" && <EventsLog caseId={caseId} />}
    </div>
  );
}
