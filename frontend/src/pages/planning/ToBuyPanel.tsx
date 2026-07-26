import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiFetch } from "../../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import { trimQty, type GridToBuy } from "./types";

interface GeneratePosResult {
  created: { po_id: string; po_number: number; supplier_name: string; line_count: number }[];
  unassigned: { item_id: string; item_sku: string }[];
}

const URGENCY_DOT: Record<GridToBuy["urgency"], string> = {
  overdue: "bg-destructive",
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
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
  });

  if (rows.length === 0) return null;

  const result = order.data;
  const error = order.error as Error | null;

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
        {result && (
          <div className="space-y-2 rounded-lg border border-border p-4">
            {result.created.length > 0 ? (
              <ul className="space-y-1 text-sm">
                {result.created.map((po) => (
                  <li key={po.po_id} className="flex items-center gap-2">
                    <Badge variant="success">Draft PO #{po.po_number}</Badge>
                    {po.supplier_name} · {po.line_count} line{po.line_count === 1 ? "" : "s"}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Nothing to order.</p>
            )}
            {result.unassigned.length > 0 && (
              <Alert variant="warning">
                No supplier set for: {result.unassigned.map((u) => u.item_sku).join(", ")}. Set one
                under <Link to="/items" className="underline">Items</Link> and order again.
              </Alert>
            )}
            <p className="text-sm text-muted-foreground">
              Review and send them under{" "}
              <Link to="/procurement" className="text-primary hover:underline">
                Procurement
              </Link>
              .
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
