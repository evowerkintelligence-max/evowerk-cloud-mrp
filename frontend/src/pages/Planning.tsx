import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  is_lot_tracked: boolean;
}

interface ForecastResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  period_start: string;
  quantity: string;
  source: string;
  notes: string | null;
  updated_at: string;
}

interface MrpPlanLine {
  item_id: string;
  item_sku: string;
  item_name: string;
  is_manufactured: boolean;
  independent_demand: string;
  dependent_demand: string;
  gross_demand: string;
  on_hand: string;
  on_order: string;
  net_requirement: string;
  suggested_order_quantity: string;
}

interface MrpPlanResponse {
  horizon_start: string;
  horizon_end: string;
  lines: MrpPlanLine[];
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function plusDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function Planning() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  // Forecast entry form
  const [itemId, setItemId] = useState("");
  const [periodStart, setPeriodStart] = useState(today());
  const [quantity, setQuantity] = useState("");
  const [notes, setNotes] = useState("");

  // MRP horizon
  const [horizonStart, setHorizonStart] = useState(today());
  const [horizonEnd, setHorizonEnd] = useState(plusDays(90));

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const forecasts = useQuery<ForecastResponse[]>({
    queryKey: ["forecasts"],
    queryFn: async () => {
      const res = await apiFetch("/forecasts");
      if (!res.ok) throw new Error("Failed to load forecasts");
      return res.json();
    },
  });

  const plan = useQuery<MrpPlanResponse>({
    queryKey: ["mrp-plan", horizonStart, horizonEnd],
    queryFn: async () => {
      const res = await apiFetch(
        `/mrp/plan?horizon_start=${horizonStart}&horizon_end=${horizonEnd}`,
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to run MRP plan");
      }
      return res.json();
    },
  });

  function invalidatePlanning() {
    queryClient.invalidateQueries({ queryKey: ["forecasts"] });
    queryClient.invalidateQueries({ queryKey: ["mrp-plan"] });
  }

  const upsertForecast = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/forecasts", {
        method: "POST",
        body: JSON.stringify({
          item_id: itemId,
          period_start: periodStart,
          quantity,
          notes: notes || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to save forecast");
      }
      return res.json();
    },
    onSuccess: () => {
      setQuantity("");
      setNotes("");
      invalidatePlanning();
    },
    onError: (e: Error) => setError(e.message),
  });

  const deleteForecast = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/forecasts/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to delete forecast");
      }
    },
    onSuccess: invalidatePlanning,
    onError: (e: Error) => setError(e.message),
  });

  function onAddForecast(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!itemId) {
      setError("Pick an item first");
      return;
    }
    upsertForecast.mutate();
  }

  return (
    <div>
      <p>
        <Link to="/">Back to dashboard</Link> · <Link to="/items">Manage items</Link>
      </p>
      <h1>Planning</h1>
      <p>
        Enter demand forecasts per item and period. The plan explodes forecasts of manufactured
        items through their active BOM into component requirements, then nets everything against
        on-hand stock and open purchase orders to show what to build and what to order.
      </p>

      {error && <p role="alert">{error}</p>}

      <section>
        <h2>Add / update a demand forecast</h2>
        <form onSubmit={onAddForecast}>
          <label>
            Item
            <select value={itemId} onChange={(e) => setItemId(e.target.value)} required>
              <option value="">Select an item…</option>
              {items.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.sku} - {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Period start
            <input
              type="date"
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
              required
            />
          </label>
          <label>
            Quantity
            <input
              type="number"
              step="0.0001"
              min="0"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              required
            />
          </label>
          <label>
            Notes (optional)
            <input value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
          <button type="submit" disabled={upsertForecast.isPending}>
            Save forecast
          </button>
        </form>

        <table>
          <thead>
            <tr>
              <th>Item</th>
              <th>Period start</th>
              <th>Quantity</th>
              <th>Source</th>
              <th>Notes</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {forecasts.data?.map((fc) => (
              <tr key={fc.id}>
                <td>
                  {fc.item_sku} - {fc.item_name}
                </td>
                <td>{fc.period_start}</td>
                <td>{fc.quantity}</td>
                <td>{fc.source}</td>
                <td>{fc.notes ?? "-"}</td>
                <td>
                  <button
                    onClick={() => deleteForecast.mutate(fc.id)}
                    disabled={deleteForecast.isPending}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>MRP plan</h2>
        <label>
          Horizon start
          <input
            type="date"
            value={horizonStart}
            onChange={(e) => setHorizonStart(e.target.value)}
          />
        </label>
        <label>
          Horizon end
          <input type="date" value={horizonEnd} onChange={(e) => setHorizonEnd(e.target.value)} />
        </label>

        {plan.isError && <p role="alert">{(plan.error as Error).message}</p>}
        {plan.data && plan.data.lines.length === 0 && (
          <p>No forecast demand in this horizon.</p>
        )}
        {plan.data && plan.data.lines.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>Independent</th>
                <th>Dependent</th>
                <th>Gross demand</th>
                <th>On hand</th>
                <th>On order</th>
                <th>Net requirement</th>
                <th>Suggested action</th>
              </tr>
            </thead>
            <tbody>
              {plan.data.lines.map((line) => {
                const qty = Number(line.suggested_order_quantity);
                const action =
                  qty > 0
                    ? `${line.is_manufactured ? "Build" : "Order"} ${line.suggested_order_quantity}`
                    : "-";
                return (
                  <tr key={line.item_id}>
                    <td>
                      {line.item_sku} - {line.item_name}
                      {line.is_manufactured ? " (make)" : ""}
                    </td>
                    <td>{line.independent_demand}</td>
                    <td>{line.dependent_demand}</td>
                    <td>{line.gross_demand}</td>
                    <td>{line.on_hand}</td>
                    <td>{line.on_order}</td>
                    <td>{line.net_requirement}</td>
                    <td>{action}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
