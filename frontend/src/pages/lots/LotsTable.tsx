import { Boxes } from "lucide-react";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, lotStatusVariant } from "@/lib/status";
import { cn } from "@/lib/utils";
import { ExpiryCell } from "./ExpiryCell";
import type { LotResponse } from "./types";

export function LotsTable({
  visibleLots,
  selectedLotId,
  setSelectedLotId,
}: {
  visibleLots: LotResponse[] | undefined;
  selectedLotId: string | null;
  setSelectedLotId: (id: string) => void;
}) {
  return (
    <>
      {visibleLots && visibleLots.length === 0 ? (
        <EmptyState
          icon={<Boxes />}
          title="No lots found"
          description="No lots match the current filters."
        />
      ) : (
        <TableWrap className="mb-4">
          <Table>
            <THead>
              <TR>
                <TH>Item</TH>
                <TH>Lot #</TH>
                <TH>Location</TH>
                <TH>Received</TH>
                <TH>Remaining</TH>
                <TH>Status</TH>
                <TH>Expiry</TH>
              </TR>
            </THead>
            <TBody>
              {visibleLots?.map((lot) => (
                <TR
                  key={lot.id}
                  onClick={() => setSelectedLotId(lot.id)}
                  className={cn("cursor-pointer", selectedLotId === lot.id && "bg-primary/5")}
                >
                  <TD className="font-medium">
                    {lot.item_sku} - {lot.item_name}
                  </TD>
                  <TD className="font-mono text-xs">{lot.lot_number}</TD>
                  <TD className="text-muted-foreground">{lot.location_code}</TD>
                  <TD className="text-muted-foreground">{lot.quantity_received}</TD>
                  <TD>{lot.quantity_remaining}</TD>
                  <TD>
                    <Badge variant={lotStatusVariant(lot.status)}>{humanize(lot.status)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">
                    <ExpiryCell expiryDate={lot.expiry_date} />
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
