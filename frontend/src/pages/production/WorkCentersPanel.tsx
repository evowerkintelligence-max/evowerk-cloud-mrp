import { FormEvent, useState } from "react";
import { Cog } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, lifecycleStatusVariant } from "@/lib/status";
import { optionalNumber, useWorkCenters } from "./setupData";

export function WorkCentersPanel() {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [capacity, setCapacity] = useState("1");
  const [cycleTime, setCycleTime] = useState("");
  const [mtbf, setMtbf] = useState("");
  const [mttr, setMttr] = useState("");
  const [error, setError] = useState<string | null>(null);

  const workCenters = useWorkCenters();

  const createWorkCenter = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/work-centers", {
        method: "POST",
        body: JSON.stringify({
          code,
          name,
          machine_capacity: Number(capacity) || 1,
          default_cycle_time_seconds: optionalNumber(cycleTime),
          default_mtbf_hours: optionalNumber(mtbf),
          default_mttr_hours: optionalNumber(mttr),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create work center");
      }
      return res.json();
    },
    onSuccess: () => {
      setCode("");
      setName("");
      setCapacity("1");
      setCycleTime("");
      setMtbf("");
      setMttr("");
      queryClient.invalidateQueries({ queryKey: ["work-centers"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createWorkCenter.mutate();
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add a work center</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Code" htmlFor="wc-code">
              <Input id="wc-code" value={code} onChange={(e) => setCode(e.target.value)} required />
            </Field>
            <Field label="Name" htmlFor="wc-name">
              <Input id="wc-name" value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <Field label="Machines in parallel" htmlFor="wc-capacity">
              <Input
                id="wc-capacity"
                type="number"
                min="1"
                step="1"
                value={capacity}
                onChange={(e) => setCapacity(e.target.value)}
                required
              />
            </Field>
            <Field label="Cycle time (s)" htmlFor="wc-cycle" hint="Optional default for routing steps">
              <Input
                id="wc-cycle"
                type="number"
                min="0"
                step="0.01"
                value={cycleTime}
                onChange={(e) => setCycleTime(e.target.value)}
              />
            </Field>
            <Field label="MTBF (hours)" htmlFor="wc-mtbf" hint="Optional">
              <Input
                id="wc-mtbf"
                type="number"
                min="0"
                step="any"
                value={mtbf}
                onChange={(e) => setMtbf(e.target.value)}
              />
            </Field>
            <Field label="MTTR (hours)" htmlFor="wc-mttr" hint="Optional">
              <Input
                id="wc-mttr"
                type="number"
                min="0"
                step="any"
                value={mttr}
                onChange={(e) => setMttr(e.target.value)}
              />
            </Field>
            <div className="flex items-end">
              <Button type="submit" className="w-full" loading={createWorkCenter.isPending}>
                Add work center
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {workCenters.data && workCenters.data.length === 0 ? (
        <EmptyState
          icon={<Cog />}
          title="No work centers yet"
          description="A work center is a station or cell that production passes through."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Code</TH>
                <TH>Name</TH>
                <TH>Status</TH>
                <TH>Capacity</TH>
                <TH>Cycle time (s)</TH>
                <TH>MTBF (h)</TH>
                <TH>MTTR (h)</TH>
              </TR>
            </THead>
            <TBody>
              {workCenters.data?.map((wc) => (
                <TR key={wc.id}>
                  <TD className="font-medium">{wc.code}</TD>
                  <TD>{wc.name}</TD>
                  <TD>
                    <Badge variant={lifecycleStatusVariant(wc.status)}>{humanize(wc.status)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{wc.machine_capacity}</TD>
                  <TD className="text-muted-foreground">{wc.default_cycle_time_seconds ?? "-"}</TD>
                  <TD className="text-muted-foreground">{wc.default_mtbf_hours ?? "-"}</TD>
                  <TD className="text-muted-foreground">{wc.default_mttr_hours ?? "-"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}
