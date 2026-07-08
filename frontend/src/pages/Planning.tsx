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

interface SupplierResponse {
  id: string;
  name: string;
}

interface GeneratedPo {
  po_id: string;
  po_number: number;
  supplier_name: string;
  lines: { item_id: string }[];
}

interface AutoGenerateResult {
  created: { po_id: string; po_number: number; supplier_name: string; line_count: number }[];
  unassigned: { item_id: string; item_sku: string; item_name: string; net_requirement: string }[];
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

  // Draft-PO generation from the plan
  const [poSupplierId, setPoSupplierId] = useState("");
  const [selectedItems, setSelectedItems] = useState<Record<string, boolean>>({});
  const [generatedPo, setGeneratedPo] = useState<GeneratedPo | null>(null);
  const [autoResult, setAutoResult] = useState<AutoGenerateResult | null>(null);

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

  const suppliers = useQuery<SupplierResponse[]>({
    queryKey: ["suppliers"],
    queryFn: async () => {
      const res = await apiFetch("/suppliers");
      if (!res.ok) throw new Error("Failed to load suppliers");
      return res.json();
    },
  });

  // Only purchased items with a real shortfall can go on a PO.
  const orderableItemIds = (plan.data?.lines ?? [])
    .filter((line) => !line.is_manufactured && Number(line.net_requirement) > 0)
    .map((line) => line.item_id);
  const chosenItemIds = orderableItemIds.filter((id) => selectedItems[id]);

  const generatePo = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/mrp/generate-po", {
        method: "POST",
        body: JSON.stringify({
          supplier_id: poSupplierId,
          item_ids: chosenItemIds,
          horizon_start: horizonStart,
          horizon_end: horizonEnd,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create draft PO");
      }
      return (await res.json()) as GeneratedPo;
    },
    onSuccess: (po) => {
      setGeneratedPo(po);
      setSelectedItems({});
      // The new draft counts as on-order once submitted; refresh so the plan
      // and PO list stay in sync.
      queryClient.invalidateQueries({ queryKey: ["mrp-plan"] });
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onGeneratePo() {
    setError(null);
    setGeneratedPo(null);
    if (!poSupplierId) {
      setError("Pick a supplier for the draft PO");
      return;
    }
    if (chosenItemIds.length === 0) {
      setError("Select at least one item to order");
      return;
    }
    generatePo.mutate();
  }

  const autoGeneratePos = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/mrp/generate-pos", {
        method: "POST",
        body: JSON.stringify({ horizon_start: horizonStart, horizon_end: horizonEnd }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to auto-generate POs");
      }
      return (await res.json()) as AutoGenerateResult;
    },
    onSuccess: (result) => {
      setAutoResult(result);
      queryClient.invalidateQueries({ queryKey: ["mrp-plan"] });
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onAutoGenerate() {
    setError(null);
    setAutoResult(null);
    autoGeneratePos.mutate();
  }

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
          <>
            <table>
              <thead>
                <tr>
                  <th>Order?</th>
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
                  const orderable = !line.is_manufactured && qty > 0;
                  const action = qty > 0
                    ? `${line.is_manufactured ? "Build" : "Order"} ${line.suggested_order_quantity}`
                    : "-";
                  return (
                    <tr key={line.item_id}>
                      <td>
                        {orderable && (
                          <input
                            type="checkbox"
                            checked={!!selectedItems[line.item_id]}
                            onChange={(e) =>
                              setSelectedItems((prev) => ({
                                ...prev,
                                [line.item_id]: e.target.checked,
                              }))
                            }
                          />
                        )}
                      </td>
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

            <h3>Auto-generate purchase orders</h3>
            <p>
              Create a draft PO per supplier for every purchased shortfall, routed by each item's{" "}
              <Link to="/items">preferred supplier</Link>. Items with no preferred supplier are listed
              as unassigned for you to order manually below.
            </p>
            <button onClick={onAutoGenerate} disabled={autoGeneratePos.isPending}>
              Auto-generate POs by preferred supplier
            </button>
            {autoResult && (
              <div>
                {autoResult.created.length > 0 ? (
                  <ul>
                    {autoResult.created.map((po) => (
                      <li key={po.po_id}>
                        Draft PO #{po.po_number} for {po.supplier_name} ({po.line_count} line
                        {po.line_count === 1 ? "" : "s"})
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>No POs created.</p>
                )}
                {autoResult.unassigned.length > 0 && (
                  <p role="alert">
                    No preferred supplier for:{" "}
                    {autoResult.unassigned.map((u) => u.item_sku).join(", ")}. Assign one under Items,
                    or order manually below.
                  </p>
                )}
                <p>
                  <Link to="/purchase-orders">Open Purchase Orders</Link>
                </p>
              </div>
            )}

            <h3>Create a draft purchase order manually</h3>
            <p>
              Or select the purchased shortfalls above, pick a supplier, and generate a single draft
              PO at the suggested quantities. Review and submit it under{" "}
              <Link to="/purchase-orders">Purchase Orders</Link>.
            </p>
            <label>
              Supplier
              <select value={poSupplierId} onChange={(e) => setPoSupplierId(e.target.value)}>
                <option value="">Select a supplier…</option>
                {suppliers.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <button onClick={onGeneratePo} disabled={generatePo.isPending}>
              Create draft PO from {chosenItemIds.length} item{chosenItemIds.length === 1 ? "" : "s"}
            </button>
            {generatedPo && (
              <p>
                Created draft PO #{generatedPo.po_number} for {generatedPo.supplier_name} with{" "}
                {generatedPo.lines.length} line{generatedPo.lines.length === 1 ? "" : "s"}.{" "}
                <Link to="/purchase-orders">Open Purchase Orders</Link>
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
