import { useState } from "react";
import { ChevronDown, ChevronRight, Route as RouteIcon } from "lucide-react";
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
import { ItemSelect } from "@/components/ItemSelect";
import {
  optionalNumber,
  useWorkCenters,
  type ItemResponse,
  type RoutingResponse,
  type WorkCenterResponse,
} from "./setupData";

// POST /routings/{id}/steps replaces the whole step list, so "add a step" sends
// the existing steps back alongside the new one.
function RoutingRow({
  routing,
  workCenters,
  itemLabel,
  onError,
}: {
  routing: RoutingResponse;
  workCenters: WorkCenterResponse[];
  itemLabel: string;
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [stepWorkCenterId, setStepWorkCenterId] = useState("");
  const [cycleTime, setCycleTime] = useState("");
  const [setupTime, setSetupTime] = useState("");

  const addStep = useMutation({
    mutationFn: async () => {
      const nextSequence = Math.max(0, ...routing.steps.map((s) => s.sequence)) + 1;
      const steps = [
        ...routing.steps.map((s) => ({
          sequence: s.sequence,
          work_center_id: s.work_center_id,
          cycle_time_seconds: s.cycle_time_seconds,
          setup_time_seconds: s.setup_time_seconds,
        })),
        {
          sequence: nextSequence,
          work_center_id: stepWorkCenterId,
          cycle_time_seconds: optionalNumber(cycleTime),
          setup_time_seconds: optionalNumber(setupTime),
        },
      ];
      const res = await apiFetch(`/routings/${routing.id}/steps`, {
        method: "POST",
        body: JSON.stringify(steps),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to add step");
      }
      return res.json();
    },
    onSuccess: () => {
      setStepWorkCenterId("");
      setCycleTime("");
      setSetupTime("");
      queryClient.invalidateQueries({ queryKey: ["routings"] });
    },
    onError: (e: Error) => onError(e.message),
  });

  const activate = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/routings/${routing.id}/activate`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to activate routing");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["routings"] }),
    onError: (e: Error) => onError(e.message),
  });

  const workCenterLabel = (id: string) => {
    const wc = workCenters.find((w) => w.id === id);
    return wc ? `${wc.code} - ${wc.name}` : id;
  };

  return (
    <>
      <TR>
        <TD className="w-6">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-muted-foreground hover:text-foreground"
            title={expanded ? "Collapse" : "Show steps"}
          >
            {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </button>
        </TD>
        <TD className="font-medium">{itemLabel}</TD>
        <TD className="text-muted-foreground">v{routing.version}</TD>
        <TD>
          <Badge variant={lifecycleStatusVariant(routing.status)}>{humanize(routing.status)}</Badge>
        </TD>
        <TD className="text-muted-foreground">{routing.steps.length}</TD>
        <TD>
          {routing.status === "draft" && (
            <Button size="sm" variant="secondary" loading={activate.isPending} onClick={() => activate.mutate()}>
              Activate
            </Button>
          )}
        </TD>
      </TR>
      {expanded && (
        <TR>
          <TD colSpan={6}>
            <div className="space-y-4 py-2">
              {routing.steps.length === 0 ? (
                <p className="text-sm text-muted-foreground">No steps yet.</p>
              ) : (
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Seq</TH>
                        <TH>Work center</TH>
                        <TH>Cycle time (s)</TH>
                        <TH>Setup time (s)</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {routing.steps.map((s) => (
                        <TR key={s.id}>
                          <TD className="font-medium">{s.sequence}</TD>
                          <TD>{workCenterLabel(s.work_center_id)}</TD>
                          <TD className="text-muted-foreground">{s.cycle_time_seconds ?? "-"}</TD>
                          <TD className="text-muted-foreground">{s.setup_time_seconds ?? "-"}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              )}

              {routing.status === "draft" && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    addStep.mutate();
                  }}
                  className="grid gap-4 sm:grid-cols-[1fr_160px_160px_auto] sm:items-end"
                >
                  <Field label="Work center" htmlFor={`step-wc-${routing.id}`}>
                    <Select
                      id={`step-wc-${routing.id}`}
                      value={stepWorkCenterId}
                      onChange={(e) => setStepWorkCenterId(e.target.value)}
                      required
                    >
                      <option value="" disabled>
                        Select a work center
                      </option>
                      {workCenters.map((wc) => (
                        <option key={wc.id} value={wc.id}>
                          {wc.code} - {wc.name}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Cycle time (s)" htmlFor={`step-cycle-${routing.id}`}>
                    <Input
                      id={`step-cycle-${routing.id}`}
                      type="number"
                      min="0"
                      step="0.01"
                      value={cycleTime}
                      onChange={(e) => setCycleTime(e.target.value)}
                    />
                  </Field>
                  <Field label="Setup time (s)" htmlFor={`step-setup-${routing.id}`}>
                    <Input
                      id={`step-setup-${routing.id}`}
                      type="number"
                      min="0"
                      step="0.01"
                      value={setupTime}
                      onChange={(e) => setSetupTime(e.target.value)}
                    />
                  </Field>
                  <Button type="submit" variant="secondary" loading={addStep.isPending}>
                    Add step
                  </Button>
                </form>
              )}
            </div>
          </TD>
        </TR>
      )}
    </>
  );
}

export function RoutingsPanel() {
  const queryClient = useQueryClient();
  const [itemId, setItemId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const workCenters = useWorkCenters();
  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });
  const routings = useQuery<RoutingResponse[]>({
    queryKey: ["routings"],
    queryFn: async () => {
      const res = await apiFetch("/routings");
      if (!res.ok) throw new Error("Failed to load routings");
      return res.json();
    },
  });

  // item_id is a query param on this endpoint, not a body field.
  const createRouting = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/routings?item_id=${encodeURIComponent(itemId)}`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "Failed to create routing");
      }
      return res.json();
    },
    onSuccess: () => {
      setItemId("");
      queryClient.invalidateQueries({ queryKey: ["routings"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const itemLabel = (id: string) => {
    const item = items.data?.find((i) => i.id === id);
    return item ? `${item.sku} - ${item.name}` : id;
  };

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Create a draft routing</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              createRouting.mutate();
            }}
            className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end"
          >
            <Field label="For item" htmlFor="routing-item">
              <ItemSelect id="routing-item" items={items.data} value={itemId} onChange={setItemId} required />
            </Field>
            <Button type="submit" loading={createRouting.isPending}>
              Create draft
            </Button>
          </form>
        </CardContent>
      </Card>

      {routings.data && routings.data.length === 0 ? (
        <EmptyState
          icon={<RouteIcon />}
          title="No routings yet"
          description="A routing is the ordered list of work centers an item passes through."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH />
                <TH>Item</TH>
                <TH>Version</TH>
                <TH>Status</TH>
                <TH>Steps</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {routings.data?.map((routing) => (
                <RoutingRow
                  key={routing.id}
                  routing={routing}
                  workCenters={workCenters.data ?? []}
                  itemLabel={itemLabel(routing.item_id)}
                  onError={setError}
                />
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}
