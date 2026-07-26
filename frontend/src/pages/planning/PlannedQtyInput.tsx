import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { trimQty, type GridProduct } from "./types";

// Long enough that typing "120" saves once rather than three times, short
// enough that the colours update while the user is still looking at the row.
const SAVE_DEBOUNCE_MS = 600;

export function PlannedQtyInput({
  product,
  onError,
}: {
  product: GridProduct;
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [qty, setQty] = useState(trimQty(product.planned_qty));
  const [neededBy, setNeededBy] = useState(product.needed_by ?? "");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The row can be re-fetched under us (another save, another user). Adopt the
  // server's value unless the user is mid-edit, which the pending timer marks.
  useEffect(() => {
    if (timer.current === null) {
      setQty(trimQty(product.planned_qty));
      setNeededBy(product.needed_by ?? "");
    }
  }, [product.planned_qty, product.needed_by]);

  const save = useMutation({
    mutationFn: async (next: { quantity: string; needed_by: string | null }) => {
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["planning-grid"] }),
    onError: (e: Error) => onError(e.message),
  });

  function scheduleSave(nextQty: string, nextDate: string) {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      save.mutate({ quantity: nextQty || "0", needed_by: nextDate || null });
    }, SAVE_DEBOUNCE_MS);
  }

  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
  }, []);

  return (
    <div className="space-y-1">
      <Input
        type="number"
        min="0"
        step="0.0001"
        className="w-24 text-right"
        aria-label={`Planned quantity for ${product.item_name}`}
        value={qty}
        onChange={(e) => {
          setQty(e.target.value);
          scheduleSave(e.target.value, neededBy);
        }}
      />
      <Input
        type="date"
        className="w-36"
        aria-label={`Needed by for ${product.item_name}`}
        value={neededBy}
        onChange={(e) => {
          setNeededBy(e.target.value);
          scheduleSave(qty, e.target.value);
        }}
      />
      {Number(product.planned_qty) <= 0 && product.suggested_qty !== null && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
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
