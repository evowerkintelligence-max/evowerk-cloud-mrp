import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import type { SupplierContactData, SupplierResponse } from "./types";

export function SupplierContactsSection({
  editing,
  contactError,
  contacts,
  setContactPrimary,
  deleteContact,
  contactName,
  setContactName,
  contactRole,
  setContactRole,
  contactEmail,
  setContactEmail,
  contactPhone,
  setContactPhone,
  addContact,
}: {
  editing: SupplierResponse | null;
  contactError: string | null;
  contacts: UseQueryResult<SupplierContactData[]>;
  setContactPrimary: UseMutationResult<unknown, Error, SupplierContactData>;
  deleteContact: UseMutationResult<unknown, Error, string>;
  contactName: string;
  setContactName: (value: string) => void;
  contactRole: string;
  setContactRole: (value: string) => void;
  contactEmail: string;
  setContactEmail: (value: string) => void;
  contactPhone: string;
  setContactPhone: (value: string) => void;
  addContact: UseMutationResult<unknown, Error, void>;
}) {
  return (
    <>
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
    </>
  );
}
