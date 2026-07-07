import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

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
      <p>
        <Link to="/">Back to dashboard</Link> · <Link to="/items">Manage items</Link>
      </p>
      <h1>Lots</h1>

      {error && <p role="alert">{error}</p>}

      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>Lot #</th>
            <th>Received</th>
            <th>Remaining</th>
            <th>Status</th>
            <th>Expiry</th>
          </tr>
        </thead>
        <tbody>
          {lots.data?.map((lot) => (
            <tr key={lot.id} onClick={() => setSelectedLotId(lot.id)} style={{ cursor: "pointer" }}>
              <td>
                {lot.item_sku} - {lot.item_name}
              </td>
              <td>{lot.lot_number}</td>
              <td>{lot.quantity_received}</td>
              <td>{lot.quantity_remaining}</td>
              <td>{lot.status}</td>
              <td>{lot.expiry_date ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {lotDetail.data && (
        <section>
          <h2>
            {lotDetail.data.item_sku} - Lot {lotDetail.data.lot_number} ({lotDetail.data.status})
          </h2>
          <p>
            {lotDetail.data.quantity_remaining} / {lotDetail.data.quantity_received} remaining
          </p>

          {lotDetail.data.status !== "on_hold" && (
            <form onSubmit={onConsume}>
              <label>
                Quantity
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
              <button type="submit" disabled={consumeLot.isPending}>
                Consume
              </button>
            </form>
          )}

          {lotDetail.data.status === "on_hold" ? (
            <button onClick={() => releaseLot.mutate()} disabled={releaseLot.isPending}>
              Release hold
            </button>
          ) : (
            <button onClick={() => holdLot.mutate()} disabled={holdLot.isPending}>
              Hold
            </button>
          )}

          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Quantity</th>
                <th>Reason</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {lotDetail.data.transactions.map((tx) => (
                <tr key={tx.id}>
                  <td>{tx.transaction_type}</td>
                  <td>{tx.quantity}</td>
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
