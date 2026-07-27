import { FormEvent, useState } from "react";
import { Users } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useCurrentUser } from "@/lib/currentUser";
import type { Customer } from "./types";

export function CustomersPanel() {
  const queryClient = useQueryClient();
  const currentUser = useCurrentUser();
  // Follows Planning rather than Procurement: the sell-side write endpoints are
  // owner-or-manager, so a plain member sees the list and nothing else.
  const canEdit = currentUser.data?.role === "owner" || currentUser.data?.role === "manager";

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [search, setSearch] = useState("");
  const [activeOnly, setActiveOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const customers = useQuery<Customer[]>({
    queryKey: ["customers", search, activeOnly],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (search) params.set("q", search);
      if (activeOnly) params.set("active_only", "true");
      const res = await apiFetch(`/customers?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load customers");
      return res.json();
    },
  });

  const createCustomer = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/customers", {
        method: "POST",
        body: JSON.stringify({ name, email: email || null, phone: phone || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to add customer");
      }
      return res.json();
    },
    onSuccess: () => {
      setName("");
      setEmail("");
      setPhone("");
      queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const setCustomerActive = useMutation({
    mutationFn: async ({ id, active }: { id: string; active: boolean }) => {
      const res = await apiFetch(`/customers/${id}/${active ? "reactivate" : "deactivate"}`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update customer");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["customers"] }),
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createCustomer.mutate();
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
            <CardTitle>Add a customer</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Name" htmlFor="customer-name">
                <Input
                  id="customer-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </Field>
              <Field label="Email" htmlFor="customer-email">
                <Input
                  id="customer-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </Field>
              <Field label="Phone" htmlFor="customer-phone">
                <Input
                  id="customer-phone"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
              </Field>
              <div className="flex items-end">
                <Button type="submit" className="w-full" loading={createCustomer.isPending}>
                  Add customer
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Input
          placeholder="Search customers…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={activeOnly}
            onChange={(e) => setActiveOnly(e.target.checked)}
          />
          Active only
        </label>
      </div>

      {customers.data && customers.data.length === 0 ? (
        <EmptyState
          icon={<Users />}
          title="No customers yet"
          description="Add a customer to start raising orders."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Email</TH>
                <TH>Phone</TH>
                <TH>Status</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {customers.data?.map((customer) => (
                <TR key={customer.id}>
                  <TD className="font-medium">{customer.name}</TD>
                  <TD className="text-muted-foreground">{customer.email ?? "—"}</TD>
                  <TD className="text-muted-foreground">{customer.phone ?? "—"}</TD>
                  <TD>
                    <Badge variant={customer.is_active ? "success" : "neutral"}>
                      {customer.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    {canEdit && (
                      <Button
                        variant={customer.is_active ? "secondary" : "outline"}
                        size="sm"
                        loading={
                          setCustomerActive.isPending &&
                          setCustomerActive.variables?.id === customer.id
                        }
                        onClick={() =>
                          setCustomerActive.mutate({ id: customer.id, active: !customer.is_active })
                        }
                      >
                        {customer.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}
