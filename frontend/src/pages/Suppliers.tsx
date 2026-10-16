import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { SupplierContactsSection } from "./suppliers/SupplierContactsSection";
import { SupplierScorecardSection } from "./suppliers/SupplierScorecardSection";
import { SupplierTagsField } from "./suppliers/SupplierTagsField";
import { SuppliersTable } from "./suppliers/SuppliersTable";
import type { SupplierContactData, SupplierResponse, SupplierScorecard } from "./suppliers/types";

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

      <SuppliersTable suppliers={suppliers} openEdit={openEdit} setSupplierActive={setSupplierActive} />

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
          <SupplierTagsField
            tagInput={tagInput}
            setTagInput={setTagInput}
            addTag={addTag}
            editTags={editTags}
            removeTag={removeTag}
          />
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={updateSupplier.isPending}>
              Save
            </Button>
          </div>
        </form>

        <SupplierContactsSection
          editing={editing}
          contactError={contactError}
          contacts={contacts}
          setContactPrimary={setContactPrimary}
          deleteContact={deleteContact}
          contactName={contactName}
          setContactName={setContactName}
          contactRole={contactRole}
          setContactRole={setContactRole}
          contactEmail={contactEmail}
          setContactEmail={setContactEmail}
          contactPhone={contactPhone}
          setContactPhone={setContactPhone}
          addContact={addContact}
        />

        <SupplierScorecardSection editing={editing} scorecard={scorecard} />
      </Dialog>
    </div>
  );
}
