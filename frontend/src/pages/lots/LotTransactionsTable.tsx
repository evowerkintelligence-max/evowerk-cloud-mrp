import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize } from "@/lib/status";
import type { LotTransactionResponse } from "./types";

export function LotTransactionsTable({ transactions }: { transactions: LotTransactionResponse[] }) {
  return (
    <TableWrap>
      <Table>
        <THead>
          <TR>
            <TH>Type</TH>
            <TH>Quantity</TH>
            <TH>Reason</TH>
            <TH>When</TH>
          </TR>
        </THead>
        <TBody>
          {transactions.map((tx) => (
            <TR key={tx.id}>
              <TD>{humanize(tx.transaction_type)}</TD>
              <TD>{tx.quantity}</TD>
              <TD className="text-muted-foreground">{tx.reason ?? "—"}</TD>
              <TD className="text-muted-foreground">
                {new Date(tx.created_at).toLocaleString()}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </TableWrap>
  );
}
