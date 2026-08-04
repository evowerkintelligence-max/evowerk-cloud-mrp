import { FormEvent, useState } from "react";
import { Boxes } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, lotStatusVariant } from "@/lib/status";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;
// Mirrors LOT_EXPIRY_WARNING_DAYS in backend/app/services/morning_brief.py -
// same "worth a look" threshold, so the badge here agrees with the brief.
const EXPIRY_WARNING_DAYS = 14;

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
}

interface LocationResponse {
  id: string;
  code: string;
  name: string;
  is_default: boolean;
}

interface LotResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  location_id: string;
  location_code: string;
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

interface FefoConsumeLine {
  lot_id: string;
  lot_number: string;
  quantity_consumed: string;
  quantity_remaining: string;
  expiry_date: string | null;
}

function daysUntil(dateStr: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(dateStr);
  target.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
}

function ExpiryCell({ expiryDate }: { expiryDate: string | null }) {
  if (!expiryDate) return <span className="text-muted-foreground">—</span>;
  const days = daysUntil(expiryDate);
  if (days < 0) {
    return <Badge variant="danger">Expired {Math.abs(days)}d ago</Badge>;
  }
  if (days <= EXPIRY_WARNING_DAYS) {
    return (
      <Badge variant={days <= 3 ? "danger" : "warning"}>
        {expiryDate} · {days}d left
      </Badge>
    );
  }
  return <span className="text-muted-foreground">{expiryDate}</span>;
}

