import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { ItemSelect } from "@/components/ItemSelect";
import { humanize, poStatusVariant } from "@/lib/status";
import {
  CANCELLABLE_STATUSES,
  PAGE_SIZE,
  RECEIVABLE_STATUSES,
  isLate,
  type ItemResponse,
  type PurchaseOrderDetailResponse,
  type PurchaseOrderResponse,
  type SupplierResponse,
} from "./purchase-orders/types";
import { PoListTable } from "./purchase-orders/PoListTable";
import { PoConfirmDialogs } from "./purchase-orders/PoConfirmDialogs";

export function PurchaseOrdersPanel() {
  const queryClient = useQueryClient();
  const [selectedPoId, setSelectedPoId] = useState<string | null>(null);
  const [newPoSupplierId, setNewPoSupplierId] = useState("");
  const [lineItemId, setLineItemId] = useState("");
  const [lineQuantity, setLineQuantity] = useState("1");
  const [lineUnitPrice, setLineUnitPrice] = useState("0");
  const [receiveLineId, setReceiveLineId] = useState("");
  const [receiveQuantity, setReceiveQuantity] = useState("1");
  const [receiveQuantityRejected, setReceiveQuantityRejected] = useState("0");
  const [receiveRejectReason, setReceiveRejectReason] = useState("");
  const [receiveLotNumber, setReceiveLotNumber] = useState("");
  const [receiveExpiryDate, setReceiveExpiryDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [confirmSubmit, setConfirmSubmit] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  // active_only: a cancelled/replaced vendor shouldn't be pickable for a new PO.
  const suppliers = useQuery<SupplierResponse[]>({
    queryKey: ["suppliers", "active"],
    queryFn: async () => {
      const res = await apiFetch("/suppliers?active_only=true");
      if (!res.ok) throw new Error("Failed to load suppliers");
      return res.json();
    },
  });

  const purchaseOrders = useQuery<PurchaseOrderResponse[]>({
    queryKey: ["purchase-orders", search, page],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
      if (search) params.set("q", search);
      const res = await apiFetch(`/purchase-orders?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load purchase orders");
      return res.json();
    },
  });

  const poDetail = useQuery<PurchaseOrderDetailResponse>({
    queryKey: ["purchase-orders", selectedPoId],
    enabled: !!selectedPoId,
    queryFn: async () => {
      const res = await apiFetch(`/purchase-orders/${selectedPoId}`);
      if (!res.ok) throw new Error("Failed to load purchase order");
      return res.json();
    },
  });

  function invalidatePo() {
    queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    queryClient.invalidateQueries({ queryKey: ["purchase-orders", selectedPoId] });
  }

  const createPo = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/purchase-orders", {
        method: "POST",
        body: JSON.stringify({ supplier_id: newPoSupplierId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create purchase order");
      }
      return res.json() as Promise<PurchaseOrderResponse>;
    },
    onSuccess: (po) => {
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      setSelectedPoId(po.id);
    },
    onError: (e: Error) => setError(e.message),
  });

  const addLine = useMutation({
    mutationFn: async () => {
      if (!poDetail.data) throw new Error("No purchase order selected");
      const existingLines = poDetail.data.lines.map((l) => ({
        item_id: l.item_id,
        quantity_ordered: l.quantity_ordered,
        unit_price: l.unit_price,
        sequence: l.sequence,
      }));
      const res = await apiFetch(`/purchase-orders/${selectedPoId}`, {
        method: "PATCH",
        body: JSON.stringify([
          ...existingLines,
          {
            item_id: lineItemId,
            quantity_ordered: lineQuantity,
            unit_price: lineUnitPrice,
            sequence: existingLines.length,
          },
        ]),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update lines");
      }
      return res.json();
    },
    onSuccess: () => {
      setLineItemId("");
      setLineQuantity("1");
      setLineUnitPrice("0");
      invalidatePo();
    },
    onError: (e: Error) => setError(e.message),
  });

  const submitPo = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/purchase-orders/${selectedPoId}/submit`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to submit purchase order");
      }
      return res.json();
    },
    onSuccess: invalidatePo,
    onError: (e: Error) => setError(e.message),
  });

  const receivingLineIsLotTracked =
    poDetail.data?.lines.find((l) => l.id === receiveLineId)?.item_is_lot_tracked ?? false;

  const receiveLine = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/purchase-orders/${selectedPoId}/receive`, {
        method: "POST",
        body: JSON.stringify([
          {
            po_line_id: receiveLineId,
            quantity: receiveQuantity,
            quantity_rejected: receiveQuantityRejected || "0",
            reject_reason: receiveRejectReason || null,
            lot_number: receivingLineIsLotTracked ? receiveLotNumber : null,
            expiry_date: receivingLineIsLotTracked && receiveExpiryDate ? receiveExpiryDate : null,
          },
        ]),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to receive");
      }
      return res.json();
    },
    onSuccess: () => {
      setReceiveLineId("");
      setReceiveQuantity("1");
      setReceiveQuantityRejected("0");
      setReceiveRejectReason("");
      setReceiveLotNumber("");
      setReceiveExpiryDate("");
      invalidatePo();
      queryClient.invalidateQueries({ queryKey: ["lots"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const cancelPo = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/purchase-orders/${selectedPoId}/cancel`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to cancel purchase order");
      }
      return res.json();
    },
    onSuccess: invalidatePo,
    onError: (e: Error) => setError(e.message),
  });

  function onCreatePo(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createPo.mutate();
  }

  function onAddLine(e: FormEvent) {
    e.preventDefault();
    setError(null);
    addLine.mutate();
  }

  function onReceive(e: FormEvent) {
    e.preventDefault();
    setError(null);
    receiveLine.mutate();
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Create a draft PO</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onCreatePo} className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
            <Field label="For supplier" htmlFor="po-supplier">
              <Select
                id="po-supplier"
                value={newPoSupplierId}
                onChange={(e) => setNewPoSupplierId(e.target.value)}
                required
              >
                <option value="" disabled>
                  Select a supplier
                </option>
                {suppliers.data?.map((supplier) => (
                  <option key={supplier.id} value={supplier.id}>
                    {supplier.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Button type="submit" loading={createPo.isPending}>
              Create draft
            </Button>
          </form>
        </CardContent>
      </Card>

      <div className="mb-4 max-w-xs">
        <Input
          placeholder="Search PO # or supplier…"
          value={search}
          onChange={(e) => {
            setPage(0);
            setSearch(e.target.value);
          }}
        />
      </div>

      <PoListTable purchaseOrders={purchaseOrders} selectedPoId={selectedPoId} setSelectedPoId={setSelectedPoId} />

      <div className="mb-8 flex items-center justify-between">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          Previous
        </Button>
        <span className="text-sm text-muted-foreground">Page {page + 1}</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!purchaseOrders.data || purchaseOrders.data.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </Button>
      </div>

      {poDetail.data && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              PO-{poDetail.data.po_number} · {poDetail.data.supplier_name}
              <Badge variant={poStatusVariant(poDetail.data.status)}>
                {humanize(poDetail.data.status)}
              </Badge>
              {isLate(poDetail.data) && <Badge variant="danger">Late</Badge>}
            </CardTitle>
            <div className="flex items-center gap-2">
              {poDetail.data.status === "draft" && (
                <Button variant="secondary" size="sm" onClick={() => setConfirmSubmit(true)}>
                  Submit
                </Button>
              )}
              {CANCELLABLE_STATUSES.includes(poDetail.data.status) && (
                <Button variant="destructive" size="sm" onClick={() => setConfirmCancel(true)}>
                  Cancel
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Item</TH>
                    <TH>Ordered</TH>
                    <TH>Received</TH>
                    <TH>Unit price</TH>
                  </TR>
                </THead>
                <TBody>
                  {poDetail.data.lines.map((line) => (
                    <TR key={line.id}>
                      <TD className="font-medium">
                        {line.item_sku} - {line.item_name}
                      </TD>
                      <TD>{line.quantity_ordered}</TD>
                      <TD>{line.quantity_received}</TD>
                      <TD className="text-muted-foreground">{line.unit_price}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>

            {poDetail.data.status === "draft" && (
              <form
                onSubmit={onAddLine}
                className="grid gap-4 sm:grid-cols-[1fr_140px_140px_auto] sm:items-end"
              >
                <Field label="Item" htmlFor="po-item">
                  <ItemSelect
                    id="po-item"
                    items={items.data}
                    value={lineItemId}
                    onChange={setLineItemId}
                    required
                  />
                </Field>
                <Field label="Quantity" htmlFor="po-qty">
                  <Input
                    id="po-qty"
                    type="number"
                    step="any"
                    min="0.0001"
                    value={lineQuantity}
                    onChange={(e) => setLineQuantity(e.target.value)}
                    required
                  />
                </Field>
                <Field label="Unit price" htmlFor="po-price">
                  <Input
                    id="po-price"
                    type="number"
                    step="0.0001"
                    min="0"
                    value={lineUnitPrice}
                    onChange={(e) => setLineUnitPrice(e.target.value)}
                    required
                  />
                </Field>
                <Button type="submit" variant="secondary" loading={addLine.isPending}>
                  Add line
                </Button>
              </form>
            )}

            {RECEIVABLE_STATUSES.includes(poDetail.data.status) && (
              <form
                onSubmit={onReceive}
                className="space-y-4 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Receive stock</p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Line" htmlFor="rcv-line">
                    <Select
                      id="rcv-line"
                      value={receiveLineId}
                      onChange={(e) => setReceiveLineId(e.target.value)}
                      required
                    >
                      <option value="" disabled>
                        Select a line
                      </option>
                      {poDetail.data.lines.map((line) => (
                        <option key={line.id} value={line.id}>
                          {line.item_sku} ({line.quantity_received}/{line.quantity_ordered})
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Quantity received" htmlFor="rcv-qty">
                    <Input
                      id="rcv-qty"
                      type="number"
                      step="any"
                      min="0.0001"
                      value={receiveQuantity}
                      onChange={(e) => setReceiveQuantity(e.target.value)}
                      required
                    />
                  </Field>
                  <Field label="Rejected quantity" htmlFor="rcv-qty-rejected">
                    <Input
                      id="rcv-qty-rejected"
                      type="number"
                      step="any"
                      min="0"
                      max={receiveQuantity || undefined}
                      value={receiveQuantityRejected}
                      onChange={(e) => setReceiveQuantityRejected(e.target.value)}
                    />
                  </Field>
                  <Field label="Reject reason (optional)" htmlFor="rcv-reject-reason">
                    <Input
                      id="rcv-reject-reason"
                      value={receiveRejectReason}
                      onChange={(e) => setReceiveRejectReason(e.target.value)}
                    />
                  </Field>
                  {receivingLineIsLotTracked && (
                    <>
                      <Field label="Lot number" htmlFor="rcv-lot">
                        <Input
                          id="rcv-lot"
                          value={receiveLotNumber}
                          onChange={(e) => setReceiveLotNumber(e.target.value)}
                          required
                        />
                      </Field>
                      <Field label="Expiry date (optional)" htmlFor="rcv-exp">
                        <Input
                          id="rcv-exp"
                          type="date"
                          value={receiveExpiryDate}
                          onChange={(e) => setReceiveExpiryDate(e.target.value)}
                        />
                      </Field>
                    </>
                  )}
                </div>
                <Button type="submit" loading={receiveLine.isPending}>
                  Receive
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      )}

      <PoConfirmDialogs
        poDetail={poDetail}
        confirmSubmit={confirmSubmit}
        setConfirmSubmit={setConfirmSubmit}
        submitPo={submitPo}
        confirmCancel={confirmCancel}
        setConfirmCancel={setConfirmCancel}
        cancelPo={cancelPo}
      />
    </div>
  );
}
