import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { trimQty, type GridDetailLine } from "./types";

/** The engine's own numbers. The only place MRP vocabulary is allowed, and
 * only behind an explicit "Show working" click. */
export function ProductDetail({ lines }: { lines: GridDetailLine[] }) {
  if (lines.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing planned for this product yet.</p>;
  }
  return (
    <TableWrap>
      <Table>
        <THead>
          <TR>
            <TH>Item</TH>
            <TH>Needed</TH>
            <TH>In stock</TH>
            <TH>On order</TH>
            <TH>Still needed</TH>
            <TH>Order by</TH>
          </TR>
        </THead>
        <TBody>
          {lines.map((line) => (
            <TR key={line.item_id}>
              <TD className="font-medium">
                {line.item_sku} - {line.item_name}
                {line.is_manufactured && (
                  <Badge variant="accent" className="ml-2">
                    make
                  </Badge>
                )}
              </TD>
              <TD>{trimQty(line.gross_demand)}</TD>
              <TD>{trimQty(line.on_hand)}</TD>
              <TD>{trimQty(String(Number(line.on_order) + Number(line.on_draft)))}</TD>
              <TD className="font-semibold">{trimQty(line.net_requirement)}</TD>
              <TD>{line.suggested_order_date ?? "-"}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </TableWrap>
  );
}
