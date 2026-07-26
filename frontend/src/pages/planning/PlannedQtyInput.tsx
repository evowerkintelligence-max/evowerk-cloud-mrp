import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { trimQty, type GridProduct } from "./types";
import { createSaveScheduler, type SaveScheduler } from "./saveScheduler";

// Long enough that typing "120" saves once rather than three times, short
// enough that the colours update while the user is still looking at the row.
const SAVE_DEBOUNCE_MS = 600;

type SavePayload = { quantity: string; needed_by: string | null };

export function PlannedQtyInput({
  product,
  onError,
  onSaved,
  readOnly,
}: {
  product: GridProduct;
  onError: (message: string) => void;
  onSaved: () => void;
  readOnly: boolean;
}) {
  const queryClient = useQueryClient();
  const [qty, setQty] = useState(trimQty(product.planned_qty));
  const [neededBy, setNeededBy] = useState(product.needed_by ?? "");
  const scheduler = useRef<SaveScheduler<SavePayload> | null>(null);

  const save = useMutation({
    // Serializes mutations for this row: two rapid edits queue rather than
    // race, so an older save can never land after a newer one.
    scope: { id: product.item_id },
    mutationFn: async (next: SavePayload) => {
      const res = await apiFetch(`/planning/grid/${product.item_id}`, {
        method: "PUT",
        body: JSON.stringify(next),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Could not save the plan");
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
      onSaved();
    },
    onError: (e: Error) => {
      // The server refused this value, so the row must stop showing a number
      // it never actually saved. Revert to what the server last accepted,
      // unless the user has already re-armed the scheduler with a newer edit
      // (same guard the prop-sync effect below uses), or this overwrites
      // whatever they are currently typing.
      if (!scheduler.current?.isPending()) {
        setQty(trimQty(product.planned_qty));
        setNeededBy(product.needed_by ?? "");
      }
      onError(`${product.item_name}: ${e.message}`);
    },
  });

  if (scheduler.current === null) {
    scheduler.current = createSaveScheduler<SavePayload>((next) => save.mutate(next), SAVE_DEBOUNCE_MS);
  }

  // The row can be re-fetched under us (another save, another user). Adopt the
  // server's value unless the user is mid-edit, which a pending save marks.
  useEffect(() => {
    if (!scheduler.current?.isPending()) {
      setQty(trimQty(product.planned_qty));
      setNeededBy(product.needed_by ?? "");
    }
  }, [product.planned_qty, product.needed_by]);

  function scheduleSave(nextQty: string, nextDate: string) {
    scheduler.current!.schedule({ quantity: nextQty || "0", needed_by: nextDate || null });
  }

  // No save button on this screen, so a pending edit must never be silently
  // discarded: flush it (save now) instead of just cancelling the timer.
  function flush() {
    scheduler.current!.flush();
  }

  useEffect(() => () => flush(), []);

  if (readOnly) {
    return <span>{qty}</span>;
  }

  return (
    <div className="space-y-1">
      <Input
        type="number"
        min="0"
        step="0.0001"
        className="w-24 text-right"
        aria-label={`Planned quantity for ${product.item_sku}`}
        value={qty}
        onChange={(e) => {
          setQty(e.target.value);
          scheduleSave(e.target.value, neededBy);
        }}
        onBlur={flush}
      />
      <Input
        type="date"
        className="w-36"
        aria-label={`Needed by for ${product.item_sku}`}
        value={neededBy}
        onChange={(e) => {
          setNeededBy(e.target.value);
          scheduleSave(qty, e.target.value);
        }}
        onBlur={flush}
      />
      {Number(product.planned_qty) <= 0 && product.suggested_qty !== null && (
        <Button
          variant="ghost"
          size="sm"
          disabled={save.isPending}
          aria-label={`Use suggested quantity for ${product.item_sku}`}
          // Keep focus on the input through mousedown so no blur (and no
          // flush) fires before the click lands. Without this, save.isPending
          // flips true a few ms after the flush and disables this button
          // before mouseup, so the click is dropped and the chip is stuck.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            // Clear the pending timer first: without this, the keystroke the
            // user typed just before clicking still fires after the debounce
            // and overwrites the suggestion this click just accepted.
            scheduler.current!.cancel();
            const suggested = trimQty(product.suggested_qty as string);
            setQty(suggested);
            save.mutate({ quantity: suggested, needed_by: neededBy || null });
          }}
        >
          Use {trimQty(product.suggested_qty)} from history
        </Button>
      )}
    </div>
  );
}
