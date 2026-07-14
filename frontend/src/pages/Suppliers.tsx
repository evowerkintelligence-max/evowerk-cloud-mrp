import { FormEvent, useState } from "react";
import { Truck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface SupplierResponse {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  is_active: boolean;
}

const PAGE_SIZE = 20;

export function SuppliersPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<SupplierResponse | null>(null);
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  const suppliers = useQuery<SupplierResponse[]>({
    queryKey: ["suppliers", search, page],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
      if (search) params.set("q", search);
      const res = await apiFetch(`/suppliers?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load suppliers");
      return res.json();
    },
  });

  const createSupplier = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/suppliers", {
        method: "POST",
        body: JSON.stringify({ name, email: email || null, phone: phone || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create supplier");
      }
      return res.json();
    },
    onSuccess: () => {
      setName("");
      setEmail("");
      setPhone("");
      queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const updateSupplier = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("No supplier selected");
      const res = await apiFetch(`/suppliers/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: editName, email: editEmail || null, phone: editPhone || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update supplier");
      }
      return res.json();
    },
    onSuccess: () => {
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    },
    onError: (e: Error) => setEditError(e.message),
  });

  const setSupplierActive = useMutation({
    mutationFn: async ({ id, active }: { id: string; active: boolean }) => {
      const res = await apiFetch(`/suppliers/${id}/${active ? "reactivate" : "deactivate"}`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update supplier");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suppliers"] }),
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createSupplier.mutate();
  }

  function openEdit(supplier: SupplierResponse) {
    setEditError(null);
    setEditing(supplier);
    setEditName(supplier.name);
    setEditEmail(supplier.email ?? "");
    setEditPhone(supplier.phone ?? "");
  }

  function onEditSubmit(e: FormEvent) {
    e.preventDefault();
    setEditError(null);
    updateSupplier.mutate();
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add a supplier</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Name" htmlFor="name">
              <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <Field label="Email" htmlFor="email">
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </Field>
            <Field label="Phone" htmlFor="phone">
              <Input id="phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </Field>
            <div className="flex items-end">
              <Button type="submit" className="w-full" loading={createSupplier.isPending}>
                Add supplier
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <div className="mb-4 max-w-xs">
        <Input
          placeholder="Search suppliers…"
          value={search}
          onChange={(e) => {
            setPage(0);
            setSearch(e.target.value);
          }}
        />
      </div>

      {suppliers.data && suppliers.data.length === 0 ? (
        <EmptyState
          icon={<Truck />}
          title="No suppliers yet"
          description="Add a vendor to start creating purchase orders."
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
              {suppliers.data?.map((supplier) => (
                <TR key={supplier.id}>
                  <TD className="font-medium">{supplier.name}</TD>
                  <TD className="text-muted-foreground">{supplier.email ?? "—"}</TD>
                  <TD className="text-muted-foreground">{supplier.phone ?? "—"}</TD>
                  <TD>
                    <Badge variant={supplier.is_active ? "success" : "neutral"}>
                      {supplier.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="outline" size="sm" onClick={() => openEdit(supplier)}>
                        Edit
                      </Button>
                      <Button
                        variant={supplier.is_active ? "secondary" : "outline"}
                        size="sm"
                        loading={
                          setSupplierActive.isPending && setSupplierActive.variables?.id === supplier.id
                        }
                        onClick={() =>
                          setSupplierActive.mutate({ id: supplier.id, active: !supplier.is_active })
                        }
                      >
                        {supplier.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <div className="mb-8 mt-4 flex items-center justify-between">
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
          disabled={!suppliers.data || suppliers.data.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </Button>
      </div>

      <Dialog open={editing !== null} onClose={() => setEditing(null)} title="Edit supplier">
        <form onSubmit={onEditSubmit} className="space-y-4">
          {editError && <Alert variant="destructive">{editError}</Alert>}
          <Field label="Name" htmlFor="edit-name">
            <Input id="edit-name" value={editName} onChange={(e) => setEditName(e.target.value)} required />
          </Field>
          <Field label="Email" htmlFor="edit-email">
            <Input
              id="edit-email"
              type="email"
              value={editEmail}
              onChange={(e) => setEditEmail(e.target.value)}
            />
          </Field>
          <Field label="Phone" htmlFor="edit-phone">
            <Input id="edit-phone" value={editPhone} onChange={(e) => setEditPhone(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={updateSupplier.isPending}>
              Save
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
