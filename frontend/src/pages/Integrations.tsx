import { useRef, useState } from "react";
import { Plug } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface IntegrationType {
  id: string;
  label: string;
  category: string;
}

interface IntegrationDef {
  id: string;
  name: string;
  type: string;
  config: Record<string, unknown>;
  status: "untested" | "connected" | "error";
  last_synced_at: string | null;
  last_sync_rows: number | null;
}

const FILE_TYPES = new Set(["csv", "excel"]);

// One-line reminder of the config keys each connector expects — full field-by-field
// forms per connector would be 11x the UI code for a first pass; the config
// textarea below takes raw JSON in the meantime.
const CONFIG_HINTS: Record<string, string> = {
  csv: "No config needed here — upload a file after creating this integration.",
  excel: "No config needed here — upload a file after creating this integration.",
  sqlserver: '{"host": "...", "port": 1433, "database": "...", "username": "...", "password": "...", "query": "SELECT ..."}',
  oracle: '{"host": "...", "port": 1521, "service_name": "...", "username": "...", "password": "...", "query": "SELECT ..."}',
  postgres: '{"host": "...", "port": 5432, "database": "...", "username": "...", "password": "...", "query": "SELECT ..."}',
  mysql: '{"host": "...", "port": 3306, "database": "...", "username": "...", "password": "...", "query": "SELECT ..."}',
  sap_odata: '{"base_url": "https://...", "endpoint": "/sap/opu/odata/...", "auth_type": "basic", "auth_value": "user:pass", "response_path": "value"}',
  erp_rest: '{"base_url": "https://...", "endpoint": "/api/...", "auth_type": "bearer", "auth_value": "token", "response_path": "data"}',
  mes_rest: '{"base_url": "https://...", "endpoint": "/api/...", "auth_type": "none", "response_path": "value"}',
  opcua: '{"server_url": "opc.tcp://host:4840", "node_ids": "ns=2;i=1001,ns=2;i=1002"}',
  mqtt: '{"broker_url": "...", "port": 1883, "topics": "factory/+/temp", "collect_seconds": 5}',
  opcda: '{"server": "Kepware.KEPServerEX.V6", "host": "localhost", "tags": "Channel1.Device1.Tag1"}',
  pi_webapi: '{"base_url": "https://piserver/piwebapi", "username": "...", "password": "...", "tags": "sinusoid,CDT158"}',
};

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  connected: "success",
  error: "danger",
  untested: "neutral",
};

function emptyForm() {
  return { name: "", type: "erp_rest", configText: "{}" };
}

