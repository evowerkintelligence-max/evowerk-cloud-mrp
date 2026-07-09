import { FormEvent, useState } from "react";
import { Boxes } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize } from "@/lib/status";

interface LotResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  lot_number: string;
  quantity_received: string;
  quantity_remaining: string;
  status: string;
  expiry_date: string | null;
}

interface LotTransactionResponse {
  id: string;
  transaction_type: string;
  quantity: string;
  reason: string | null;
  created_by: string;
  created_at: string;
}

interface LotDetailResponse extends LotResponse {
  transactions: LotTransactionResponse[];
}

function lotStatusVariant(status: string) {
  if (status === "on_hold") return "warning" as const;
  if (status === "depleted") return "neutral" as const;
  return "success" as const;
}

export function Lots() {
  const queryClient = useQueryClient();
  const [selectedLotId, setSelectedLotId] = useState<string | null>(null);
  const [consumeQuantity, setConsumeQuantity] = useState("1");
  const [consumeReason, setConsumeReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const lots = useQuery<LotResponse[]>({
    queryKey: ["lots"],
    queryFn: async () => {
      const res = await apiFetch("/lots");
      if (!res.ok) throw new Error("Failed to load lots");
      return res.json();
    },
  });

  const lotDetail = useQuery<LotDetailResponse>({
    queryKey: ["lots", selectedLotId],
    enabled: !!selectedLotId,
    queryFn: async () => {
      const res = await apiFetch(`/lots/${selectedLotId}`);
      if (!res.ok) throw new Error("Failed to load lot");
      return res.json();
    },
  });

  function invalidateLot() {
    queryClient.invalidateQueries({ queryKey: ["lots"] });
    queryClient.invalidateQueries({ queryKey: ["lots", selectedLotId] });
  }

  const consumeLot = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/lots/${selectedLotId}/consume`, {
        method: "POST",
        body: JSON.stringify({ quantity: consumeQuantity, reason: consumeReason || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to consume");
      }
      return res.json();
    },
    onSuccess: () => {
      setConsumeQuantity("1");
      setConsumeReason("");
      invalidateLot();
    },
    onError: (e: Error) => setError(e.message),
  });

  const holdLot = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/lots/${selectedLotId}/hold`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to hold lot");
      }
      return res.json();
    },
    onSuccess: invalidateLot,
    onError: (e: Error) => setError(e.message),
  });

  const releaseLot = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/lots/${selectedLotId}/release`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to release lot");
      }
      return res.json();
    },
    onSuccess: invalidateLot,
    onError: (e: Error) => setError(e.message),
  });

  function onConsume(e: FormEvent) {
    e.preventDefault();
    setError(null);
    consumeLot.mutate();
  }

  return (
    <div>
      <PageHeader
        title="Lots"
        description="Lot-tracked stock from received purchase orders. Select a lot to consume, hold, or release it."
      />

      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      {lots.data && lots.data.length === 0 ? (
        <EmptyState
          icon={<Boxes />}
          title="No lots yet"
          description="Lots appear here when you receive a lot-tracked item on a purchase order."
        />
      ) : (
        <TableWrap className="mb-8">
          <Table>
            <THead>
              <TR>
                <TH>Item</TH>
                <TH>Lot #</TH>
                <TH>Received</TH>
                <TH>Remaining</TH>
                <TH>Status</TH>
                <TH>Expiry</TH>
              </TR>
            </THead>
            <TBody>
              {lots.data?.map((lot) => (
                <TR
                  key={lot.id}
                  onClick={() => setSelectedLotId(lot.id)}
                  className={`cursor-pointer ${selectedLotId === lot.id ? "bg-primary/5" : ""}`}
                >
                  <TD className="font-medium">
                    {lot.item_sku} - {lot.item_name}
                  </TD>
                  <TD className="font-mono text-xs">{lot.lot_number}</TD>
                  <TD className="text-muted-foreground">{lot.quantity_received}</TD>
                  <TD>{lot.quantity_remaining}</TD>
                  <TD>
                    <Badge variant={lotStatusVariant(lot.status)}>{humanize(lot.status)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{lot.expiry_date ?? "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      {lotDetail.data && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                {lotDetail.data.item_sku} · Lot {lotDetail.data.lot_number}
                <Badge variant={lotStatusVariant(lotDetail.data.status)}>
                  {humanize(lotDetail.data.status)}
                </Badge>
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {lotDetail.data.quantity_remaining} / {lotDetail.data.quantity_received} remaining
              </p>
            </div>
            {lotDetail.data.status === "on_hold" ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => releaseLot.mutate()}
                loading={releaseLot.isPending}
              >
                Release hold
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => holdLot.mutate()}
                loading={holdLot.isPending}
              >
                Hold
              </Button>
            )}
          </CardHeader>
          <CardContent className="space-y-6">
            {lotDetail.data.status !== "on_hold" && (
              <form onSubmit={onConsume} className="grid gap-4 sm:grid-cols-[160px_1fr_auto] sm:items-end">
                <Field label="Quantity" htmlFor="consume-qty">
                  <Input
                    id="consume-qty"
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    value={consumeQuantity}
                    onChange={(e) => setConsumeQuantity(e.target.value)}
                    required
                  />
                </Field>
                <Field label="Reason (optional)" htmlFor="consume-reason">
                  <Input
                    id="consume-reason"
                    value={consumeReason}
                    onChange={(e) => setConsumeReason(e.target.value)}
                  />
                </Field>
                <Button type="submit" variant="secondary" loading={consumeLot.isPending}>
                  Consume
                </Button>
              </form>
            )}

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
                  {lotDetail.data.transactions.map((tx) => (
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
          </CardContent>
        </Card>
      )}
    </div>
  );
}
