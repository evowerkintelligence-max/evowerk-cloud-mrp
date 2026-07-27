import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { trimQty, type GridProduct } from "./types";
import { createSaveScheduler, type SaveScheduler } from "./saveScheduler";
import { shouldWarnBeforeCollapse } from "./collapseWarning";

// Long enough that typing "120" saves once rather than three times, short
// enough that the colours update while the user is still looking at the row.
const SAVE_DEBOUNCE_MS = 600;

type SavePayload = { quantity: string; needed_by: string | null };

/** The prompt shown instead of saving when a product planned across more
 * than one date would collapse to a single date. The decision of whether to
 * show this lives in collapseWarning.ts; this is only the copy and buttons,
 * kept as its own component so it can be render-tested with plain props. */
export function CollapseConfirm({
  bucketCount,
  pending,
  onConfirm,
  onCancel,
}: {
  bucketCount: number;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-1 rounded-md border border-warning/40 bg-warning/5 p-2 text-xs">
      <p>
        This plan is spread across {bucketCount} dates. Saving one number will replace all of
        them with a single date. This cannot be undone.
      </p>
      <div className="flex gap-2">
        <Button type="button" size="sm" disabled={pending} onClick={onConfirm}>
          Save one date
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

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
  const [confirmingCollapse, setConfirmingCollapse] = useState(false);
  // Sticky for the life of this row: once the user has agreed to collapse
  // the multi-date plan, further edits in this session should not ask again.
  const collapseConfirmed = useRef(false);
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

  const make = useMutation({
    mutationFn: async (quantity: number) => {
      const res = await apiFetch("/work-orders", {
        method: "POST",
        body: JSON.stringify({
          item_id: product.item_id,
          quantity_planned: String(quantity),
          start_now: true,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // The backend's 409 names the short component, the location and the
        // job holding it, and is written to be read by the user.
        throw new Error(body.detail ?? "Could not raise the job");
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
      queryClient.invalidateQueries({ queryKey: ["work-orders"] });
      onSaved();
    },
    onError: (e: Error) => onError(`${product.item_name}: ${e.message}`),
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
    const quantity = nextQty || "0";
    const needed_by = nextDate || null;

    if (Number(quantity) > 0 && !needed_by && product.planned_bucket_count === 0) {
      // Nothing planned yet and no date given: the server will reject this
      // with a 400, so don't send it. The date field's own hint below asks
      // for a date instead, and saves as soon as one is picked.
      scheduler.current!.cancel();
      return;
    }

    if (
      shouldWarnBeforeCollapse({
        plannedBucketCount: product.planned_bucket_count,
        quantityChanged: trimQty(quantity) !== trimQty(product.planned_qty),
        alreadyConfirmed: collapseConfirmed.current,
      })
    ) {
      scheduler.current!.cancel();
      setConfirmingCollapse(true);
      return;
    }

    setConfirmingCollapse(false);
    scheduler.current!.schedule({ quantity, needed_by });
  }

  // No save button on this screen, so a pending edit must never be silently
  // discarded: flush it (save now) instead of just cancelling the timer.
  function flush() {
    scheduler.current!.flush();
  }

  useEffect(() => () => flush(), []);

  // Missing a date a customer was already promised is a different problem
  // from missing an upcoming one, so the late case says so rather than
  // reading like any other ordered line.
  const committed = Number(product.committed_qty) > 0 && (
    <p
      className={
        product.committed_overdue ? "text-xs text-destructive" : "text-xs text-muted-foreground"
      }
    >
      {trimQty(product.committed_qty)} ordered{product.committed_overdue ? ", late" : ""}
    </p>
  );

  // Rendered in BOTH branches below: a member watching the floor has as much
  // reason to know a job is already running as the person who typed the
  // number. The committed line above was missed that way once already.
  const inProduction = Number(product.in_production) > 0 && (
    <p className="text-xs text-muted-foreground">
      {trimQty(product.in_production)} in production
    </p>
  );

  if (readOnly) {
    return (
      <div className="space-y-1">
        <span>{qty}</span>
        {committed}
        {inProduction}
      </div>
    );
  }

  // What a job raised from this row would be for: what is planned, less what
  // jobs are already making. The backend's can_make judges the components
  // against exactly this figure.
  const outstanding = Math.max(0, Number(product.planned_qty) - Number(product.in_production));

  // Nothing planned for this product yet, and the user has typed a quantity
  // above 0 with no date chosen: the row must not silently fail to save.
  const dateRequired = Number(qty) > 0 && !neededBy && product.planned_bucket_count === 0;
  const dateHintId = `needed-by-hint-${product.item_id}`;

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
        aria-invalid={dateRequired}
        aria-describedby={dateRequired ? dateHintId : undefined}
        value={neededBy}
        onChange={(e) => {
          setNeededBy(e.target.value);
          scheduleSave(qty, e.target.value);
        }}
        onBlur={flush}
      />
      {committed}
      {inProduction}
      {Number(product.planned_qty) > 0 &&
        (outstanding <= 0 ? (
          <p className="text-xs text-muted-foreground">A job is covering this row.</p>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            disabled={!product.can_make || make.isPending}
            aria-label={`Make ${outstanding} of ${product.item_sku}`}
            // Same reason as the suggestion chip below: keep focus through
            // mousedown so no blur-flush disables this button before mouseup
            // and swallows the click.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => make.mutate(outstanding)}
          >
            Make {outstanding}
          </Button>
        ))}
      {dateRequired && (
        <p id={dateHintId} className="text-xs text-warning">
          Pick a date to save this quantity.
        </p>
      )}
      {confirmingCollapse && (
        <CollapseConfirm
          bucketCount={product.planned_bucket_count}
          pending={save.isPending}
          onConfirm={() => {
            collapseConfirmed.current = true;
            setConfirmingCollapse(false);
            save.mutate({ quantity: qty || "0", needed_by: neededBy || null });
          }}
          onCancel={() => {
            setConfirmingCollapse(false);
            setQty(trimQty(product.planned_qty));
            setNeededBy(product.needed_by ?? "");
          }}
        />
      )}
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
            if (Number(suggested) > 0 && !neededBy && product.planned_bucket_count === 0) {
              // Same rule as typing: nothing planned yet needs a date before
              // this can be saved. The hint below the date field takes over.
              return;
            }
            save.mutate({ quantity: suggested, needed_by: neededBy || null });
          }}
        >
          Use {trimQty(product.suggested_qty)} from history
        </Button>
      )}
    </div>
  );
}
