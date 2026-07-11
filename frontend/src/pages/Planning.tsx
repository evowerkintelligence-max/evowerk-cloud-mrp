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
  need_by_date: string | null;
  suggested_order_date: string | null;
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

  // Tracked separately from deleteForecast.isPending/.variables - those
  // reflect only the single most-recently-invoked call, so deleting row B
  // while row A's DELETE is still in flight would incorrectly re-enable A's
  // button. A Set of in-flight ids handles overlapping calls correctly.
  const [pendingForecastIds, setPendingForecastIds] = useState<Set<string>>(new Set());
  const deleteForecast = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/forecasts/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to delete forecast");
      }
    },
    onMutate: (id) => setPendingForecastIds((prev) => new Set(prev).add(id)),
    onSettled: (_data, _err, id) =>
      setPendingForecastIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      }),
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
                        loading={pendingForecastIds.has(fc.id)}
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
                    <TH>Item</TH>
                    <TH>Independent</TH>
                    <TH>Dependent</TH>
                    <TH>Gross</TH>
                    <TH>On hand</TH>
                    <TH>On order</TH>
                    <TH>On draft</TH>
                    <TH>Net req.</TH>
                    <TH>Suggested action</TH>
                    <TH>Order by</TH>
                  </TR>
                </THead>
                <TBody>
                  {plan.data.lines.map((line) => {
                    const qty = Number(line.suggested_order_quantity);
                    return (
                      <TR key={line.item_id}>
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
                        <TD>
                          {line.suggested_order_date ? (
                            <span
                              className={
                                line.suggested_order_date < today()
                                  ? "font-semibold text-destructive"
                                  : undefined
                              }
                            >
                              {line.suggested_order_date}
                            </span>
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
          {plan.data && plan.data.lines.length > 0 && (
            <p className="text-sm text-muted-foreground">
              Ready to order the shortfalls?{" "}
              <Link
                to={`/procurement?tab=generate&horizonStart=${horizonStart}&horizonEnd=${horizonEnd}`}
                className="text-primary hover:underline"
              >
                Generate purchase orders
              </Link>{" "}
              in Procurement.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
