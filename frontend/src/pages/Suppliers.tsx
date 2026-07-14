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
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string | null;
  payment_terms_days: number | null;
  lead_time_days: number | null;
  tags: string[];
}

interface SupplierContactData {
  id: string;
  name: string;
  role: string | null;
  email: string | null;
  phone: string | null;
  is_primary: boolean;
}

interface SupplierScorecard {
  on_time_pct: number | null;
  defect_pct: number | null;
  sample_size: number;
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
  const [tagFilter, setTagFilter] = useState<string | null>(null);

  const [editing, setEditing] = useState<SupplierResponse | null>(null);
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editAddressLine1, setEditAddressLine1] = useState("");
  const [editAddressLine2, setEditAddressLine2] = useState("");
  const [editCity, setEditCity] = useState("");
  const [editState, setEditState] = useState("");
  const [editPostalCode, setEditPostalCode] = useState("");
  const [editCountry, setEditCountry] = useState("");
  const [editPaymentTermsDays, setEditPaymentTermsDays] = useState("");
  const [editLeadTimeDays, setEditLeadTimeDays] = useState("");
  const [editTags, setEditTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  const [contactName, setContactName] = useState("");
  const [contactRole, setContactRole] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [contactPhone, setContactPhone] = useState("");
  const [contactError, setContactError] = useState<string | null>(null);

  const suppliers = useQuery<SupplierResponse[]>({
    queryKey: ["suppliers", search, page, tagFilter],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
      if (search) params.set("q", search);
      if (tagFilter) params.set("tag", tagFilter);
      const res = await apiFetch(`/suppliers?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load suppliers");
      return res.json();
    },
  });

  const availableTags = Array.from(new Set((suppliers.data ?? []).flatMap((s) => s.tags))).sort();

  const contacts = useQuery<SupplierContactData[]>({
    queryKey: ["suppliers", editing?.id, "contacts"],
    enabled: !!editing,
    queryFn: async () => {
      const res = await apiFetch(`/suppliers/${editing!.id}/contacts`);
      if (!res.ok) throw new Error("Failed to load contacts");
      return res.json();
    },
  });

  const scorecard = useQuery<SupplierScorecard>({
    queryKey: ["suppliers", editing?.id, "scorecard"],
    enabled: !!editing,
    queryFn: async () => {
      const res = await apiFetch(`/suppliers/${editing!.id}/scorecard`);
      if (!res.ok) throw new Error("Failed to load scorecard");
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
        body: JSON.stringify({
          name: editName,
          email: editEmail || null,
          phone: editPhone || null,
          address_line1: editAddressLine1 || null,
          address_line2: editAddressLine2 || null,
          city: editCity || null,
          state: editState || null,
          postal_code: editPostalCode || null,
          country: editCountry || null,
          payment_terms_days: editPaymentTermsDays ? Number(editPaymentTermsDays) : null,
          lead_time_days: editLeadTimeDays ? Number(editLeadTimeDays) : null,
          tags: editTags,
        }),
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

  const addContact = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("No supplier selected");
      const res = await apiFetch(`/suppliers/${editing.id}/contacts`, {
        method: "POST",
        body: JSON.stringify({
          name: contactName,
          role: contactRole || null,
          email: contactEmail || null,
          phone: contactPhone || null,
          is_primary: false,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to add contact");
      }
      return res.json();
    },
    onSuccess: () => {
      setContactName("");
      setContactRole("");
      setContactEmail("");
      setContactPhone("");
      queryClient.invalidateQueries({ queryKey: ["suppliers", editing?.id, "contacts"] });
    },
    onError: (e: Error) => setContactError(e.message),
  });

  const setContactPrimary = useMutation({
    mutationFn: async (contact: SupplierContactData) => {
      if (!editing) throw new Error("No supplier selected");
      const res = await apiFetch(`/suppliers/${editing.id}/contacts/${contact.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: contact.name,
          role: contact.role,
          email: contact.email,
          phone: contact.phone,
          is_primary: true,
        }),
      });
      if (!res.ok) throw new Error("Failed to update contact");
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suppliers", editing?.id, "contacts"] }),
    onError: (e: Error) => setContactError(e.message),
  });

  const deleteContact = useMutation({
    mutationFn: async (contactId: string) => {
      if (!editing) throw new Error("No supplier selected");
      const res = await apiFetch(`/suppliers/${editing.id}/contacts/${contactId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete contact");
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suppliers", editing?.id, "contacts"] }),
    onError: (e: Error) => setContactError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createSupplier.mutate();
  }

  function openEdit(supplier: SupplierResponse) {
    setEditError(null);
    setContactError(null);
    setEditing(supplier);
    setEditName(supplier.name);
    setEditEmail(supplier.email ?? "");
    setEditPhone(supplier.phone ?? "");
    setEditAddressLine1(supplier.address_line1 ?? "");
    setEditAddressLine2(supplier.address_line2 ?? "");
    setEditCity(supplier.city ?? "");
    setEditState(supplier.state ?? "");
    setEditPostalCode(supplier.postal_code ?? "");
    setEditCountry(supplier.country ?? "");
    setEditPaymentTermsDays(
      supplier.payment_terms_days != null ? String(supplier.payment_terms_days) : "",
    );
    setEditLeadTimeDays(supplier.lead_time_days != null ? String(supplier.lead_time_days) : "");
    setEditTags(supplier.tags);
    setTagInput("");
  }

  function onEditSubmit(e: FormEvent) {
    e.preventDefault();
    setEditError(null);
    updateSupplier.mutate();
  }

  function addTag() {
    const t = tagInput.trim();
    if (t && !editTags.includes(t)) {
      setEditTags([...editTags, t]);
    }
    setTagInput("");
  }

  function removeTag(tag: string) {
    setEditTags(editTags.filter((t) => t !== tag));
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

      {availableTags.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {availableTags.map((t) => (
            <Badge
              key={t}
              variant={tagFilter === t ? "primary" : "neutral"}
              className="cursor-pointer"
              onClick={() => {
                setPage(0);
                setTagFilter(tagFilter === t ? null : t);
              }}
            >
              {t}
            </Badge>
          ))}
        </div>
      )}

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

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="Edit supplier"
        className="max-w-2xl"
      >
        <form onSubmit={onEditSubmit} className="space-y-4">
          {editError && <Alert variant="destructive">{editError}</Alert>}
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name" htmlFor="edit-name">
              <Input
                id="edit-name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                required
              />
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
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Address line 1" htmlFor="edit-addr1">
              <Input
                id="edit-addr1"
                value={editAddressLine1}
                onChange={(e) => setEditAddressLine1(e.target.value)}
              />
            </Field>
            <Field label="Address line 2" htmlFor="edit-addr2">
              <Input
                id="edit-addr2"
                value={editAddressLine2}
                onChange={(e) => setEditAddressLine2(e.target.value)}
              />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-4">
            <Field label="City" htmlFor="edit-city">
              <Input id="edit-city" value={editCity} onChange={(e) => setEditCity(e.target.value)} />
            </Field>
            <Field label="State" htmlFor="edit-state">
              <Input id="edit-state" value={editState} onChange={(e) => setEditState(e.target.value)} />
            </Field>
            <Field label="Postal code" htmlFor="edit-postal">
              <Input
                id="edit-postal"
                value={editPostalCode}
                onChange={(e) => setEditPostalCode(e.target.value)}
              />
            </Field>
            <Field label="Country" htmlFor="edit-country">
              <Input
                id="edit-country"
                value={editCountry}
                onChange={(e) => setEditCountry(e.target.value)}
              />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Payment terms (days)" htmlFor="edit-terms">
              <Input
                id="edit-terms"
                type="number"
                min="0"
                value={editPaymentTermsDays}
                onChange={(e) => setEditPaymentTermsDays(e.target.value)}
              />
            </Field>
            <Field label="Lead time (days)" htmlFor="edit-lead-time">
              <Input
                id="edit-lead-time"
                type="number"
                min="0"
                value={editLeadTimeDays}
                onChange={(e) => setEditLeadTimeDays(e.target.value)}
              />
            </Field>
          </div>
          <Field label="Tags" htmlFor="edit-tag-input">
            <div className="flex gap-2">
              <Input
                id="edit-tag-input"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTag();
                  }
                }}
                placeholder="Add a tag and press Enter"
              />
              <Button type="button" variant="outline" onClick={addTag}>
                Add
              </Button>
            </div>
            {editTags.length > 0 && (
              <div className="flex flex-wrap gap-2 pt-2">
                {editTags.map((t) => (
                  <Badge
                    key={t}
                    variant="neutral"
                    className="cursor-pointer"
                    onClick={() => removeTag(t)}
                  >
                    {t} ×
                  </Badge>
                ))}
              </div>
            )}
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

        {editing && (
          <div className="mt-6 space-y-3 border-t border-border pt-4">
            <h3 className="text-sm font-semibold">Contacts</h3>
            {contactError && <Alert variant="destructive">{contactError}</Alert>}
            {contacts.data && contacts.data.length > 0 && (
              <ul className="space-y-2">
                {contacts.data.map((c) => (
                  <li key={c.id} className="flex items-center justify-between gap-2 text-sm">
                    <div>
                      <span className="font-medium">{c.name}</span>
                      {c.role && <span className="text-muted-foreground"> · {c.role}</span>}
                      {c.email && <span className="text-muted-foreground"> · {c.email}</span>}
                      {c.phone && <span className="text-muted-foreground"> · {c.phone}</span>}
                      {c.is_primary && (
                        <Badge variant="primary" className="ml-2">
                          Primary
                        </Badge>
                      )}
                    </div>
                    <div className="flex gap-2">
                      {!c.is_primary && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          loading={
                            setContactPrimary.isPending && setContactPrimary.variables?.id === c.id
                          }
                          onClick={() => setContactPrimary.mutate(c)}
                        >
                          Make primary
                        </Button>
                      )}
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        loading={deleteContact.isPending && deleteContact.variables === c.id}
                        onClick={() => deleteContact.mutate(c.id)}
                      >
                        Remove
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_1fr_auto] sm:items-end">
              <Field label="Name" htmlFor="contact-name">
                <Input
                  id="contact-name"
                  value={contactName}
                  onChange={(e) => setContactName(e.target.value)}
                />
              </Field>
              <Field label="Role" htmlFor="contact-role">
                <Input
                  id="contact-role"
                  value={contactRole}
                  onChange={(e) => setContactRole(e.target.value)}
                />
              </Field>
              <Field label="Email" htmlFor="contact-email">
                <Input
                  id="contact-email"
                  type="email"
                  value={contactEmail}
                  onChange={(e) => setContactEmail(e.target.value)}
                />
              </Field>
              <Field label="Phone" htmlFor="contact-phone">
                <Input
                  id="contact-phone"
                  value={contactPhone}
                  onChange={(e) => setContactPhone(e.target.value)}
                />
              </Field>
              <Button
                type="button"
                variant="outline"
                loading={addContact.isPending}
                disabled={!contactName.trim()}
                onClick={() => addContact.mutate()}
              >
                Add contact
              </Button>
            </div>
          </div>
        )}

        {editing && scorecard.data && (
          <div className="mt-6 space-y-2 border-t border-border pt-4">
            <h3 className="text-sm font-semibold">Performance</h3>
            {scorecard.data.sample_size === 0 ? (
              <p className="text-sm text-muted-foreground">Not enough data yet.</p>
            ) : (
              <div className="flex gap-4 text-sm">
                <span>
                  On-time delivery:{" "}
                  <strong>
                    {scorecard.data.on_time_pct != null
                      ? `${Math.round(scorecard.data.on_time_pct * 100)}%`
                      : "—"}
                  </strong>
                </span>
                <span>
                  Defect rate:{" "}
                  <strong>
                    {scorecard.data.defect_pct != null
                      ? `${Math.round(scorecard.data.defect_pct * 100)}%`
                      : "—"}
                  </strong>
                </span>
              </div>
            )}
          </div>
        )}
      </Dialog>
    </div>
  );
}
