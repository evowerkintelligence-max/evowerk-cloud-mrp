import { useEffect, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiFetch } from "../../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import { trimQty, type GridToBuy } from "./types";
import { describeOrderResult, type GeneratePosResult } from "./orderResult";

const URGENCY_DOT: Record<GridToBuy["urgency"], string> = {
  overdue: "bg-destructive",
  // No lead time on file, so there is no real order-by date to judge against -
  // shown distinctly from "later" so it never reads as the least urgent row.
  unknown: "bg-accent",
  soon: "bg-warning",
  later: "bg-muted-foreground",
};

export function ToBuyPanel({ rows, canOrder }: { rows: GridToBuy[]; canOrder: boolean }) {
  const queryClient = useQueryClient();

  // Use the mutation's own data/error rather than duplicating them in local
  // state: react-query already clears `error` on success and `data` on a new
  // attempt (and on error), so a stale success list never sits next to a
  // fresh error from a later click.
  const order = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/mrp/generate-pos", { method: "POST", body: JSON.stringify({}) });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Could not create the purchase orders");
      }
      return (await res.json()) as GeneratePosResult;
    },
    onSuccess: () => {
      justOrdered.current = true;
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
  });

  // A successful order empties `rows` (the drafted quantities count as
  // incoming supply), so the result block must survive that refetch - that's
  // the one rows change we deliberately ignore. Any other change to `rows`
  // (e.g. the user edits a planned quantity and shortfalls repopulate) means
  // the on-screen result is stale and should not sit next to a fresh list.
  const justOrdered = useRef(false);
  const rowsKey = rows.map((row) => row.item_id).join("|");
  useEffect(() => {
    if (justOrdered.current) {
      justOrdered.current = false;
      return;
    }
    order.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowsKey]);

  const error = order.error as Error | null;
  const result = describeOrderResult(order.data);

  if (rows.length === 0 && !result && !error) return null;

  return (
    <Card className="mt-8">
      <CardHeader className="flex-row items-center justify-between gap-4">
        <CardTitle>To buy for the plan above</CardTitle>
        {canOrder && (
          <Button onClick={() => order.mutate()} loading={order.isPending}>
            Order all missing materials
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Alert variant="destructive">{error.message}</Alert>}
        {rows.length > 0 && (
          <ul className="space-y-2">
            {rows.map((row) => (
              <li key={row.item_id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="flex items-center gap-2">
                  <span className={cn("size-2 rounded-full", URGENCY_DOT[row.urgency])} aria-hidden />
                  <span className="font-medium">{row.item_name}</span>
                  <span className="text-xs text-muted-foreground">{row.item_sku}</span>
                </span>
                <span className="text-muted-foreground">
                  {row.supplier_name
                    ? `order ${trimQty(row.quantity)} from ${row.supplier_name}`
                    : `order ${trimQty(row.quantity)}, no supplier set`}
                  {row.order_by ? ` · by ${row.order_by}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
        {result && (
          <div className="space-y-2 rounded-lg border border-border p-4">
            {result.created.length > 0 && (
              <ul className="space-y-1 text-sm">
                {result.created.map((po) => (
                  <li key={po.po_id} className="flex items-center gap-2">
                    <Badge variant="success">Draft PO #{po.po_number}</Badge>
                    {po.supplier_name} · {po.line_count} line{po.line_count === 1 ? "" : "s"}
                  </li>
                ))}
              </ul>
            )}
            {result.showEmptyCreatedMessage && (
              <p className="text-sm text-muted-foreground">Nothing to order.</p>
            )}
            {result.showUnassignedWarning && (
              <Alert variant="warning">
                No supplier set for:{" "}
                {result.unassigned.map((u) => `${u.item_name} (${u.item_sku})`).join(", ")}. Set one
                under <Link to="/items" className="underline">Items</Link> and order again.
              </Alert>
            )}
            {result.showProcurementPointer && (
              <p className="text-sm text-muted-foreground">
                Review and send them under{" "}
                <Link to="/procurement" className="text-primary hover:underline">
                  Procurement
                </Link>
                .
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
