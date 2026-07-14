import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Workflow as WorkflowIcon } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface WorkflowSummary {
  id: string;
  name: string;
  description: string | null;
  steps: unknown[];
  failure_policy: string;
  updated_at: string;
}

export function Workflows() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const workflows = useQuery<WorkflowSummary[]>({
    queryKey: ["workflows"],
    queryFn: async () => {
      const res = await apiFetch("/workflows");
      if (!res.ok) throw new Error("Failed to load workflows");
      return res.json();
    },
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/workflows/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete workflow");
    },
    onSettled: () => {
      setDeletingId(null);
      qc.invalidateQueries({ queryKey: ["workflows"] });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Workflows"
        description="Chain query, dataset and import steps into a run you can trigger and replay."
        actions={<Button onClick={() => navigate("/workflows/new")}>New workflow</Button>}
      />

      {workflows.data && workflows.data.length === 0 ? (
        <EmptyState
          icon={<WorkflowIcon />}
          title="No workflows yet"
          description="Build a DAG of steps — load a dataset, filter it, export the result — and run it on demand."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Steps</TH>
                <TH>Failure policy</TH>
                <TH>Updated</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {workflows.data?.map((w) => (
                <TR key={w.id}>
                  <TD className="font-medium">
                    <Link to={`/workflows/${w.id}`} className="hover:underline">
                      {w.name}
                    </Link>
                    {w.description && <div className="text-xs text-muted-foreground">{w.description}</div>}
                  </TD>
                  <TD className="text-muted-foreground">{w.steps.length}</TD>
                  <TD className="text-muted-foreground">{w.failure_policy}</TD>
                  <TD className="text-muted-foreground">{new Date(w.updated_at).toLocaleString()}</TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="secondary" size="sm" onClick={() => navigate(`/workflows/${w.id}`)}>
                        Open
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        loading={deletingId === w.id && remove.isPending}
                        onClick={() => {
                          setDeletingId(w.id);
                          remove.mutate(w.id);
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

      {workflows.data && workflows.data.length > 0 && (
        <Card className="p-4 text-sm text-muted-foreground">
          Click a workflow to edit its steps, trigger a run, and see run history.
        </Card>
      )}
    </div>
  );
}
