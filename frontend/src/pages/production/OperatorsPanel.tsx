import { FormEvent, useState } from "react";
import { Users } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, lifecycleStatusVariant } from "@/lib/status";
import type { OperatorResponse } from "./setupData";
import { OperatorPerformancePanel } from "./OperatorPerformancePanel";

function OperatorRow({
  operator,
  others,
  onError,
}: {
  operator: OperatorResponse;
  others: OperatorResponse[];
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [mergeTarget, setMergeTarget] = useState(operator.duplicate_of_id ?? "");

  const merge = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/operators/${operator.id}/merge`, {
        method: "POST",
        body: JSON.stringify({ target_operator_id: mergeTarget }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to merge operators");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operators"] }),
    onError: (e: Error) => onError(e.message),
  });

  const keepBoth = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/operators/${operator.id}`, {
        method: "PATCH",
        body: JSON.stringify({ needs_review: false }),
      });
      if (!res.ok) throw new Error("Failed to update operator");
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operators"] }),
    onError: (e: Error) => onError(e.message),
  });

  return (
    <>
      <TR>
        <TD className="font-medium">{operator.name}</TD>
        <TD className="text-muted-foreground">{operator.code ?? "-"}</TD>
        <TD className="text-muted-foreground">{operator.badge_id ? "Assigned" : "-"}</TD>
        <TD>
          <Badge variant={lifecycleStatusVariant(operator.status)}>{humanize(operator.status)}</Badge>
        </TD>
        <TD>{operator.needs_review ? <Badge variant="warning">Needs review</Badge> : ""}</TD>
      </TR>
      {operator.needs_review && (
        <TR>
          <TD colSpan={5}>
            <div className="flex flex-wrap items-center gap-3 rounded-md bg-amber-50 p-3 text-sm dark:bg-amber-950">
              <span>
                {operator.duplicate_of_name
                  ? `Possible duplicate of ${operator.duplicate_of_name}.`
                  : "Possible duplicate of an existing operator."}
              </span>
              <Select value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)}>
                <option value="" disabled>
                  Merge into...
                </option>
                {others.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </Select>
              <Button size="sm" disabled={!mergeTarget} loading={merge.isPending} onClick={() => merge.mutate()}>
                Merge
              </Button>
              <Button size="sm" variant="secondary" loading={keepBoth.isPending} onClick={() => keepBoth.mutate()}>
                Keep both
              </Button>
            </div>
          </TD>
        </TR>
      )}
    </>
  );
}

export function OperatorsPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [badgeId, setBadgeId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const operators = useQuery<OperatorResponse[]>({
    queryKey: ["operators"],
    queryFn: async () => {
      const res = await apiFetch("/operators");
      if (!res.ok) throw new Error("Failed to load operators");
      return res.json();
    },
  });

  const createOperator = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/operators", {
        method: "POST",
        body: JSON.stringify({ name, code: code.trim() || null, badge_id: badgeId.trim() || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create operator");
      }
      return res.json();
    },
    onSuccess: () => {
      setName("");
      setCode("");
      setBadgeId("");
      queryClient.invalidateQueries({ queryKey: ["operators"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createOperator.mutate();
  }

  const sorted = [...(operators.data ?? [])].sort((a, b) => Number(b.needs_review) - Number(a.needs_review));

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add an operator</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Name" htmlFor="operator-name">
              <Input id="operator-name" value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <Field label="Code" htmlFor="operator-code" hint="Optional">
              <Input id="operator-code" value={code} onChange={(e) => setCode(e.target.value)} />
            </Field>
            <Field label="Badge ID" htmlFor="operator-badge" hint="Optional">
              <Input id="operator-badge" value={badgeId} onChange={(e) => setBadgeId(e.target.value)} />
            </Field>
            <div className="flex items-end">
              <Button type="submit" className="w-full" loading={createOperator.isPending}>
                Add operator
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {operators.data && operators.data.length === 0 ? (
        <EmptyState
          icon={<Users />}
          title="No operators yet"
          description="The roster people are attributed against on production data."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Code</TH>
                <TH>Badge</TH>
                <TH>Status</TH>
                <TH>Review</TH>
              </TR>
            </THead>
            <TBody>
              {sorted.map((o) => (
                <OperatorRow
                  key={o.id}
                  operator={o}
                  others={sorted.filter((other) => other.id !== o.id)}
                  onError={setError}
                />
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <OperatorPerformancePanel />
    </div>
  );
}
