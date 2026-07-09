import { FormEvent, useState } from "react";
import { Truck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface SupplierResponse {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
}

export function Suppliers() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);

  const suppliers = useQuery<SupplierResponse[]>({
    queryKey: ["suppliers"],
    queryFn: async () => {
      const res = await apiFetch("/suppliers");
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

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createSupplier.mutate();
  }

  return (
    <div>
      <PageHeader title="Suppliers" description="Your vendor directory — the sources behind purchase orders." />

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
              </TR>
            </THead>
            <TBody>
              {suppliers.data?.map((supplier) => (
                <TR key={supplier.id}>
                  <TD className="font-medium">{supplier.name}</TD>
                  <TD className="text-muted-foreground">{supplier.email ?? "—"}</TD>
                  <TD className="text-muted-foreground">{supplier.phone ?? "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}