export function Lots() {
  const queryClient = useQueryClient();
  const [selectedLotId, setSelectedLotId] = useState<string | null>(null);
  const [consumeQuantity, setConsumeQuantity] = useState("1");
  const [consumeReason, setConsumeReason] = useState("");
  const [transferQuantity, setTransferQuantity] = useState("1");
  const [transferToLocation, setTransferToLocation] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState("");
  const [locationFilter, setLocationFilter] = useState("");
  const [expiringSoonOnly, setExpiringSoonOnly] = useState(false);
  const [page, setPage] = useState(0);

  const [fefoItemId, setFefoItemId] = useState("");
  const [fefoLocationId, setFefoLocationId] = useState("");
  const [fefoQuantity, setFefoQuantity] = useState("1");
  const [fefoReason, setFefoReason] = useState("");
  const [fefoResult, setFefoResult] = useState<FefoConsumeLine[] | null>(null);

  function resetToFirstPage<T>(setter: (v: T) => void) {
    return (value: T) => {
      setPage(0);
      setter(value);
    };
  }

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
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

  const lots = useQuery<LotResponse[]>({
    queryKey: ["lots", statusFilter, locationFilter, page],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
      if (statusFilter) params.set("status", statusFilter);
      if (locationFilter) params.set("location_id", locationFilter);
      const res = await apiFetch(`/lots?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load lots");
      return res.json();
    },
  });

  const visibleLots = expiringSoonOnly
    ? lots.data?.filter((lot) => lot.expiry_date && daysUntil(lot.expiry_date) <= EXPIRY_WARNING_DAYS)
    : lots.data;

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

  const transferLot = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/lots/${selectedLotId}/transfer`, {
        method: "POST",
        body: JSON.stringify({ to_location_id: transferToLocation, quantity: transferQuantity }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to transfer");
      }
      return res.json();
    },
    onSuccess: () => {
      setTransferQuantity("1");
      setTransferToLocation("");
      invalidateLot();
    },
    onError: (e: Error) => setError(e.message),
  });

  const fefoConsume = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/lots/consume-fefo", {
        method: "POST",
        body: JSON.stringify({
          item_id: fefoItemId,
          location_id: fefoLocationId,
          quantity: fefoQuantity,
          reason: fefoReason || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to consume");
      }
      return res.json() as Promise<{ lines: FefoConsumeLine[] }>;
    },
    onSuccess: (data) => {
      setFefoResult(data.lines);
      setFefoQuantity("1");
      setFefoReason("");
      invalidateLot();
    },
    onError: (e: Error) => setError(e.message),
  });

  function onConsume(e: FormEvent) {
    e.preventDefault();
    setError(null);
    consumeLot.mutate();
  }

  function onTransfer(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!transferToLocation) {
      setError("Pick a destination location");
      return;
    }
    transferLot.mutate();
  }

  function onFefoConsume(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setFefoResult(null);
    if (!fefoItemId || !fefoLocationId) {
      setError("Pick an item and a location");
      return;
    }
    fefoConsume.mutate();
  }

  return (
    <div>
      <PageHeader
        title="Lots"
        description="Lot-tracked stock from received purchase orders. Select a lot to consume, hold, transfer, or release it."
      />

      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Consume by item (FEFO)</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onFefoConsume} className="grid gap-4 md:grid-cols-4">
            <Field label="Item" htmlFor="fefo-item">
              <Select
                id="fefo-item"
                value={fefoItemId}
                onChange={(e) => setFefoItemId(e.target.value)}
              >
                <option value="">Select…</option>
                {items.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.sku} - {item.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Location" htmlFor="fefo-location">
              <Select
                id="fefo-location"
                value={fefoLocationId}
                onChange={(e) => setFefoLocationId(e.target.value)}
              >
                <option value="">Select…</option>
                {locations.data?.map((loc) => (
                  <option key={loc.id} value={loc.id}>
                    {loc.code}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Quantity" htmlFor="fefo-qty">
              <Input
                id="fefo-qty"
                type="number"
                step="any"
                min="0.0001"
                value={fefoQuantity}
                onChange={(e) => setFefoQuantity(e.target.value)}
                required
              />
            </Field>
            <Field label="Reason (optional)" htmlFor="fefo-reason">
              <Input
                id="fefo-reason"
                value={fefoReason}
                onChange={(e) => setFefoReason(e.target.value)}
              />
            </Field>
            <div className="md:col-span-4">
              <Button type="submit" variant="secondary" loading={fefoConsume.isPending}>
                Consume earliest-expiring stock first
              </Button>
            </div>
          </form>
          {fefoResult && (
            <Alert variant="success" className="mt-4">
              Consumed {fefoResult.length} lot{fefoResult.length === 1 ? "" : "s"}:{" "}
              {fefoResult
                .map((line) => `${line.lot_number} (${line.quantity_consumed})`)
                .join(", ")}
            </Alert>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-end gap-4 pt-6">
          <Field label="Status" htmlFor="filter-status" className="w-40">
            <Select
              id="filter-status"
              value={statusFilter}
              onChange={(e) => resetToFirstPage(setStatusFilter)(e.target.value)}
            >
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="on_hold">On hold</option>
              <option value="depleted">Depleted</option>
            </Select>
          </Field>
          <Field label="Location" htmlFor="filter-location" className="w-40">
            <Select
              id="filter-location"
              value={locationFilter}
              onChange={(e) => resetToFirstPage(setLocationFilter)(e.target.value)}
            >
              <option value="">All</option>
              {locations.data?.map((loc) => (
                <option key={loc.id} value={loc.id}>
                  {loc.code}
                </option>
              ))}
            </Select>
          </Field>
          <label className="flex items-center gap-2 pb-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={expiringSoonOnly}
              onChange={(e) => setExpiringSoonOnly(e.target.checked)}
              className="size-4 rounded border-input"
            />
            Expiring within {EXPIRY_WARNING_DAYS}d only
          </label>
        </CardContent>
      </Card>

      {visibleLots && visibleLots.length === 0 ? (
        <EmptyState
          icon={<Boxes />}
          title="No lots found"
          description="No lots match the current filters."
        />
      ) : (
        <TableWrap className="mb-4">
          <Table>
            <THead>
              <TR>
                <TH>Item</TH>
                <TH>Lot #</TH>
                <TH>Location</TH>
                <TH>Received</TH>
                <TH>Remaining</TH>
                <TH>Status</TH>
                <TH>Expiry</TH>
              </TR>
            </THead>
            <TBody>
              {visibleLots?.map((lot) => (
                <TR
                  key={lot.id}
                  onClick={() => setSelectedLotId(lot.id)}
                  className={cn("cursor-pointer", selectedLotId === lot.id && "bg-primary/5")}
                >
                  <TD className="font-medium">
                    {lot.item_sku} - {lot.item_name}
                  </TD>
                  <TD className="font-mono text-xs">{lot.lot_number}</TD>
                  <TD className="text-muted-foreground">{lot.location_code}</TD>
                  <TD className="text-muted-foreground">{lot.quantity_received}</TD>
                  <TD>{lot.quantity_remaining}</TD>
                  <TD>
                    <Badge variant={lotStatusVariant(lot.status)}>{humanize(lot.status)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">
                    <ExpiryCell expiryDate={lot.expiry_date} />
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <div className="mb-8 flex items-center justify-between">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          Previous
        </Button>
        <span className="text-sm text-muted-foreground">Page {page + 1}</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!lots.data || lots.data.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </Button>
      </div>

      {lotDetail.data && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                {lotDetail.data.item_sku} · Lot {lotDetail.data.lot_number} · {lotDetail.data.location_code}
                <Badge variant={lotStatusVariant(lotDetail.data.status)}>
                  {humanize(lotDetail.data.status)}
                </Badge>
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {lotDetail.data.quantity_remaining} / {lotDetail.data.quantity_received} remaining
              </p>
            </div>
            {lotDetail.data.status === "on_hold" ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => releaseLot.mutate()}
                loading={releaseLot.isPending}
              >
                Release hold
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => holdLot.mutate()}
                loading={holdLot.isPending}
              >
                Hold
              </Button>
            )}
          </CardHeader>
          <CardContent className="space-y-6">
            {lotDetail.data.status !== "on_hold" && (
              <div className="grid gap-4 md:grid-cols-2">
                <form onSubmit={onConsume} className="space-y-3 rounded-lg border border-border p-4">
                  <p className="text-sm font-semibold">Consume</p>
                  <Field label="Quantity" htmlFor="consume-qty">
                    <Input
                      id="consume-qty"
                      type="number"
                      step="any"
                      min="0.0001"
                      value={consumeQuantity}
                      onChange={(e) => setConsumeQuantity(e.target.value)}
                      required
                    />
                  </Field>
                  <Field label="Reason (optional)" htmlFor="consume-reason">
                    <Input
                      id="consume-reason"
                      value={consumeReason}
                      onChange={(e) => setConsumeReason(e.target.value)}
                    />
                  </Field>
                  <Button type="submit" variant="secondary" className="w-full" loading={consumeLot.isPending}>
                    Consume
                  </Button>
                </form>

                <form onSubmit={onTransfer} className="space-y-3 rounded-lg border border-border p-4">
                  <p className="text-sm font-semibold">Transfer</p>
                  <Field label="Quantity" htmlFor="transfer-qty">
                    <Input
                      id="transfer-qty"
                      type="number"
                      step="any"
                      min="0.0001"
                      value={transferQuantity}
                      onChange={(e) => setTransferQuantity(e.target.value)}
                      required
                    />
                  </Field>
                  <Field label="To location" htmlFor="transfer-loc">
                    <Select
                      id="transfer-loc"
                      value={transferToLocation}
                      onChange={(e) => setTransferToLocation(e.target.value)}
                    >
                      <option value="">Select…</option>
                      {locations.data
                        ?.filter((l) => l.id !== lotDetail.data?.location_id)
                        .map((l) => (
                          <option key={l.id} value={l.id}>
                            {l.code}
                          </option>
                        ))}
                    </Select>
                  </Field>
                  <Button type="submit" variant="secondary" className="w-full" loading={transferLot.isPending}>
                    Transfer
                  </Button>
                </form>
              </div>
            )}

            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Type</TH>
                    <TH>Quantity</TH>
                    <TH>Reason</TH>
                    <TH>When</TH>
                  </TR>
                </THead>
                <TBody>
                  {lotDetail.data.transactions.map((tx) => (
                    <TR key={tx.id}>
                      <TD>{humanize(tx.transaction_type)}</TD>
                      <TD>{tx.quantity}</TD>
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
