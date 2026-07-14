import { useState } from "react";
import { Gauge } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { useDatasets } from "./analytics/shared";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface KpiDef {
  id: string;
  dataset_id: string;
  name: string;
  description: string | null;
  sql: string;
  unit: string | null;
  direction: "higher_is_better" | "lower_is_better" | "neutral";
  enabled: boolean;
  tags: string[];
}

interface KpiComputeItem {
  kpi_id: string;
  value: number | null;
  error: string | null;
  eval: { status: "ok" | "warn" | "breach" | "unknown"; trend: string } | null;
}

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  ok: "success",
  warn: "warning",
  breach: "danger",
  unknown: "neutral",
};

function emptyForm() {
  return {
    name: "",
    description: "",
    dataset_id: "",
    sql: "SELECT AVG(TRY_CAST(value AS DOUBLE)) FROM {table}",
    unit: "",
    direction: "neutral" as KpiDef["direction"],
    tags: "",
  };
}

export function Kpis() {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(emptyForm());
  const [seedDatasetId, setSeedDatasetId] = useState("");
  const [results, setResults] = useState<Record<string, KpiComputeItem>>({});
  const [computingId, setComputingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const kpis = useQuery<KpiDef[]>({
    queryKey: ["kpis"],
    queryFn: async () => {
      const res = await apiFetch("/kpis");
      if (!res.ok) throw new Error("Failed to load KPIs");
      return res.json();
    },
  });

  const datasetName = (id: string) => datasets.data?.find((d) => d.id === id)?.name ?? id;

  const create = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/kpis", {
        method: "POST",
        body: JSON.stringify({
          name: form.name,
          description: form.description || null,
          dataset_id: form.dataset_id,
          sql: form.sql,
          unit: form.unit || null,
          direction: form.direction,
          tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create KPI");
      return res.json();
    },
    onSuccess: () => {
      setFormOpen(false);
      setForm(emptyForm());
      qc.invalidateQueries({ queryKey: ["kpis"] });
    },
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/kpis/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete KPI");
    },
    onSettled: () => {
      setDeletingId(null);
      qc.invalidateQueries({ queryKey: ["kpis"] });
    },
  });

  const compute = useMutation({
    mutationFn: async (kpi: KpiDef) => {
      const res = await apiFetch("/kpis/compute", {
        method: "POST",
        body: JSON.stringify({ dataset_id: kpi.dataset_id, kpi_ids: [kpi.id], force: true, include_eval: true }),
      });
      if (!res.ok) throw new Error("Failed to compute KPI");
      const items: KpiComputeItem[] = await res.json();
      return items[0];
    },
    onMutate: (kpi) => setComputingId(kpi.id),
    onSuccess: (item) => setResults((r) => ({ ...r, [item.kpi_id]: item })),
    onSettled: () => setComputingId(null),
  });

  const seed = useMutation({
    mutationFn: async (datasetId: string) => {
      const res = await apiFetch("/kpis/seed/manufacturing", {
        method: "POST",
        body: JSON.stringify({ dataset_id: datasetId }),
      });
      if (!res.ok) throw new Error("Failed to seed manufacturing KPIs");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["kpis"] }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="KPIs"
        description="Named SQL metrics over a dataset, with cached history and delta/static/baseline breach evaluation."
        actions={
          <div className="flex gap-2">
            <Select
              value={seedDatasetId}
              onChange={(e) => setSeedDatasetId(e.target.value)}
              className="w-48"
            >
              <option value="">Seed templates for…</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </Select>
            <Button
              variant="secondary"
              disabled={!seedDatasetId}
              loading={seed.isPending}
              onClick={() => seedDatasetId && seed.mutate(seedDatasetId)}
            >
              Seed manufacturing KPIs
            </Button>
            <Button onClick={() => setFormOpen(true)}>New KPI</Button>
          </div>
        }
      />

      {kpis.data && kpis.data.length === 0 ? (
        <EmptyState
          icon={<Gauge />}
          title="No KPIs yet"
          description="Define a named SQL metric over a dataset, or seed the manufacturing template set above."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Dataset</TH>
                <TH>Unit</TH>
                <TH>Direction</TH>
                <TH>Value</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {kpis.data?.map((k) => {
                const result = results[k.id];
                return (
                  <TR key={k.id}>
                    <TD className="font-medium">
                      {k.name}
                      {!k.enabled && <Badge variant="neutral" className="ml-2">disabled</Badge>}
                      {k.description && <div className="text-xs text-muted-foreground">{k.description}</div>}
                    </TD>
                    <TD className="text-muted-foreground">{datasetName(k.dataset_id)}</TD>
                    <TD className="text-muted-foreground">{k.unit ?? "—"}</TD>
                    <TD className="text-muted-foreground">{k.direction.replace(/_/g, " ")}</TD>
                    <TD>
                      {result ? (
                        result.error ? (
                          <Badge variant="danger">{result.error}</Badge>
                        ) : (
                          <span className="flex items-center gap-2">
                            <span className="font-medium">{result.value ?? "—"}</span>
                            {result.eval && (
                              <Badge variant={STATUS_VARIANT[result.eval.status]}>{result.eval.status}</Badge>
                            )}
                          </span>
                        )
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TD>
                    <TD className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          loading={computingId === k.id && compute.isPending}
                          onClick={() => compute.mutate(k)}
                        >
                          Compute
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          loading={deletingId === k.id && remove.isPending}
                          onClick={() => {
                            setDeletingId(k.id);
                            remove.mutate(k.id);
                          }}
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

      <Dialog open={formOpen} onClose={() => setFormOpen(false)} title="New KPI">
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
          <Field label="Description">
            <Input value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
          </Field>
          <Field label="Dataset">
            <Select
              value={form.dataset_id}
              onChange={(e) => setForm((f) => ({ ...f, dataset_id: e.target.value }))}
              required
            >
              <option value="">Select a dataset…</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </Select>
          </Field>
          <Field
            label="SQL"
            hint="Must include a {table} placeholder. Dataset columns are text — wrap numeric columns in TRY_CAST(col AS DOUBLE)."
          >
            <textarea
              className="flex min-h-24 w-full rounded-md border border-input bg-background/60 px-3 py-2 font-mono text-sm text-foreground shadow-sm focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              value={form.sql}
              onChange={(e) => setForm((f) => ({ ...f, sql: e.target.value }))}
              required
            />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Unit">
              <Input value={form.unit} onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))} placeholder="%, hrs, units/hr…" />
            </Field>
            <Field label="Direction">
              <Select
                value={form.direction}
                onChange={(e) => setForm((f) => ({ ...f, direction: e.target.value as KpiDef["direction"] }))}
              >
                <option value="neutral">Neutral</option>
                <option value="higher_is_better">Higher is better</option>
                <option value="lower_is_better">Lower is better</option>
              </Select>
            </Field>
          </div>
          <Field label="Tags" hint="Comma-separated">
            <Input value={form.tags} onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))} placeholder="manufacturing, quality" />
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button type="submit" loading={create.isPending}>Create</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
