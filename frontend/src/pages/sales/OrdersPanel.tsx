import { FormEvent, useState } from "react";
import { ClipboardList } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { ItemSelect } from "@/components/ItemSelect";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useCurrentUser } from "@/lib/currentUser";
import { humanize, soStatusVariant } from "@/lib/status";
import { trimQty } from "../planning/types";
import type { Customer, SalesOrderDetail } from "./types";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
}

export function OrdersPanel() {
  const queryClient = useQueryClient();
  const currentUser = useCurrentUser();
  const canEdit = currentUser.data?.role === "owner" || currentUser.data?.role === "manager";

  const [customerId, setCustomerId] = useState("");
  const [itemId, setItemId] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [neededBy, setNeededBy] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmDone, setConfirmDone] = useState<SalesOrderDetail | null>(null);
  const [confirmCancel, setConfirmCancel] = useState<SalesOrderDetail | null>(null);

  // The list endpoint returns each order's lines, so the rows need no
  // per-row fetch of their own.
  const orders = useQuery<SalesOrderDetail[]>({
    queryKey: ["sales-orders", search, statusFilter],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (search) params.set("q", search);
      if (statusFilter) params.set("status", statusFilter);
      const res = await apiFetch(`/sales-orders?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load orders");
      return res.json();
    },
  });

  // active_only: a customer you have stopped trading with should not be
  // pickable for a new order.
  const customers = useQuery<Customer[]>({
    queryKey: ["customers", "active"],
    queryFn: async () => {
      const res = await apiFetch("/customers?active_only=true");
      if (!res.ok) throw new Error("Failed to load customers");
      return res.json();
    },
  });

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const createOrder = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/sales-orders", {
        method: "POST",
        body: JSON.stringify({
          customer_id: customerId,
          needed_by: neededBy,
          lines: [{ item_id: itemId, quantity_ordered: quantity, sequence: 0 }],
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to raise the order");
      }
      return (await res.json()) as SalesOrderDetail;
    },
    onSuccess: () => {
      setItemId("");
      setQuantity("1");
      setNeededBy("");
      queryClient.invalidateQueries({ queryKey: ["sales-orders"] });
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const completeOrder = useMutation({
    mutationFn: async (order: SalesOrderDetail) => {
      const res = await apiFetch(`/sales-orders/${order.id}/complete`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // The backend's 409 for a shortfall names it in plain words and is
        // written to be read by the user, so show it verbatim.
        throw new Error(body.detail ?? "Failed to complete the order");
      }
      return res.json();
    },
    onSuccess: () => {
      setConfirmDone(null);
      queryClient.invalidateQueries({ queryKey: ["sales-orders"] });
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
    },
    onError: (e: Error) => {
      setConfirmDone(null);
      setError(e.message);
    },
  });

  const cancelOrder = useMutation({
    mutationFn: async (order: SalesOrderDetail) => {
      const res = await apiFetch(`/sales-orders/${order.id}/cancel`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to cancel the order");
      }
      return res.json();
    },
    onSuccess: () => {
      setConfirmCancel(null);
      queryClient.invalidateQueries({ queryKey: ["sales-orders"] });
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
    },
    onError: (e: Error) => {
      setConfirmCancel(null);
      setError(e.message);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createOrder.mutate();
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      {canEdit && (
        <Card className="mb-8">
          <CardHeader>
            <CardTitle>Raise an order</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
              <Field label="Customer" htmlFor="order-customer">
                <Select
                  id="order-customer"
                  value={customerId}
                  onChange={(e) => setCustomerId(e.target.value)}
                  required
                >
                  <option value="">Choose a customer...</option>
                  {customers.data?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Product" htmlFor="order-item">
                <ItemSelect
                  id="order-item"
                  items={items.data}
                  value={itemId}
                  onChange={setItemId}
                  required
                />
              </Field>
              <Field label="Quantity" htmlFor="order-qty">
                <Input
                  id="order-qty"
                  type="number"
                  min="0"
                  step="any"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  required
                />
              </Field>
              <Field label="Needed by" htmlFor="order-needed-by">
                <Input
                  id="order-needed-by"
                  type="date"
                  value={neededBy}
                  onChange={(e) => setNeededBy(e.target.value)}
                  required
                />
              </Field>
              <div className="flex items-end">
                <Button type="submit" className="w-full" loading={createOrder.isPending}>
                  Raise order
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Input
          placeholder="Search orders…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <div className="w-40">
          <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="done">Done</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        </div>
      </div>

      {orders.data && orders.data.length === 0 ? (
        <EmptyState
          icon={<ClipboardList />}
          title="No orders yet"
          description="Raise a customer order and it becomes demand on the planning grid."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Order</TH>
                <TH>Customer</TH>
                <TH>Needed by</TH>
                <TH>Status</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {orders.data?.map((order) => (
                <OrderRow
                  key={order.id}
                  order={order}
                  canEdit={canEdit}
                  onMarkDone={() => setConfirmDone(order)}
                  onCancel={() => setConfirmCancel(order)}
                />
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <Dialog
        open={confirmDone !== null}
        onClose={() => setConfirmDone(null)}
        title="Mark this order done?"
      >
        <p className="text-sm text-muted-foreground">
          SO-{confirmDone?.so_number} will close and the goods will come off stock. This can't be
          undone.
        </p>
        <div className="flex justify-end gap-2 pt-4">
          <Button variant="secondary" onClick={() => setConfirmDone(null)}>
            Never mind
          </Button>
          <Button
            loading={completeOrder.isPending}
            onClick={() => confirmDone && completeOrder.mutate(confirmDone)}
          >
            Mark done
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={confirmCancel !== null}
        onClose={() => setConfirmCancel(null)}
        title="Cancel this order?"
      >
        <p className="text-sm text-muted-foreground">
          SO-{confirmCancel?.so_number} will be cancelled and stop counting as demand. No stock
          moves.
        </p>
        <div className="flex justify-end gap-2 pt-4">
          <Button variant="secondary" onClick={() => setConfirmCancel(null)}>
            Never mind
          </Button>
          <Button
            variant="destructive"
            loading={cancelOrder.isPending}
            onClick={() => confirmCancel && cancelOrder.mutate(confirmCancel)}
          >
            Cancel order
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

function OrderRow({
  order,
  canEdit,
  onMarkDone,
  onCancel,
}: {
  order: SalesOrderDetail;
  canEdit: boolean;
  onMarkDone: () => void;
  onCancel: () => void;
}) {
  return (
    <TR>
      <TD className="font-medium">SO-{order.so_number}</TD>
      <TD>
        {order.customer_name}
        {order.lines.length > 0 && (
          <div className="text-sm text-muted-foreground">
            {order.lines.map((l) => `${l.item_sku} × ${trimQty(l.quantity_ordered)}`).join(", ")}
          </div>
        )}
      </TD>
      <TD className="text-muted-foreground">{order.needed_by}</TD>
      <TD>
        <Badge variant={soStatusVariant(order.status)}>{humanize(order.status)}</Badge>
      </TD>
      <TD className="text-right">
        {canEdit && order.status === "open" && (
          <div className="flex justify-end gap-2">
            <Button size="sm" onClick={onMarkDone}>
              Mark done
            </Button>
            <Button variant="outline" size="sm" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        )}
      </TD>
    </TR>
  );
}
