import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
}

interface SupplierResponse {
  id: string;
  name: string;
}

interface PurchaseOrderResponse {
  id: string;
  supplier_id: string;
  supplier_name: string;
  po_number: number;
  status: string;
  expected_delivery_date: string | null;
}

interface PurchaseOrderLineResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  quantity_ordered: string;
  quantity_received: string;
  unit_price: string;
  sequence: number;
}

interface PurchaseOrderDetailResponse extends PurchaseOrderResponse {
  lines: PurchaseOrderLineResponse[];
}

const CANCELLABLE_STATUSES = ["draft", "submitted"];
const RECEIVABLE_STATUSES = ["submitted", "partially_received"];

export function PurchaseOrders() {
  const queryClient = useQueryClient();
  const [selectedPoId, setSelectedPoId] = useState<string | null>(null);
  const [newPoSupplierId, setNewPoSupplierId] = useState("");
  const [lineItemId, setLineItemId] = useState("");
  const [lineQuantity, setLineQuantity] = useState("1");
  const [lineUnitPrice, setLineUnitPrice] = useState("0");
  const [receiveLineId, setReceiveLineId] = useState("");
  const [receiveQuantity, setReceiveQuantity] = useState("1");
  const [error, setError] = useState<string | null>(null);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const suppliers = useQuery<SupplierResponse[]>({
    queryKey: ["suppliers"],
    queryFn: async () => {
      const res = await apiFetch("/suppliers");
      if (!res.ok) throw new Error("Failed to load suppliers");
      return res.json();
    },
  });

  const purchaseOrders = useQuery<PurchaseOrderResponse[]>({
    queryKey: ["purchase-orders"],
    queryFn: async () => {
      const res = await apiFetch("/purchase-orders");
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

  const receiveLine = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/purchase-orders/${selectedPoId}/receive`, {
        method: "POST",
        body: JSON.stringify([{ po_line_id: receiveLineId, quantity: receiveQuantity }]),
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
      invalidatePo();
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
      <p>
        <Link to="/">Back to dashboard</Link> · <Link to="/suppliers">Manage suppliers</Link>
      </p>
      <h1>Purchase Orders</h1>

      <form onSubmit={onCreatePo}>
        <label>
          New PO for supplier
          <select value={newPoSupplierId} onChange={(e) => setNewPoSupplierId(e.target.value)} required>
            <option value="" disabled>
              Select a supplier
            </option>
            {suppliers.data?.map((supplier) => (
              <option key={supplier.id} value={supplier.id}>
                {supplier.name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={createPo.isPending}>
          Create draft
        </button>
      </form>

      {error && <p role="alert">{error}</p>}

      <table>
        <thead>
          <tr>
            <th>PO #</th>
            <th>Supplier</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {purchaseOrders.data?.map((po) => (
            <tr key={po.id} onClick={() => setSelectedPoId(po.id)} style={{ cursor: "pointer" }}>
              <td>PO-{po.po_number}</td>
              <td>{po.supplier_name}</td>
              <td>{po.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {poDetail.data && (
        <section>
          <h2>
            PO-{poDetail.data.po_number} - {poDetail.data.supplier_name} ({poDetail.data.status})
          </h2>

          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>Ordered</th>
                <th>Received</th>
                <th>Unit price</th>
              </tr>
            </thead>
            <tbody>
              {poDetail.data.lines.map((line) => (
                <tr key={line.id}>
                  <td>
                    {line.item_sku} - {line.item_name}
                  </td>
                  <td>{line.quantity_ordered}</td>
                  <td>{line.quantity_received}</td>
                  <td>{line.unit_price}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {poDetail.data.status === "draft" && (
            <>
              <form onSubmit={onAddLine}>
                <label>
                  Item
                  <select value={lineItemId} onChange={(e) => setLineItemId(e.target.value)} required>
                    <option value="" disabled>
                      Select an item
                    </option>
                    {items.data?.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.sku} - {item.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Quantity
                  <input
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    value={lineQuantity}
                    onChange={(e) => setLineQuantity(e.target.value)}
                    required
                  />
                </label>
                <label>
                  Unit price
                  <input
                    type="number"
                    step="0.0001"
                    min="0"
                    value={lineUnitPrice}
                    onChange={(e) => setLineUnitPrice(e.target.value)}
                    required
                  />
                </label>
                <button type="submit" disabled={addLine.isPending}>
                  Add line
                </button>
              </form>

              <button onClick={() => submitPo.mutate()} disabled={submitPo.isPending}>
                Submit
              </button>
            </>
          )}

          {RECEIVABLE_STATUSES.includes(poDetail.data.status) && (
            <form onSubmit={onReceive}>
              <label>
                Line
                <select value={receiveLineId} onChange={(e) => setReceiveLineId(e.target.value)} required>
                  <option value="" disabled>
                    Select a line
                  </option>
                  {poDetail.data.lines.map((line) => (
                    <option key={line.id} value={line.id}>
                      {line.item_sku} ({line.quantity_received}/{line.quantity_ordered})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Quantity received
                <input
                  type="number"
                  step="0.0001"
                  min="0.0001"
                  value={receiveQuantity}
                  onChange={(e) => setReceiveQuantity(e.target.value)}
                  required
                />
              </label>
              <button type="submit" disabled={receiveLine.isPending}>
                Receive
              </button>
            </form>
          )}

          {CANCELLABLE_STATUSES.includes(poDetail.data.status) && (
            <button onClick={() => cancelPo.mutate()} disabled={cancelPo.isPending}>
              Cancel
            </button>
          )}
        </section>
      )}
    </div>
  );
}
