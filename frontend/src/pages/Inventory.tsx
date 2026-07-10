import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface LocationResponse {
  id: string;
  code: string;
  name: string;
  is_default: boolean;
}

interface LocationQuantity {
  location_id: string;
  location_code: string;
  location_name: string;
  quantity_on_hand: string;
}

interface InventoryItem {
  item_id: string;
  item_sku: string;
  item_name: string;
  unit_of_measure: string;
  quantity_on_hand: string;
  by_location: LocationQuantity[];
}

interface InventoryTransaction {
  id: string;
  location_code: string;
  transaction_type: string;
  quantity_delta: string;
  reason: string | null;
  created_at: string;
}

interface InventoryDetail extends InventoryItem {
  transactions: InventoryTransaction[];
}

export function Inventory() {
  const queryClient = useQueryClient();
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [consumeQty, setConsumeQty] = useState("1");
  const [consumeLoc, setConsumeLoc] = useState("");
  const [adjustDelta, setAdjustDelta] = useState("1");
  const [adjustLoc, setAdjustLoc] = useState("");
  const [fromLoc, setFromLoc] = useState("");
  const [toLoc, setToLoc] = useState("");
  const [transferQty, setTransferQty] = useState("1");
  const [error, setError] = useState<string | null>(null);

  const balances = useQuery<InventoryItem[]>({
    queryKey: ["inventory"],
    queryFn: async () => {
      const res = await apiFetch("/inventory");
      if (!res.ok) throw new Error("Failed to load inventory");
      return res.json();
    },
  });

  const locations = useQuery<LocationResponse[]>({
    queryKey: ["locations"],
    queryFn: async () => {
      const res = await apiFetch("/locations");
      if (!res.ok) throw new Error("Failed to load locations");
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

  function post(path: string, body: Record<string, unknown>) {
    return async () => {
      const res = await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail ?? "Request failed");
      }
      return res.json();
    };
  }

  const consume = useMutation({
    mutationFn: () =>
      post(`/inventory/${selectedItemId}/consume`, {
        quantity: consumeQty,
        location_id: consumeLoc || null,
      })(),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const adjust = useMutation({
    mutationFn: () =>
      post(`/inventory/${selectedItemId}/adjust`, {
        quantity: adjustDelta,
        location_id: adjustLoc || null,
      })(),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const transfer = useMutation({
    mutationFn: () =>
      post(`/inventory/${selectedItemId}/transfer`, {
        from_location_id: fromLoc,
        to_location_id: toLoc,
        quantity: transferQty,
      })(),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  function onTransfer(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!fromLoc || !toLoc) {
      setError("Pick both a source and destination location");
      return;
    }
    transfer.mutate();
  }

  const locationOptions = (
    <>
      <option value="">Default location</option>
      {locations.data?.map((l) => (
        <option key={l.id} value={l.id}>
          {l.code} - {l.name}
        </option>
      ))}
    </>
  );

  return (
    <div>
      <p>
        <Link to="/">Back to dashboard</Link> · <Link to="/items">Manage items</Link> ·{" "}
        <Link to="/locations">Locations</Link> · <Link to="/purchase-orders">Purchase Orders</Link>
      </p>
      <h1>Inventory</h1>
      <p>
        On-hand stock for non-lot-tracked items, per location. Receiving a purchase order adds stock;
        consume, adjust, or transfer between locations to move it. (Lot-tracked items track on-hand
        through their lots instead.)
      </p>

      {error && <p role="alert">{error}</p>}

      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>Total on hand</th>
            <th>By location</th>
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
              <td>
                {bal.by_location
                  .map((lq) => `${lq.location_code}: ${lq.quantity_on_hand}`)
                  .join(", ")}
              </td>
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
            Total on hand: {detail.data.quantity_on_hand} {detail.data.unit_of_measure}
          </p>

          <table>
            <thead>
              <tr>
                <th>Location</th>
                <th>On hand</th>
              </tr>
            </thead>
            <tbody>
              {detail.data.by_location.map((lq) => (
                <tr key={lq.location_id}>
                  <td>
                    {lq.location_code} - {lq.location_name}
                  </td>
                  <td>{lq.quantity_on_hand}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              consume.mutate();
            }}
          >
            <label>
              Consume
              <input
                type="number"
                step="0.0001"
                min="0.0001"
                value={consumeQty}
                onChange={(e) => setConsumeQty(e.target.value)}
                required
              />
            </label>
            <label>
              at
              <select value={consumeLoc} onChange={(e) => setConsumeLoc(e.target.value)}>
                {locationOptions}
              </select>
            </label>
            <button type="submit" disabled={consume.isPending}>
              Consume
            </button>
          </form>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              adjust.mutate();
            }}
          >
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
              at
              <select value={adjustLoc} onChange={(e) => setAdjustLoc(e.target.value)}>
                {locationOptions}
              </select>
            </label>
            <button type="submit" disabled={adjust.isPending}>
              Adjust
            </button>
          </form>

          <form onSubmit={onTransfer}>
            <label>
              Transfer
              <input
                type="number"
                step="0.0001"
                min="0.0001"
                value={transferQty}
                onChange={(e) => setTransferQty(e.target.value)}
                required
              />
            </label>
            <label>
              from
              <select value={fromLoc} onChange={(e) => setFromLoc(e.target.value)}>
                <option value="">Select…</option>
                {locations.data?.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.code}
                  </option>
                ))}
              </select>
            </label>
            <label>
              to
              <select value={toLoc} onChange={(e) => setToLoc(e.target.value)}>
                <option value="">Select…</option>
                {locations.data?.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.code}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={transfer.isPending}>
              Transfer
            </button>
          </form>

          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Location</th>
                <th>Change</th>
                <th>Reason</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {detail.data.transactions.map((tx) => (
                <tr key={tx.id}>
                  <td>{tx.transaction_type}</td>
                  <td>{tx.location_code}</td>
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