export function Integrations() {
  const qc = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(emptyForm());
  const [formError, setFormError] = useState<string | null>(null);
  const [previewFor, setPreviewFor] = useState<IntegrationDef | null>(null);
  const [previewRows, setPreviewRows] = useState<Record<string, unknown>[] | null>(null);
  const [testMessages, setTestMessages] = useState<Record<string, string>>({});
  const [syncMessages, setSyncMessages] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadTargetId, setUploadTargetId] = useState<string | null>(null);

  const types = useQuery<{ types: IntegrationType[] }>({
    queryKey: ["integration-types"],
    queryFn: async () => {
      const res = await apiFetch("/integrations/types");
      if (!res.ok) throw new Error("Failed to load integration types");
      return res.json();
    },
  });

  const integrations = useQuery<IntegrationDef[]>({
    queryKey: ["integrations"],
    queryFn: async () => {
      const res = await apiFetch("/integrations");
      if (!res.ok) throw new Error("Failed to load integrations");
      return res.json();
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      let config: Record<string, unknown> = {};
      try {
        config = JSON.parse(form.configText || "{}");
      } catch {
        throw new Error("Config must be valid JSON");
      }
      const res = await apiFetch("/integrations", {
        method: "POST",
        body: JSON.stringify({ name: form.name, type: form.type, config }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create integration");
      return res.json();
    },
    onSuccess: () => {
      setFormOpen(false);
      setForm(emptyForm());
      setFormError(null);
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/integrations/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete integration");
    },
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  const test = useMutation({
    mutationFn: async (item: IntegrationDef) => {
      const res = await apiFetch(`/integrations/${item.id}/test`, { method: "POST" });
      if (!res.ok) throw new Error("Test request failed");
      return res.json();
    },
    onMutate: (item) => setBusyId(item.id),
    onSuccess: (data, item) => setTestMessages((m) => ({ ...m, [item.id]: data.message || (data.ok ? "Connected" : "Failed") })),
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  const preview = useMutation({
    mutationFn: async (item: IntegrationDef) => {
      const res = await apiFetch(`/integrations/${item.id}/preview?n=10`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Preview failed");
      return res.json();
    },
    onMutate: (item) => {
      setBusyId(item.id);
      setPreviewFor(item);
    },
    onSuccess: (data) => setPreviewRows(data.rows),
    onError: (e: Error) => setTestMessages((m) => ({ ...m, [previewFor?.id ?? ""]: e.message })),
    onSettled: () => setBusyId(null),
  });

  const sync = useMutation({
    mutationFn: async (item: IntegrationDef) => {
      const res = await apiFetch(`/integrations/${item.id}/sync`, { method: "POST", body: JSON.stringify({}) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Sync failed");
      return res.json();
    },
    onMutate: (item) => setBusyId(item.id),
    onSuccess: (data, item) =>
      setSyncMessages((m) => ({ ...m, [item.id]: `Synced ${data.rows_synced} rows → dataset "${data.dataset.name}"` })),
    onError: (e: Error, item) => setSyncMessages((m) => ({ ...m, [item.id]: e.message })),
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  const upload = useMutation({
    mutationFn: async ({ id, file }: { id: string; file: File }) => {
      const body = new FormData();
      body.append("file", file);
      const res = await apiFetch(`/integrations/${id}/upload`, { method: "POST", body });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Upload failed");
      return res.json();
    },
    onMutate: ({ id }) => setBusyId(id),
    onSuccess: (_data, { id }) => setTestMessages((m) => ({ ...m, [id]: "File uploaded — ready to sync" })),
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Integrations"
        description="Pull data from an external system into a Dataset. REST APIs work directly from the cloud; SQL Server/Oracle/PI need the source network-reachable from this server; OPC-UA/MQTT need the broker/server reachable; OPC-DA is Windows-only."
        actions={<Button onClick={() => setFormOpen(true)}>New integration</Button>}
      />

      {integrations.data && integrations.data.length === 0 ? (
        <EmptyState
          icon={<Plug />}
          title="No integrations yet"
          description="Connect a REST API, database, or industrial data source and sync it into a dataset."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Type</TH>
                <TH>Status</TH>
                <TH>Last sync</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {integrations.data?.map((item) => {
                const typeLabel = types.data?.types.find((t) => t.id === item.type)?.label ?? item.type;
                const isFile = FILE_TYPES.has(item.type);
                return (
                  <TR key={item.id}>
                    <TD className="font-medium">
                      {item.name}
                      {(testMessages[item.id] || syncMessages[item.id]) && (
                        <div className="text-xs text-muted-foreground">
                          {syncMessages[item.id] || testMessages[item.id]}
                        </div>
                      )}
                    </TD>
                    <TD className="text-muted-foreground">{typeLabel}</TD>
                    <TD>
                      <Badge variant={STATUS_VARIANT[item.status]}>{item.status}</Badge>
                    </TD>
                    <TD className="text-muted-foreground">
                      {item.last_synced_at ? `${new Date(item.last_synced_at).toLocaleString()} (${item.last_sync_rows} rows)` : "—"}
                    </TD>
                    <TD className="text-right">
                      <div className="flex justify-end gap-2">
                        {isFile ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            loading={busyId === item.id && upload.isPending}
                            onClick={() => {
                              setUploadTargetId(item.id);
                              fileInputRef.current?.click();
                            }}
                          >
                            Upload file
                          </Button>
                        ) : (
                          <Button
                            variant="secondary"
                            size="sm"
                            loading={busyId === item.id && test.isPending}
                            onClick={() => test.mutate(item)}
                          >
                            Test
                          </Button>
                        )}
                        <Button
                          variant="secondary"
                          size="sm"
                          loading={busyId === item.id && preview.isPending}
                          onClick={() => preview.mutate(item)}
                        >
                          Preview
                        </Button>
                        <Button
                          size="sm"
                          loading={busyId === item.id && sync.isPending}
                          onClick={() => sync.mutate(item)}
                        >
                          Sync
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          loading={busyId === item.id && remove.isPending}
                          onClick={() => remove.mutate(item.id)}
                        >
                          Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept=".csv,.tsv,.xlsx,.xls"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && uploadTargetId) upload.mutate({ id: uploadTargetId, file });
          e.target.value = "";
        }}
      />

      <Dialog open={formOpen} onClose={() => setFormOpen(false)} title="New integration">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
          </Field>
          <Field label="Type">
            <Select
              value={form.type}
              onChange={(e) => setForm((f) => ({ ...f, type: e.target.value, configText: "{}" }))}
            >
              {types.data?.types.map((t) => (
                <option key={t.id} value={t.id}>{t.label} ({t.category})</option>
              ))}
            </Select>
          </Field>
          {!FILE_TYPES.has(form.type) && (
            <Field label="Config (JSON)" hint={CONFIG_HINTS[form.type]}>
              <textarea
                className="flex min-h-32 w-full rounded-md border border-input bg-background/60 px-3 py-2 font-mono text-sm text-foreground shadow-sm focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                value={form.configText}
                onChange={(e) => setForm((f) => ({ ...f, configText: e.target.value }))}
              />
            </Field>
          )}
          {FILE_TYPES.has(form.type) && (
            <p className="text-xs text-muted-foreground">{CONFIG_HINTS[form.type]}</p>
          )}
          {formError && <p className="text-sm text-destructive">{formError}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button type="submit" loading={create.isPending}>Create</Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={previewFor !== null}
        onClose={() => {
          setPreviewFor(null);
          setPreviewRows(null);
        }}
        title={`Preview — ${previewFor?.name ?? ""}`}
        className="max-w-3xl"
      >
        {previewRows && previewRows.length > 0 ? (
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  {Object.keys(previewRows[0]).map((c) => <TH key={c}>{c}</TH>)}
                </TR>
              </THead>
              <TBody>
                {previewRows.map((row, i) => (
                  <TR key={i}>
                    {Object.keys(previewRows[0]).map((c) => <TD key={c}>{String(row[c] ?? "")}</TD>)}
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        ) : (
          <p className="text-sm text-muted-foreground">
            {previewFor && testMessages[previewFor.id] ? testMessages[previewFor.id] : "No rows returned."}
          </p>
        )}
      </Dialog>
    </div>
  );
}
