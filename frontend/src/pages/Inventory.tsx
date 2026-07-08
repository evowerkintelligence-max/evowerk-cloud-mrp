import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface InventoryBalance {
  item_id: string;
  item_sku: string;
  item_name: string;
  unit_of_measure: string;
  quantity_on_hand: string;
}

interface InventoryTransaction {
  id: string;
  transaction_type: string;
  quantity_delta: string;
  reason: string | null;
  created_by: string;
  created_at: string;
}

interface InventoryDetail extends InventoryBalance {
  transactions: InventoryTransaction[];
}

export function Inventory() {
  const queryClient = useQueryClient();
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [consumeQuantity, setConsumeQuantity] = useState("1");
  const [consumeReason, setConsumeReason] = useState("");
  const [adjustDelta, setAdjustDelta] = useState("1");
  const [adjustReason, setAdjustReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const balances = useQuery<InventoryBalance[]>({
    queryKey: ["inventory"],
    queryFn: async () => {
      const res = await apiFetch("/inventory");
      if (!res.ok) throw new Error("Failed to load inventory");
      return res.json();
    },
  });

  const detail = useQuery<InventoryDetail>({
    queryKey: ["inventory", selectedItemId],
    enabled: !!selectedItemId,
    queryFn: async () => {
      const res = await apiFetch(`/inventory/${selectedItemId}`);
      if (!res.ok) throw new Error("Failed to load item inventory");
      return res.json();
    },
  });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["inventory"] });
    queryClient.invalidateQueries({ queryKey: ["inventory", selectedItemId] });
  }

  const consume = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/inventory/${selectedItemId}/consume`, {
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
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  const adjust = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/inventory/${selectedItemId}/adjust`, {
        method: "POST",
        body: JSON.stringify({ quantity: adjustDelta, reason: adjustReason || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to adjust");
      }
      return res.json();
    },
    onSuccess: () => {
      setAdjustDelta("1");
      setAdjustReason("");
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  function onConsume(e: FormEvent) {
    e.preventDefault();
    setError(null);
    consume.mutate();
  }

  function onAdjust(e: FormEvent) {
    e.preventDefault();
    setError(null);
    adjust.mutate();
  }

  return (
    <div>
      <p>
        <Link to="/">Back to dashboard</Link> · <Link to="/items">Manage items</Link> ·{" "}
        <Link to="/purchase-orders">Purchase Orders</Link>
      </p>
      <h1>Inventory</h1>
      <p>
        On-hand stock for non-lot-tracked items. Receiving a purchase order adds stock; use consume
        and adjust to draw it down or correct a count. (Lot-tracked items track on-hand through their
        lots instead.)
      </p>

      {error && <p role="alert">{error}</p>}

      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>On hand</th>
            <th>UoM</th>
          </tr>
        </thead>
        <tbody>
          {balances.data?.map((bal) => (
            <tr
              key={bal.item_id}
              onClick={() => setSelectedItemId(bal.item_id)}
              style={{ cursor: "pointer" }}
            >
              <td>
                {bal.item_sku} - {bal.item_name}
              </td>
              <td>{bal.quantity_on_hand}</td>
              <td>{bal.unit_of_measure}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {detail.data && (
        <section>
          <h2>
            {detail.data.item_sku} - {detail.data.item_name}
          </h2>
          <p>
            On hand: {detail.data.quantity_on_hand} {detail.data.unit_of_measure}
          </p>

          <form onSubmit={onConsume}>
            <label>
              Consume
              <input
                type="number"
                step="0.0001"
                min="0.0001"
                value={consumeQuantity}
                onChange={(e) => setConsumeQuantity(e.target.value)}
                required
              />
            </label>
            <label>
              Reason (optional)
              <input value={consumeReason} onChange={(e) => setConsumeReason(e.target.value)} />
            </label>
            <button type="submit" disabled={consume.isPending}>
              Consume
            </button>
          </form>

          <form onSubmit={onAdjust}>
            <label>
              Adjust by (+/-)
              <input
                type="number"
                step="0.0001"
                value={adjustDelta}
                onChange={(e) => setAdjustDelta(e.target.value)}
                required
              />
            </label>
            <label>
              Reason (optional)
              <input value={adjustReason} onChange={(e) => setAdjustReason(e.target.value)} />
            </label>
            <button type="submit" disabled={adjust.isPending}>
              Adjust
            </button>
          </form>

          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Change</th>
                <th>Reason</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {detail.data.transactions.map((tx) => (
                <tr key={tx.id}>
                  <td>{tx.transaction_type}</td>
                  <td>{tx.quantity_delta}</td>
                  <td>{tx.reason ?? "-"}</td>
                  <td>{new Date(tx.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
