import { FormEvent, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { trimQty } from "../../planning/types";
import type { WorkOrder } from "../types";

export function EditDialog({
  job,
  onClose,
  onDone,
  onError,
}: {
  job: WorkOrder | null;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  // Keyed on the job id at the call site, so these initialise from whichever
  // job the user opened rather than sticking at the first one's values.
  const [quantity, setQuantity] = useState(trimQty(job?.quantity_planned ?? "0"));
  const [start, setStart] = useState(job?.scheduled_start ?? "");
  const [notes, setNotes] = useState(job?.notes ?? "");
  const [operatorId, setOperatorId] = useState(job?.assigned_operator_id ?? "");

  const isScheduled = job?.status === "scheduled";

  const operators = useQuery<{ id: string; name: string }[]>({
    queryKey: ["operators"],
    enabled: job !== null,
    queryFn: async () => {
      const res = await apiFetch("/operators");
      if (!res.ok) throw new Error("Failed to load operators");
      return res.json();
    },
  });

  const save = useMutation({
    mutationFn: async () => {
      if (job === null) return null;
      const body: Record<string, unknown> = { assigned_operator_id: operatorId || null };
      // Every other field 409s on a job that isn't "scheduled" - only send
      // them when the job actually is, so assigning an operator to a running
      // job doesn't trip that guard.
      if (isScheduled) {
        body.quantity_planned = quantity;
        body.scheduled_start = start || null;
        body.notes = notes || null;
      }
      const res = await apiFetch(`/work-orders/${job.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const responseBody = await res.json().catch(() => ({}));
        throw new Error(responseBody.detail ?? "Failed to save the job");
      }
      return res.json();
    },
    onSuccess: onDone,
    onError: (e: Error) => onError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <Dialog open={job !== null} onClose={onClose} title={`Edit WO-${job?.wo_number ?? ""}`}>
      <form onSubmit={onSubmit} className="grid gap-4">
        {isScheduled && (
          <>
            <p className="text-sm text-muted-foreground">
              Changing the quantity recomputes what this job takes, against the recipe as it stands
              now.
            </p>
            <Field label="Quantity" htmlFor="edit-qty">
              <Input
                id="edit-qty"
                type="number"
                min="0"
                step="any"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                required
              />
            </Field>
            <Field label="Start date" htmlFor="edit-start">
              <Input
                id="edit-start"
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </Field>
            <Field label="Notes" htmlFor="edit-notes">
              <Input id="edit-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
          </>
        )}
        <Field
          label="Assigned operator"
          htmlFor="edit-operator"
          hint="Who's running this job, for shops without a badge reader or QR check-in."
        >
          <Select id="edit-operator" value={operatorId} onChange={(e) => setOperatorId(e.target.value)}>
            <option value="">Unassigned</option>
            {operators.data?.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Never mind
          </Button>
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
