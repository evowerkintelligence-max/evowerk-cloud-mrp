import { FormEvent, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { ItemSelect } from "@/components/ItemSelect";

export function RaiseDialog({
  open,
  onClose,
  onDone,
  onError,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  const [itemId, setItemId] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [start, setStart] = useState("");
  const [notes, setNotes] = useState("");

  const items = useQuery<{ id: string; sku: string; name: string }[]>({
    queryKey: ["items"],
    enabled: open,
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const raise = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/work-orders", {
        method: "POST",
        body: JSON.stringify({
          item_id: itemId,
          quantity_planned: quantity,
          scheduled_start: start || null,
          notes: notes || null,
          // Scheduled, not started. This is the only path that produces a job
          // holding stock for a future date; the grid's Make button always
          // starts immediately.
          start_now: false,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to raise the job");
      }
      return res.json();
    },
    onSuccess: onDone,
    onError: (e: Error) => onError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    raise.mutate();
  }

  return (
    <Dialog open={open} onClose={onClose} title="Raise a job">
      <form onSubmit={onSubmit} className="grid gap-4">
        <p className="text-sm text-muted-foreground">
          The job holds its materials from now until you start it, and the date decides which job
          wins when two need the same stock. Leave the date blank to mean as soon as possible.
        </p>
        <Field label="Product" htmlFor="raise-item">
          <ItemSelect
            id="raise-item"
            items={items.data}
            value={itemId}
            onChange={setItemId}
            required
          />
        </Field>
        <Field label="Quantity" htmlFor="raise-qty">
          <Input
            id="raise-qty"
            type="number"
            min="0"
            step="any"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            required
          />
        </Field>
        <Field label="Start date" htmlFor="raise-start">
          <Input
            id="raise-start"
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </Field>
        <Field label="Notes" htmlFor="raise-notes">
          <Input id="raise-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Never mind
          </Button>
          <Button type="submit" loading={raise.isPending}>
            Raise job
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
