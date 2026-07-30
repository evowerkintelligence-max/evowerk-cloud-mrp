import { FormEvent, useState } from "react";
import { PackageCheck, TrendingDown, TrendingUp, Warehouse } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { StatCard, STAT_TONES } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, inventoryStatusVariant } from "@/lib/status";
import { cn } from "@/lib/utils";
import { AnalyticsDetail } from "./inventory/AnalyticsDetail";
import { InsightBanner } from "./inventory/InsightBanner";
import { sortInventoryRows, type InventoryAnalyticsLine } from "./inventory/types";

const REASON_CODES = [
  { value: "cycle_count", label: "Cycle count" },
  { value: "damage", label: "Damage" },
  { value: "found_stock", label: "Found stock" },
  { value: "shrinkage", label: "Shrinkage" },
  { value: "correction", label: "Correction" },
  { value: "other", label: "Other" },
] as const;
type ReasonCode = (typeof REASON_CODES)[number]["value"];

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
  counted_quantity: string | null;
  expected_quantity: string | null;
  reason_note: string | null;
  created_at: string;
}

interface InventoryDetail extends InventoryItem {
  transactions: InventoryTransaction[];
}

interface AdjustmentRequestSummary {
  id: string;
  status: string;
}

interface AdjustmentOutcome {
  applied: boolean;
  item: InventoryItem | null;
  request: AdjustmentRequestSummary | null;
}

