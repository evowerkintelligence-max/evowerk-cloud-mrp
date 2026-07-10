import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

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
  on_draft: string;
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
    <div className="space-y-8">
      <PageHeader
        title="Planning"
        description="Enter demand forecasts per item and period. The plan explodes manufactured-item forecasts through their active BOM into component requirements, then nets against on-hand stock and open POs to show what to build and what to order."
      />

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Demand forecasts</CardTitle>
          <CardDescription>Add or update a forecast for an item and period.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <form onSubmit={onAddForecast} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Item" htmlFor="fc-item">
              <Select id="fc-item" value={itemId} onChange={(e) => setItemId(e.target.value)} required>
                <option value="">Select an item…</option>
                {items.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.sku} - {item.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Period start" htmlFor="fc-period">
              <Input
                id="fc-period"
                type="date"
                value={periodStart}
                onChange={(e) => setPeriodStart(e.target.value)}
                required
              />
            </Field>
            <Field label="Quantity" htmlFor="fc-qty">
              <Input
                id="fc-qty"
                type="number"
                step="0.0001"
                min="0"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                required
              />
            </Field>
            <Field label="Notes (optional)" htmlFor="fc-notes">
              <Input id="fc-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
            <div className="flex items-end">
              <Button type="submit" loading={upsertForecast.isPending}>
                Save forecast
              </Button>
            </div>
          </form>

          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Item</TH>
                  <TH>Period start</TH>
                  <TH>Quantity</TH>
                  <TH>Source</TH>
                  <TH>Notes</TH>
                  <TH className="text-right">Actions</TH>
                </TR>
              </THead>
              <TBody>
                {forecasts.data?.map((fc) => (
                  <TR key={fc.id}>
                    <TD className="font-medium">
                      {fc.item_sku} - {fc.item_name}
                    </TD>
                    <TD>{fc.period_start}</TD>
                    <TD>{fc.quantity}</TD>
                    <TD>
                      <Badge variant="neutral">{fc.source}</Badge>
                    </TD>
                    <TD className="text-muted-foreground">{fc.notes ?? "—"}</TD>
                    <TD className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => deleteForecast.mutate(fc.id)}
                        loading={deleteForecast.isPending}
                      >
                        Delete
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>MRP plan</CardTitle>
          <CardDescription>Net requirements across the planning horizon.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:max-w-md">
            <Field label="Horizon start" htmlFor="h-start">
              <Input
                id="h-start"
                type="date"
                value={horizonStart}
                onChange={(e) => setHorizonStart(e.target.value)}
              />
            </Field>
            <Field label="Horizon end" htmlFor="h-end">
              <Input
                id="h-end"
                type="date"
                value={horizonEnd}
                onChange={(e) => setHorizonEnd(e.target.value)}
              />
            </Field>
          </div>

          {plan.isError && <Alert variant="destructive">{(plan.error as Error).message}</Alert>}
          {plan.data && plan.data.lines.length === 0 && (
            <p className="text-sm text-muted-foreground">No forecast demand in this horizon.</p>
          )}
          {plan.data && plan.data.lines.length > 0 && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Order?</TH>
                    <TH>Item</TH>
                    <TH>Independent</TH>
                    <TH>Dependent</TH>
                    <TH>Gross</TH>
                    <TH>On hand</TH>
                    <TH>On order</TH>
                    <TH>On draft</TH>
                    <TH>Net req.</TH>
                    <TH>Suggested action</TH>
                  </TR>
                </THead>
                <TBody>
                  {plan.data.lines.map((line) => {
                    const qty = Number(line.suggested_order_quantity);
                    const orderable = !line.is_manufactured && qty > 0;
                    return (
                      <TR key={line.item_id}>
                        <TD>
                          {orderable && (
                            <Checkbox
                              checked={!!selectedItems[line.item_id]}
                              onChange={(e) =>
                                setSelectedItems((prev) => ({
                                  ...prev,
                                  [line.item_id]: e.target.checked,
                                }))
                              }
                            />
                          )}
                        </TD>
                        <TD className="font-medium">
                          {line.item_sku} - {line.item_name}
                          {line.is_manufactured && (
                            <Badge variant="accent" className="ml-2">
                              make
                            </Badge>
                          )}
                        </TD>
                        <TD>{line.independent_demand}</TD>
                        <TD>{line.dependent_demand}</TD>
                        <TD>{line.gross_demand}</TD>
                        <TD>{line.on_hand}</TD>
                        <TD>{line.on_order}</TD>
                        <TD>{line.on_draft}</TD>
                        <TD className="font-semibold">{line.net_requirement}</TD>
                        <TD>
                          {qty > 0 ? (
                            <Badge variant={line.is_manufactured ? "accent" : "info"}>
                              {line.is_manufactured ? "Build" : "Order"} {line.suggested_order_quantity}
                            </Badge>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>

      {plan.data && plan.data.lines.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Generate purchase orders</CardTitle>
            <CardDescription>
              Turn purchased shortfalls into draft POs — automatically by preferred supplier, or manually.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-8">
            <div className="space-y-3">
              <h3 className="text-sm font-semibold">Auto-generate by preferred supplier</h3>
              <p className="text-sm text-muted-foreground">
                Create a draft PO per supplier for every purchased shortfall, routed by each item's{" "}
                <Link to="/items" className="text-primary hover:underline">
                  preferred supplier
                </Link>
                . Items with no preferred supplier are listed as unassigned.
              </p>
              <Button onClick={onAutoGenerate} loading={autoGeneratePos.isPending}>
                Auto-generate POs
              </Button>
              {autoResult && (
                <div className="space-y-2 rounded-lg border border-border p-4">
                  {autoResult.created.length > 0 ? (
                    <ul className="space-y-1 text-sm">
                      {autoResult.created.map((po) => (
                        <li key={po.po_id} className="flex items-center gap-2">
                          <Badge variant="success">Draft PO #{po.po_number}</Badge>
                          {po.supplier_name} · {po.line_count} line
                          {po.line_count === 1 ? "" : "s"}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground">No POs created.</p>
                  )}
                  {autoResult.unassigned.length > 0 && (
                    <Alert variant="warning">
                      No preferred supplier for: {autoResult.unassigned.map((u) => u.item_sku).join(", ")}.
                      Assign one under Items, or order manually below.
                    </Alert>
                  )}
                  <Link to="/purchase-orders" className="inline-block text-sm text-primary hover:underline">
                    Open Purchase Orders →
                  </Link>
                </div>
              )}
            </div>

            <div className="space-y-3 border-t border-border pt-6">
              <h3 className="text-sm font-semibold">Create a draft PO manually</h3>
              <p className="text-sm text-muted-foreground">
                Select the purchased shortfalls above, pick a supplier, and generate a single draft PO at
                the suggested quantities.
              </p>
              <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end sm:max-w-lg">
                <Field label="Supplier" htmlFor="gen-supplier">
                  <Select
                    id="gen-supplier"
                    value={poSupplierId}
                    onChange={(e) => setPoSupplierId(e.target.value)}
                  >
                    <option value="">Select a supplier…</option>
                    {suppliers.data?.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Button variant="secondary" onClick={onGeneratePo} loading={generatePo.isPending}>
                  Create from {chosenItemIds.length} item{chosenItemIds.length === 1 ? "" : "s"}
                </Button>
              </div>
              {generatedPo && (
                <Alert variant="success">
                  Created draft PO #{generatedPo.po_number} for {generatedPo.supplier_name} with{" "}
                  {generatedPo.lines.length} line{generatedPo.lines.length === 1 ? "" : "s"}.{" "}
                  <Link to="/purchase-orders" className="font-medium underline">
                    Open Purchase Orders
                  </Link>
                </Alert>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
