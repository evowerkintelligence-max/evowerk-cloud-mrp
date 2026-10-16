import type { UseQueryResult } from "@tanstack/react-query";
import { ShoppingCart } from "lucide-react";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, poStatusVariant } from "@/lib/status";
import { cn } from "@/lib/utils";
import { isLate, type PurchaseOrderResponse } from "./types";

export function PoListTable({
  purchaseOrders,
  selectedPoId,
  setSelectedPoId,
}: {
  purchaseOrders: UseQueryResult<PurchaseOrderResponse[]>;
  selectedPoId: string | null;
  setSelectedPoId: (id: string) => void;
}) {
  return (
    <>
      {purchaseOrders.data && purchaseOrders.data.length === 0 ? (
        <EmptyState icon={<ShoppingCart />} title="No purchase orders yet" />
      ) : (
        <TableWrap className="mb-4">
          <Table>
            <THead>
              <TR>
                <TH>PO #</TH>
                <TH>Supplier</TH>
                <TH>Expected</TH>
                <TH>Status</TH>
              </TR>
            </THead>
            <TBody>
              {purchaseOrders.data?.map((po) => (
                <TR
                  key={po.id}
                  onClick={() => setSelectedPoId(po.id)}
                  className={cn("cursor-pointer", selectedPoId === po.id && "bg-primary/5")}
                >
                  <TD className="font-medium">PO-{po.po_number}</TD>
                  <TD>{po.supplier_name}</TD>
                  <TD className="text-muted-foreground">{po.expected_delivery_date ?? "—"}</TD>
                  <TD>
                    <div className="flex flex-wrap items-center gap-1">
                      <Badge variant={poStatusVariant(po.status)}>{humanize(po.status)}</Badge>
                      {isLate(po) && <Badge variant="danger">Late</Badge>}
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </>
  );
}
