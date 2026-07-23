import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { CORE_FEATURES, FEATURES, PREMIUM_FEATURES } from "@/lib/features";

interface Customer {
  org_id: string;
  org_name: string;
  owner_email: string;
  enabled_features: string[];
}

function FeatureCheckboxes({
  selected,
  onChange,
}: {
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  function toggle(key: string) {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(next);
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => onChange(new Set(CORE_FEATURES))}>
          Core
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => onChange(new Set(PREMIUM_FEATURES))}>
          Core + Premium
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
        {Object.entries(FEATURES).map(([key, def]) => (
          <label key={key} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={selected.has(key)} onChange={() => toggle(key)} />
            {def.label}
          </label>
        ))}
      </div>
    </div>
  );
}

function NewCustomerForm({ onCreated }: { onCreated: () => void }) {
  const [orgName, setOrgName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [features, setFeatures] = useState<Set<string>>(new Set(CORE_FEATURES));

  const create = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/admin/customers", {
        method: "POST",
        body: JSON.stringify({ org_name: orgName, owner_email: ownerEmail, enabled_features: Array.from(features) }),
      });
      if (!res.ok) throw new Error("Failed to create customer");
      return res.json();
    },
    onSuccess: () => {
      setOrgName("");
      setOwnerEmail("");
      setFeatures(new Set(CORE_FEATURES));
      onCreated();
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>New customer</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="Organization name">
          <Input value={orgName} onChange={(e) => setOrgName(e.target.value)} />
        </Field>
        <Field label="Owner email">
          <Input type="email" value={ownerEmail} onChange={(e) => setOwnerEmail(e.target.value)} />
        </Field>
        <FeatureCheckboxes selected={features} onChange={setFeatures} />
        <Button
          disabled={!orgName || !ownerEmail}
          loading={create.isPending}
          onClick={() => create.mutate()}
        >
          Create customer
        </Button>
      </CardContent>
    </Card>
  );
}

function EditCustomerRow({ customer, onSaved }: { customer: Customer; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [features, setFeatures] = useState<Set<string>>(new Set(customer.enabled_features));

  const update = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/admin/customers/${customer.org_id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled_features: Array.from(features) }),
      });
      if (!res.ok) throw new Error("Failed to update customer");
      return res.json();
    },
    onSuccess: () => {
      setEditing(false);
      onSaved();
    },
  });

  if (!editing) {
    return (
      <TR>
        <TD className="font-medium">{customer.org_name}</TD>
        <TD>{customer.owner_email}</TD>
        <TD>
          {customer.enabled_features.length === 0 ? (
            <span className="text-muted-foreground">None</span>
          ) : (
            customer.enabled_features.map((k) => (
              <Badge key={k} variant="neutral" className="mr-1">
                {FEATURES[k]?.label ?? k}
              </Badge>
            ))
          )}
        </TD>
        <TD>
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            Edit features
          </Button>
        </TD>
      </TR>
    );
  }

  return (
    <TR>
      <TD colSpan={4}>
        <div className="space-y-3 py-2">
          <div className="font-medium">{customer.org_name}</div>
          <FeatureCheckboxes selected={features} onChange={setFeatures} />
          <div className="flex gap-2">
            <Button size="sm" loading={update.isPending} onClick={() => update.mutate()}>
              Save
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      </TD>
    </TR>
  );
}

export function Customers() {
  const qc = useQueryClient();
  const customers = useQuery<Customer[]>({
    queryKey: ["admin-customers"],
    queryFn: async () => {
      const res = await apiFetch("/admin/customers");
      if (!res.ok) throw new Error("Failed to load customers");
      return res.json();
    },
  });

  function refresh() {
    qc.invalidateQueries({ queryKey: ["admin-customers"] });
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Customers" description="Onboard new customers and manage existing customers' feature access." />
      <NewCustomerForm onCreated={refresh} />
      <Card>
        <CardHeader>
          <CardTitle>Existing customers</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Organization</TH>
                  <TH>Owner</TH>
                  <TH>Features</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {customers.data?.map((c) => (
                  <EditCustomerRow key={c.org_id} customer={c} onSaved={refresh} />
                ))}
              </TBody>
            </Table>
          </TableWrap>
        </CardContent>
      </Card>
    </div>
  );
}