export function InventoryPanel() {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [selectedItemId, setSelectedItemId] = useState<string | null>(searchParams.get("item"));
  const [consumeQty, setConsumeQty] = useState("1");
  const [consumeLoc, setConsumeLoc] = useState("");
  const [adjustDelta, setAdjustDelta] = useState("1");
  const [adjustLoc, setAdjustLoc] = useState("");
  const [adjustReasonCode, setAdjustReasonCode] = useState<ReasonCode>("correction");
  const [adjustReasonNote, setAdjustReasonNote] = useState("");
  const [countQty, setCountQty] = useState("");
  const [countLoc, setCountLoc] = useState("");
  const [countReasonCode, setCountReasonCode] = useState<ReasonCode>("cycle_count");
  const [countReasonNote, setCountReasonNote] = useState("");
  const [fromLoc, setFromLoc] = useState("");
  const [toLoc, setToLoc] = useState("");
  const [transferQty, setTransferQty] = useState("1");
  const [error, setError] = useState<string | null>(null);
  const [pendingNotice, setPendingNotice] = useState<string | null>(null);

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
  const sortedBalances = balances.data ? sortInventoryRows(balances.data, analyticsByItem) : undefined;

  // Summary counts over the full analytics set (not paginated) — a quick read on stock health.
  const statusCounts = (analytics.data ?? []).reduce(
    (acc, a) => {
      if (a.status === "stockout" || a.status === "low") acc.low += 1;
      else if (a.status === "excess") acc.excess += 1;
      else if (a.status === "healthy") acc.healthy += 1;
      return acc;
    },
    { low: 0, excess: 0, healthy: 0 },
  );
  const statTiles = [
    { label: "Low stock", value: statusCounts.low, icon: <TrendingDown /> },
    { label: "Excess", value: statusCounts.excess, icon: <TrendingUp /> },
    { label: "Healthy", value: statusCounts.healthy, icon: <PackageCheck /> },
  ];

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

  const adjust = useMutation<AdjustmentOutcome, Error>({
    mutationFn: () =>
      post(`/inventory/${selectedItemId}/adjust`, {
        quantity: adjustDelta,
        location_id: adjustLoc || null,
        reason_code: adjustReasonCode,
        reason_note: adjustReasonCode === "other" ? adjustReasonNote : null,
      })(),
    onSuccess: (outcome) => {
      if (outcome.applied) {
        invalidate();
      } else {
        setPendingNotice(`Submitted for owner approval (request ${outcome.request?.id.slice(0, 8)}…).`);
      }
    },
    onError: (e: Error) => setError(e.message),
  });

  const count = useMutation<AdjustmentOutcome, Error>({
    mutationFn: () =>
      post(`/inventory/${selectedItemId}/count`, {
        location_id: countLoc || null,
        counted_quantity: countQty,
        reason_code: countReasonCode,
        reason_note: countReasonCode === "other" ? countReasonNote : null,
      })(),
    onSuccess: (outcome) => {
      if (outcome.applied) {
        invalidate();
      } else {
        setPendingNotice(`Submitted for owner approval (request ${outcome.request?.id.slice(0, 8)}…).`);
      }
      setCountQty("");
    },
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
      <InsightBanner />

      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      {pendingNotice && (
        <Alert variant="info" className="mb-6">
          {pendingNotice}
        </Alert>
      )}

      {sortedBalances && sortedBalances.length > 0 && analytics.data && (
        <div className="mb-8 grid grid-cols-3 gap-4">
          {statTiles.map((t, i) => (
            <StatCard
              key={t.label}
              label={t.label}
              value={t.value}
              icon={t.icon}
              tone={STAT_TONES[i % STAT_TONES.length]}
            />
          ))}
        </div>
      )}

      {sortedBalances && sortedBalances.length === 0 ? (
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
              {sortedBalances?.map((bal) => {
                const a = analyticsByItem.get(bal.item_id);
                return (
                  <TR
                    key={bal.item_id}
                    onClick={() => setSelectedItemId(bal.item_id)}
                    className={cn("cursor-pointer", selectedItemId === bal.item_id && "bg-primary/5")}
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
            <CardTitle className="flex items-center justify-between">
              <span>
                {detail.data.item_sku} - {detail.data.item_name}
              </span>
              <Link to="/planning" className="text-xs font-normal text-muted-foreground underline">
                View in Planning
              </Link>
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

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setError(null);
                  setPendingNotice(null);
                  consume.mutate();
                }}
                className="space-y-3 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Consume</p>
                <Field label="Quantity" htmlFor="c-qty">
                  <Input
                    id="c-qty"
                    type="number"
                    step="any"
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
                  setPendingNotice(null);
                  count.mutate();
                }}
                className="space-y-3 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Count</p>
                <Field label="Counted quantity" htmlFor="cnt-qty">
                  <Input
                    id="cnt-qty"
                    type="number"
                    step="any"
                    min="0"
                    value={countQty}
                    onChange={(e) => setCountQty(e.target.value)}
                    required
                  />
                </Field>
                <Field label="At location" htmlFor="cnt-loc">
                  <Select id="cnt-loc" value={countLoc} onChange={(e) => setCountLoc(e.target.value)}>
                    {locationOptions}
                  </Select>
                </Field>
                <Field label="Reason" htmlFor="cnt-reason">
                  <Select
                    id="cnt-reason"
                    value={countReasonCode}
                    onChange={(e) => setCountReasonCode(e.target.value as ReasonCode)}
                  >
                    {REASON_CODES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                {countReasonCode === "other" && (
                  <Field label="Note" htmlFor="cnt-note">
                    <Input
                      id="cnt-note"
                      value={countReasonNote}
                      onChange={(e) => setCountReasonNote(e.target.value)}
                      required
                    />
                  </Field>
                )}
                <Button type="submit" variant="secondary" className="w-full" loading={count.isPending}>
                  Count
                </Button>
              </form>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setError(null);
                  setPendingNotice(null);
                  adjust.mutate();
                }}
                className="space-y-3 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Adjust (no count)</p>
                <Field label="Delta (+/-)" htmlFor="a-qty">
                  <Input
                    id="a-qty"
                    type="number"
                    step="any"
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
                <Field label="Reason" htmlFor="a-reason">
                  <Select
                    id="a-reason"
                    value={adjustReasonCode}
                    onChange={(e) => setAdjustReasonCode(e.target.value as ReasonCode)}
                  >
                    {REASON_CODES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                {adjustReasonCode === "other" && (
                  <Field label="Note" htmlFor="a-note">
                    <Input
                      id="a-note"
                      value={adjustReasonNote}
                      onChange={(e) => setAdjustReasonNote(e.target.value)}
                      required
                    />
                  </Field>
                )}
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
                    step="any"
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
                      <TD className="text-muted-foreground">
                        {tx.reason ? humanize(tx.reason) : "—"}
                        {tx.counted_quantity != null && (
                          <span className="block text-xs">
                            counted {tx.counted_quantity} · expected {tx.expected_quantity}
                          </span>
                        )}
                      </TD>
                      <TD className="text-muted-foreground">
                        {new Date(tx.created_at).toLocaleString()}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>

            {(() => {
              const line = analyticsByItem.get(detail.data.item_id);
              return line ? <AnalyticsDetail itemId={detail.data.item_id} line={line} /> : null;
            })()}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
