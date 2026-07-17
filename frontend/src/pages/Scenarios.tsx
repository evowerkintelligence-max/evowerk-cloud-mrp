import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { SendToReportButton, useDatasets, useQueryBuilder, QueryBuilderFields } from "@/pages/analytics/shared";

interface Scenario {
  id: string;
  dataset_id: string;
  name: string;
  description: string;
  base_query: Record<string, unknown>;
}

async function j<T>(resPromise: Promise<Response>): Promise<T> {
  const res = await resPromise;
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Request failed");
  return res.json();
}

function CreateScenarioForm({ onCreated }: { onCreated: () => void }) {
  const datasets = useDatasets();
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const names = useMemo(() => datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [], [datasets.data, datasetId]);
  const qb = useQueryBuilder(datasetId, names);

  const create = useMutation({
    mutationFn: () =>
      j<Scenario>(
        apiFetch("/scenarios", {
          method: "POST",
          body: JSON.stringify({ name, dataset_id: datasetId, base_query: qb.toRequestBody() }),
        }),
      ),
    onSuccess: () => {
      setName("");
      onCreated();
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>New scenario</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Base case" />
          </Field>
          <Field label="Dataset">
            <Select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
              <option value="">Select a dataset…</option>
              {(datasets.data ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {datasetId && <QueryBuilderFields qb={qb} names={names} />}
        {create.isError && <Alert variant="destructive">{(create.error as Error).message}</Alert>}
        <Button disabled={!name || !datasetId} loading={create.isPending} onClick={() => create.mutate()}>
          Create scenario
        </Button>
      </CardContent>
    </Card>
  );
}

function ScenarioList() {
  const qc = useQueryClient();
  const scenarios = useQuery<Scenario[]>({ queryKey: ["scenarios"], queryFn: () => j(apiFetch("/scenarios")) });
  const [ranScenarioId, setRanScenarioId] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const del = useMutation({
    mutationFn: (id: string) => apiFetch(`/scenarios/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scenarios"] }),
  });

  const run = useMutation({
    mutationFn: (id: string) => j<{ params: object; result: { columns: string[]; rows: Record<string, unknown>[] } }[]>(apiFetch(`/scenarios/${id}/execute`, { method: "POST", body: JSON.stringify({ param_sets: [{}] }) })),
    onSuccess: (_data, id) => setRanScenarioId(id),
  });

  async function downloadLatestRun(fmt: "csv" | "xlsx") {
    if (!ranScenarioId) return;
    setDownloading(true);
    try {
      const runs = await j<{ id: string }[]>(apiFetch(`/scenarios/${ranScenarioId}/runs`));
      if (!runs.length) return;
      const res = await apiFetch(`/scenarios/${ranScenarioId}/runs/${runs[0].id}/export?fmt=${fmt}`);
      if (!res.ok) throw new Error("Export failed");
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `scenario-run.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Scenarios</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Description</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {(scenarios.data ?? []).map((s) => (
                <TR key={s.id}>
                  <TD className="font-medium">{s.name}</TD>
                  <TD className="text-muted-foreground">{s.description || "—"}</TD>
                  <TD className="flex gap-2">
                    <Button size="sm" variant="secondary" loading={run.isPending} onClick={() => run.mutate(s.id)}>
                      Run
                    </Button>
                    <SendToReportButton
                      spec={{ type: "table", dataset_id: s.dataset_id, query: s.base_query }}
                      label={s.name}
                    />
                    <Button size="sm" variant="ghost" onClick={() => del.mutate(s.id)}>
                      Delete
                    </Button>
                  </TD>
                </TR>
              ))}
              {scenarios.data?.length === 0 && (
                <TR>
                  <TD colSpan={3} className="text-center text-muted-foreground">
                    No scenarios yet.
                  </TD>
                </TR>
              )}
            </TBody>
          </Table>
        </TableWrap>

        {run.isSuccess && run.data[0] && (
          <>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" loading={downloading} onClick={() => downloadLatestRun("csv")}>
                Download CSV
              </Button>
              <Button size="sm" variant="secondary" loading={downloading} onClick={() => downloadLatestRun("xlsx")}>
                Download XLSX
              </Button>
            </div>
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    {run.data[0].result.columns.map((c) => (
                      <TH key={c}>{c}</TH>
                    ))}
                  </TR>
                </THead>
                <TBody>
                  {run.data[0].result.rows.map((row, i) => (
                    <TR key={i}>
                      {run.data![0].result.columns.map((c) => (
                        <TD key={c}>{String(row[c])}</TD>
                      ))}
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function Scenarios() {
  const qc = useQueryClient();
  return (
    <div className="space-y-6">
      <PageHeader title="Scenarios" description="Save a query as a what-if scenario, re-run it with parameter overrides, and compare results." />
      <CreateScenarioForm onCreated={() => qc.invalidateQueries({ queryKey: ["scenarios"] })} />
      <ScenarioList />
    </div>
  );
}
