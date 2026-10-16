import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "@/pages/analytics/shared";
import type { RcaCase, RcaTrendEntry } from "./types";
import { j, errorMessage, STATUS_VARIANT } from "./types";

export function CaseList({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [problemStatement, setProblemStatement] = useState("");
  const [datasetId, setDatasetId] = useState("");

  const cases = useQuery<RcaCase[]>({
    queryKey: ["rca-cases"],
    queryFn: () => j(apiFetch("/rca-cases/cases")),
  });

  const trends = useQuery<{ total_root_causes: number; by_category: RcaTrendEntry[] }>({
    queryKey: ["rca-trends"],
    queryFn: () => j(apiFetch("/rca-cases/trends")),
  });

  const create = useMutation({
    mutationFn: () =>
      j<RcaCase>(
        apiFetch("/rca-cases/cases", {
          method: "POST",
          body: JSON.stringify({ title, problem_statement: problemStatement, dataset_id: datasetId }),
        }),
      ),
    onSuccess: async (c) => {
      await qc.invalidateQueries({ queryKey: ["rca-cases"] });
      setShowCreate(false);
      setTitle("");
      setProblemStatement("");
      onOpen(c.id);
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Root Cause Analysis"
        description="Investigate OEE loss with a cause tree, evidence engine, and CAPA action tracking."
        actions={<Button onClick={() => setShowCreate((s) => !s)}>{showCreate ? "Cancel" : "New case"}</Button>}
      />

      {showCreate && (
        <Card>
          <CardHeader>
            <CardTitle>New investigation</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Title">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="M2 underperforming since Monday" />
            </Field>
            <Field label="Problem statement">
              <Input value={problemStatement} onChange={(e) => setProblemStatement(e.target.value)} placeholder="What happened?" />
            </Field>
            <Field label="Dataset">
              <Select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
                <option value="">Select a dataset…</option>
                {(datasets.data ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
            {create.isError && <Alert variant="destructive">{errorMessage(create.error)}</Alert>}
            <Button disabled={!title || !datasetId} loading={create.isPending} onClick={() => create.mutate()}>
              Create case
            </Button>
          </CardContent>
        </Card>
      )}

      {(trends.data?.by_category.length ?? 0) > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Recurring root causes</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Root causes marked in 2+ cases, grouped by 6M category. A repeat category across investigations usually
              means a systemic fix pays off more than another one-off CAPA action.
            </p>
            {trends.data!.by_category.map((entry) => (
              <div key={entry.key} className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
                <Badge variant="warning">{entry.count}×</Badge>
                <span className="font-medium">{entry.key}</span>
                <span className="text-muted-foreground">in</span>
                {entry.case_ids.map((id, i) => (
                  <button
                    key={id}
                    className="text-primary underline underline-offset-2"
                    onClick={() => onOpen(id)}
                  >
                    {entry.case_titles[i] ?? "case"}
                  </button>
                ))}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="p-0">
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Title</TH>
                  <TH>Status</TH>
                  <TH>Owner</TH>
                  <TH>Created</TH>
                </TR>
              </THead>
              <TBody>
                {(cases.data ?? []).map((c) => (
                  <TR key={c.id} className="cursor-pointer" onClick={() => onOpen(c.id)}>
                    <TD className="font-medium">{c.title}</TD>
                    <TD>
                      <Badge variant={STATUS_VARIANT[c.status] ?? "neutral"}>{c.status}</Badge>
                    </TD>
                    <TD>{c.owner ?? "—"}</TD>
                    <TD>{new Date(c.created_at).toLocaleDateString()}</TD>
                  </TR>
                ))}
                {cases.data?.length === 0 && (
                  <TR>
                    <TD colSpan={4} className="text-center text-muted-foreground">
                      No cases yet.
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          </TableWrap>
        </CardContent>
      </Card>
    </div>
  );
}
