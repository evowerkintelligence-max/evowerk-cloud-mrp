import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { RcaNode } from "./types";
import { j, errorMessage } from "./types";

const NODE_STATUSES = ["hypothesis", "verified", "ruled_out"] as const;

export function CauseTree({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [category, setCategory] = useState("");
  const nodes = useQuery<RcaNode[]>({
    queryKey: ["rca-nodes", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}/nodes`)),
  });
  const taxonomy = useQuery<Record<string, string[]>>({
    queryKey: ["rca-taxonomy"],
    queryFn: () => j(apiFetch("/rca-cases/taxonomy")),
  });

  const addNode = useMutation({
    mutationFn: () =>
      j(
        apiFetch(`/rca-cases/cases/${caseId}/nodes`, {
          method: "POST",
          body: JSON.stringify({ text, node_type: "cause", category: category || null }),
        }),
      ),
    onSuccess: async () => {
      setText("");
      setCategory("");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] }),
        qc.invalidateQueries({ queryKey: ["rca-case", caseId] }),
      ]);
    },
  });

  const patchNode = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<RcaNode> }) =>
      j(apiFetch(`/rca-cases/cases/${caseId}/nodes/${id}`, { method: "PATCH", body: JSON.stringify(patch) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] }),
  });

  const deleteNode = useMutation({
    mutationFn: (id: string) => apiFetch(`/rca-cases/cases/${caseId}/nodes/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] }),
  });

  const suggestions = category ? taxonomy.data?.[category] ?? [] : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cause tree</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Classify each cause with the 6M framework (Machine, Method, Material, Manpower, Measurement, Environment) —
          picking a category surfaces common causes below and feeds the recurring-cause trends on the case list.
        </p>
        <div className="grid gap-2 sm:grid-cols-[10rem_14rem_1fr_auto]">
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Category (optional)</option>
            {Object.keys(taxonomy.data ?? {}).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
          <Select
            value=""
            disabled={!category}
            onChange={(e) => {
              if (e.target.value) setText(e.target.value);
            }}
          >
            <option value="">{category ? "Suggested cause…" : "Pick a category first"}</option>
            {suggestions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
          <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a contributing cause…" />
          <Button disabled={!text} loading={addNode.isPending} onClick={() => addNode.mutate()}>
            Add
          </Button>
        </div>
        {addNode.isError && <Alert variant="destructive">{errorMessage(addNode.error)}</Alert>}
        {patchNode.isError && <Alert variant="destructive">{errorMessage(patchNode.error)}</Alert>}
        {deleteNode.isError && <Alert variant="destructive">{errorMessage(deleteNode.error)}</Alert>}

        <div className="space-y-2">
          {(nodes.data ?? []).map((n) => (
            <div key={n.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{n.text}</span>
                <Badge variant={n.node_type === "root_cause" ? "danger" : n.node_type === "problem" ? "neutral" : "info"}>
                  {n.node_type}
                </Badge>
                {n.category && <Badge variant="indigo">{n.category}</Badge>}
              </div>
              {n.node_type !== "problem" && (
                <div className="flex items-center gap-2">
                  <Select
                    className="h-8 w-36 py-1 text-xs"
                    value={n.status}
                    onChange={(e) => patchNode.mutate({ id: n.id, patch: { status: e.target.value } })}
                  >
                    {NODE_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s.replace("_", " ")}
                      </option>
                    ))}
                  </Select>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      patchNode.mutate({ id: n.id, patch: { node_type: n.node_type === "root_cause" ? "cause" : "root_cause" } })
                    }
                  >
                    {n.node_type === "root_cause" ? "Unmark root cause" : "Mark root cause"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (confirm(`Delete cause "${n.text}"?`)) deleteNode.mutate(n.id);
                    }}
                  >
                    Delete
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
