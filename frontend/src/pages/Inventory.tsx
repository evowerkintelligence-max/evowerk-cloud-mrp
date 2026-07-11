import { FormEvent, useState } from "react";
import { Warehouse } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, inventoryStatusVariant } from "@/lib/status";

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

interface InventoryAnalyticsLine {
  item_id: string;
  reorder_point: number | null;
  days_of_supply: number | null;
  status: string;
}

export function InventoryPanel() {
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

  const analytics = useQuery<InventoryAnalyticsLine[]>({
    queryKey: ["inventory-analytics"],
    queryFn: async () => {
      const res = await apiFetch("/inventory/analytics");
      if (!res.ok) throw new Error("Failed to load inventory analytics");
      return (await res.json()).lines;
    },
  });
  const analyticsByItem = new Map(analytics.data?.map((a) => [a.item_id, a]));

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
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      {balances.data && balances.data.length === 0 ? (
        <EmptyState
          icon={<Warehouse />}
          title="No stock yet"
          description="On-hand balances appear here once you receive purchase orders."
        />
      ) : (
        <TableWrap className="mb-8">
          <Table>
            <THead>
              <TR>
                <TH>Item</TH>
                <TH>Total on hand</TH>
                <TH>By location</TH>
                <TH>UoM</TH>
                <TH>Status</TH>
              </TR>
            </THead>
            <TBody>
              {balances.data?.map((bal) => {
                const a = analyticsByItem.get(bal.item_id);
                return (
                  <TR
                    key={bal.item_id}
                    onClick={() => setSelectedItemId(bal.item_id)}
                    className={`cursor-pointer ${selectedItemId === bal.item_id ? "bg-primary/5" : ""}`}
                  >
                    <TD className="font-medium">
                      {bal.item_sku} - {bal.item_name}
                    </TD>
                    <TD>{bal.quantity_on_hand}</TD>
                    <TD className="text-muted-foreground">
                      {bal.by_location.map((lq) => `${lq.location_code}: ${lq.quantity_on_hand}`).join(", ")}
                    </TD>
                    <TD className="text-muted-foreground">{bal.unit_of_measure}</TD>
                    <TD>
                      {a && a.status !== "no_data" && (
                        <Badge variant={inventoryStatusVariant(a.status)}>{humanize(a.status)}</Badge>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </TableWrap>
      )}

      {detail.data && (
        <Card>
          <CardHeader>
            <CardTitle>
              {detail.data.item_sku} - {detail.data.item_name}
            </CardTitle>
            <p className="text-sm text-muted-foreground">
              Total on hand: {detail.data.quantity_on_hand} {detail.data.unit_of_measure}
              {(() => {
                const a = analyticsByItem.get(detail.data.item_id);
                if (!a || a.reorder_point == null) return null;
                return (
                  <>
                    {" "}
                    · Reorder point: {a.reorder_point.toFixed(2)}{" "}
                    <Badge variant={inventoryStatusVariant(a.status)}>{humanize(a.status)}</Badge>
                  </>
                );
              })()}
            </p>
          </CardHeader>
          <CardContent className="space-y-6">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Location</TH>
                    <TH>On hand</TH>
                  </TR>
                </THead>
                <TBody>
                  {detail.data.by_location.map((lq) => (
                    <TR key={lq.location_id}>
                      <TD className="font-medium">
                        {lq.location_code} - {lq.location_name}
                      </TD>
                      <TD>{lq.quantity_on_hand}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>

            <div className="grid gap-4 md:grid-cols-3">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setError(null);
                  consume.mutate();
                }}
                className="space-y-3 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Consume</p>
                <Field label="Quantity" htmlFor="c-qty">
                  <Input
                    id="c-qty"
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    value={consumeQty}
                    onChange={(e) => setConsumeQty(e.target.value)}
                    required
                  />
                </Field>
                <Field label="At location" htmlFor="c-loc">
                  <Select id="c-loc" value={consumeLoc} onChange={(e) => setConsumeLoc(e.target.value)}>
                    {locationOptions}
                  </Select>
                </Field>
                <Button type="submit" variant="secondary" className="w-full" loading={consume.isPending}>
                  Consume
                </Button>
              </form>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setError(null);
                  adjust.mutate();
                }}
                className="space-y-3 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Adjust</p>
                <Field label="Delta (+/-)" htmlFor="a-qty">
                  <Input
                    id="a-qty"
                    type="number"
                    step="0.0001"
                    value={adjustDelta}
                    onChange={(e) => setAdjustDelta(e.target.value)}
                    required
                  />
                </Field>
                <Field label="At location" htmlFor="a-loc">
                  <Select id="a-loc" value={adjustLoc} onChange={(e) => setAdjustLoc(e.target.value)}>
                    {locationOptions}
                  </Select>
                </Field>
                <Button type="submit" variant="secondary" className="w-full" loading={adjust.isPending}>
                  Adjust
                </Button>
              </form>

              <form onSubmit={onTransfer} className="space-y-3 rounded-lg border border-border p-4">
                <p className="text-sm font-semibold">Transfer</p>
                <Field label="Quantity" htmlFor="t-qty">
                  <Input
                    id="t-qty"
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    value={transferQty}
                    onChange={(e) => setTransferQty(e.target.value)}
                    required
                  />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="From" htmlFor="t-from">
                    <Select id="t-from" value={fromLoc} onChange={(e) => setFromLoc(e.target.value)}>
                      <option value="">Select…</option>
                      {locations.data?.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.code}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="To" htmlFor="t-to">
                    <Select id="t-to" value={toLoc} onChange={(e) => setToLoc(e.target.value)}>
                      <option value="">Select…</option>
                      {locations.data?.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.code}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                <Button type="submit" variant="secondary" className="w-full" loading={transfer.isPending}>
                  Transfer
                </Button>
              </form>
            </div>

            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Type</TH>
                    <TH>Location</TH>
                    <TH>Change</TH>
                    <TH>Reason</TH>
                    <TH>When</TH>
                  </TR>
                </THead>
                <TBody>
                  {detail.data.transactions.map((tx) => (
                    <TR key={tx.id}>
                      <TD>{humanize(tx.transaction_type)}</TD>
                      <TD className="text-muted-foreground">{tx.location_code}</TD>
                      <TD>
                        <Badge variant={Number(tx.quantity_delta) < 0 ? "danger" : "success"}>
                          {tx.quantity_delta}
                        </Badge>
                      </TD>
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
