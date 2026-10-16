import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { autoMapColumns } from "@/pages/analytics/shared";
import type { RcaAction } from "./types";
import { j, errorMessage, OEE_FIELDS, DATE_FIELD } from "./types";

export function Actions({ caseId, columns }: { caseId: string; columns: string[] }) {
  const qc = useQueryClient();
  const [description, setDescription] = useState("");
  const [verifyCols, setVerifyCols] = useState<Record<string, string>>(() => ({
    ...{ planned_time_col: "", operating_time_col: "", total_units_col: "", good_units_col: "", date_col: "" },
    ...autoMapColumns(OEE_FIELDS, columns),
    ...autoMapColumns([DATE_FIELD], columns),
  }));

  const actions = useQuery<RcaAction[]>({
    queryKey: ["rca-actions", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}/actions`)),
  });

  const addAction = useMutation({
    mutationFn: () =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions`, { method: "POST", body: JSON.stringify({ description }) })),
    onSuccess: async () => {
      setDescription("");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
        qc.invalidateQueries({ queryKey: ["rca-case", caseId] }),
      ]);
    },
  });

  const markDone = useMutation({
    mutationFn: (id: string) =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions/${id}`, { method: "PATCH", body: JSON.stringify({ status: "done" }) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
  });

  const deleteAction = useMutation({
    mutationFn: (id: string) => apiFetch(`/rca-cases/cases/${caseId}/actions/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
  });

  const verify = useMutation({
    mutationFn: (id: string) =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions/${id}/verify`, { method: "POST", body: JSON.stringify(verifyCols) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
  });

  const verifyReady = Object.values(verifyCols).every(Boolean);

  return (
    <Card>
      <CardHeader>
        <CardTitle>CAPA actions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Describe the corrective action…" />
          <Button disabled={!description} loading={addAction.isPending} onClick={() => addAction.mutate()}>
            Add
          </Button>
        </div>
        {addAction.isError && <Alert variant="destructive">{errorMessage(addAction.error)}</Alert>}

        <div>
          <p className="mb-2 text-xs text-muted-foreground">
            Column mapping for "Verify effectiveness" below — compares average OEE before vs. after each action's
            completion date. Auto-filled where a column name matches; check them before verifying.
          </p>
          <div className="grid gap-2 sm:grid-cols-5">
            {[...OEE_FIELDS, DATE_FIELD].map((f) => (
              <Field key={f.key} label={f.label}>
                <Select value={verifyCols[f.key]} onChange={(e) => setVerifyCols({ ...verifyCols, [f.key]: e.target.value })}>
                  <option value="">Select column…</option>
                  {columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
            ))}
          </div>
        </div>
        {verify.isError && <Alert variant="destructive">{errorMessage(verify.error)}</Alert>}
        {markDone.isError && <Alert variant="destructive">{errorMessage(markDone.error)}</Alert>}
        {deleteAction.isError && <Alert variant="destructive">{errorMessage(deleteAction.error)}</Alert>}

        <div className="space-y-2">
          {(actions.data ?? []).map((a) => (
            <div key={a.id} className="rounded-md border border-border px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <span className="font-medium">{a.description}</span> <Badge variant="neutral">{a.status}</Badge>
                </div>
                <div className="flex gap-2">
                  {a.status === "open" && (
                    <Button size="sm" variant="outline" onClick={() => markDone.mutate(a.id)}>
                      Mark done
                    </Button>
                  )}
                  {a.status === "done" && (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={!verifyReady}
                      loading={verify.isPending}
                      onClick={() => verify.mutate(a.id)}
                    >
                      Verify effectiveness
                    </Button>
                  )}
                  {a.status !== "verified" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        if (confirm(`Delete action "${a.description}"?`)) deleteAction.mutate(a.id);
                      }}
                    >
                      Delete
                    </Button>
                  )}
                </div>
              </div>
              {a.status === "done" && !verifyReady && (
                <div className="mt-1 text-xs text-amber-400">Map all 5 columns above to verify effectiveness.</div>
              )}
              {a.verification && (
                <div className="mt-1 text-xs text-muted-foreground">
                  OEE {a.verification.before_oee}% → {a.verification.after_oee}% ({a.verification.delta_oee > 0 ? "+" : ""}
                  {a.verification.delta_oee}pts)
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
