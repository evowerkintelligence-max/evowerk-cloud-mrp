import { FormEvent, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { trimQty } from "../../planning/types";
import type { WorkOrder } from "../types";

export function FinishDialog({
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
  const [good, setGood] = useState("");
  const [scrapped, setScrapped] = useState("0");
  const [scrapReason, setScrapReason] = useState("");
  const [lotNumber, setLotNumber] = useState("");
  const [disposition, setDisposition] = useState("");

  const finish = useMutation({
    mutationFn: async () => {
      if (job === null) return null;
      const res = await apiFetch(`/work-orders/${job.id}/finish`, {
        method: "POST",
        body: JSON.stringify({
          quantity_good: good,
          quantity_scrapped: scrapped || "0",
          scrap_reason: scrapReason || null,
          lot_number: lotNumber || null,
          remainder_disposition: disposition || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to finish the job");
      }
      return res.json();
    },
    onSuccess: () => {
      setGood("");
      setScrapped("0");
      setScrapReason("");
      setLotNumber("");
      setDisposition("");
      onDone();
    },
    onError: (e: Error) => onError(e.message),
  });

  // The backend refuses an under-run with no answer, so asking here is the
  // difference between a question and a 400.
  const madeSoFar = Number(good || 0) + Number(scrapped || 0);
  const isUnderRun = job !== null && madeSoFar < Number(job.quantity_planned);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    finish.mutate();
  }

  return (
    <Dialog open={job !== null} onClose={onClose} title={`Finish WO-${job?.wo_number ?? ""}`}>
      <form onSubmit={onSubmit} className="grid gap-4">
        <p className="text-sm text-muted-foreground">
          {job?.item_name}, planned for {trimQty(job?.quantity_planned ?? "0")}. Good units go into
          stock; scrap does not.
        </p>
        <Field label="Good units" htmlFor="finish-good">
          <Input
            id="finish-good"
            type="number"
            min="0"
            step="any"
            value={good}
            onChange={(e) => setGood(e.target.value)}
            required
          />
        </Field>
        <Field label="Scrapped" htmlFor="finish-scrapped">
          <Input
            id="finish-scrapped"
            type="number"
            min="0"
            step="any"
            value={scrapped}
            onChange={(e) => setScrapped(e.target.value)}
          />
        </Field>
        <Field label="Why was it scrapped?" htmlFor="finish-scrap-reason">
          <Input
            id="finish-scrap-reason"
            value={scrapReason}
            onChange={(e) => setScrapReason(e.target.value)}
            placeholder="Porosity, wrong size…"
          />
        </Field>
        {job?.item_is_lot_tracked && (
          <Field label="Lot number" htmlFor="finish-lot">
            <Input
              id="finish-lot"
              value={lotNumber}
              onChange={(e) => setLotNumber(e.target.value)}
              required
            />
          </Field>
        )}
        {isUnderRun && (
          <Field label="The units you did not make" htmlFor="finish-disposition">
            <Select
              id="finish-disposition"
              value={disposition}
              onChange={(e) => setDisposition(e.target.value)}
              required
            >
              <option value="">Choose one...</option>
              <option value="not_started">Their components are still on the shelf</option>
              <option value="consumed">Their components were used up</option>
            </Select>
          </Field>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Never mind
          </Button>
          <Button type="submit" loading={finish.isPending}>
            Finish job
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
