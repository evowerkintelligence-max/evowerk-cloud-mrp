import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useDatasets } from "@/pages/analytics/shared";
import { cn } from "@/lib/utils";

interface RcaCase {
  id: string;
  dataset_id: string;
  title: string;
  problem_statement: string;
  status: string;
  owner: string | null;
  created_at: string;
  closed_at: string | null;
}

interface RcaNode {
  id: string;
  parent_id: string | null;
  text: string;
  node_type: string;
  status: string;
  category: string | null;
}

interface RcaAction {
  id: string;
  node_id: string | null;
  description: string;
  owner: string | null;
  status: string;
  completed_at: string | null;
  verification: { before_oee: number; after_oee: number; delta_oee: number } | null;
}

interface RcaEvent {
  id: string;
  event_type: string;
  summary: string;
  actor: string | null;
  created_at: string;
}

interface RcaCandidate {
  dimension: string;
  label: string;
  oee: number;
  delta_oee: number;
  row_count: number;
  support_pct: number;
  confidence: number;
}

interface RcaParetoBucket {
  label: string;
  value: number;
  pct_of_total: number;
  cumulative_pct: number;
}

interface RcaTimelineEvent {
  date: string;
  oee: number;
  delta_oee: number;
  row_count: number;
  is_center: boolean;
}

async function j<T>(resPromise: Promise<Response>): Promise<T> {
  const res = await resPromise;
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Request failed");
  return res.json();
}

const STATUS_VARIANT: Record<string, "neutral" | "info" | "success"> = {
  open: "neutral",
  investigating: "info",
  actions_open: "info",
  closed: "success",
  verified: "success",
};

function CaseList({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [problemStatement, setProblemStatement] = useState("");
  const [datasetId, setDatasetId] = useState("");

  const cases = useQuery<RcaCase[]>({
    queryKey: ["rca-cases"],
    queryFn: () => j(apiFetch("/rca-cases/cases")),
  });

  const create = useMutation({
    mutationFn: () =>
      j<RcaCase>(
        apiFetch("/rca-cases/cases", {
          method: "POST",
          body: JSON.stringify({ title, problem_statement: problemStatement, dataset_id: datasetId }),
        }),
      ),
    onSuccess: async (c) => {
      await qc.invalidateQueries({ queryKey: ["rca-cases"] });
      setShowCreate(false);
      setTitle("");
      setProblemStatement("");
      onOpen(c.id);
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Root Cause Analysis"
        description="Investigate OEE loss with a cause tree, evidence engine, and CAPA action tracking."
        actions={<Button onClick={() => setShowCreate((s) => !s)}>{showCreate ? "Cancel" : "New case"}</Button>}
      />

      {showCreate && (
        <Card>
          <CardHeader>
            <CardTitle>New investigation</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Title">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="M2 underperforming since Monday" />
            </Field>
            <Field label="Problem statement">
              <Input value={problemStatement} onChange={(e) => setProblemStatement(e.target.value)} placeholder="What happened?" />
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
            {create.isError && <Alert variant="destructive">{(create.error as Error).message}</Alert>}
            <Button disabled={!title || !datasetId} loading={create.isPending} onClick={() => create.mutate()}>
              Create case
            </Button>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="p-0">
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Title</TH>
                  <TH>Status</TH>
                  <TH>Owner</TH>
                  <TH>Created</TH>
                </TR>
              </THead>
              <TBody>
                {(cases.data ?? []).map((c) => (
                  <TR key={c.id} className="cursor-pointer" onClick={() => onOpen(c.id)}>
                    <TD className="font-medium">{c.title}</TD>
                    <TD>
                      <Badge variant={STATUS_VARIANT[c.status] ?? "neutral"}>{c.status}</Badge>
                    </TD>
                    <TD>{c.owner ?? "—"}</TD>
                    <TD>{new Date(c.created_at).toLocaleDateString()}</TD>
                  </TR>
                ))}
                {cases.data?.length === 0 && (
                  <TR>
                    <TD colSpan={4} className="text-center text-muted-foreground">
                      No cases yet.
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          </TableWrap>
        </CardContent>
      </Card>
    </div>
  );
}

function CauseTree({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const nodes = useQuery<RcaNode[]>({
    queryKey: ["rca-nodes", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}/nodes`)),
  });

  const addNode = useMutation({
    mutationFn: () =>
      j(apiFetch(`/rca-cases/cases/${caseId}/nodes`, { method: "POST", body: JSON.stringify({ text, node_type: "cause" }) })),
    onSuccess: async () => {
      setText("");
      await qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] });
    },
  });

  const patchNode = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<RcaNode> }) =>
      j(apiFetch(`/rca-cases/cases/${caseId}/nodes/${id}`, { method: "PATCH", body: JSON.stringify(patch) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] }),
  });

  const deleteNode = useMutation({
    mutationFn: (id: string) => apiFetch(`/rca-cases/cases/${caseId}/nodes/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] }),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cause tree</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-2">
          <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a contributing cause…" />
          <Button disabled={!text} loading={addNode.isPending} onClick={() => addNode.mutate()}>
            Add
          </Button>
        </div>
        <div className="space-y-2">
          {(nodes.data ?? []).map((n) => (
            <div key={n.id} className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm">
              <div>
                <span className="font-medium">{n.text}</span>{" "}
                <Badge variant={n.node_type === "root_cause" ? "danger" : n.node_type === "problem" ? "neutral" : "info"}>
                  {n.node_type}
                </Badge>{" "}
                <span className="text-xs text-muted-foreground">{n.status}</span>
              </div>
              {n.node_type !== "problem" && (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      patchNode.mutate({ id: n.id, patch: { node_type: n.node_type === "root_cause" ? "cause" : "root_cause" } })
                    }
                  >
                    {n.node_type === "root_cause" ? "Unmark root cause" : "Mark root cause"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => deleteNode.mutate(n.id)}>
                    Delete
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function Actions({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [description, setDescription] = useState("");
  const [verifyCols, setVerifyCols] = useState({ planned_time_col: "", operating_time_col: "", total_units_col: "", good_units_col: "", date_col: "" });

  const actions = useQuery<RcaAction[]>({
    queryKey: ["rca-actions", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}/actions`)),
  });

  const addAction = useMutation({
    mutationFn: () =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions`, { method: "POST", body: JSON.stringify({ description }) })),
    onSuccess: async () => {
      setDescription("");
      await qc.invalidateQueries({ queryKey: ["rca-actions", caseId] });
    },
  });

  const markDone = useMutation({
    mutationFn: (id: string) =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions/${id}`, { method: "PATCH", body: JSON.stringify({ status: "done" }) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
  });

  const verify = useMutation({
    mutationFn: (id: string) =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions/${id}/verify`, { method: "POST", body: JSON.stringify(verifyCols) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>CAPA actions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Describe the corrective action…" />
          <Button disabled={!description} loading={addAction.isPending} onClick={() => addAction.mutate()}>
            Add
          </Button>
        </div>

        <div className="grid gap-2 sm:grid-cols-5">
          {(["planned_time_col", "operating_time_col", "total_units_col", "good_units_col", "date_col"] as const).map((k) => (
            <Field key={k} label={k.replace(/_/g, " ")}>
              <Input value={verifyCols[k]} onChange={(e) => setVerifyCols({ ...verifyCols, [k]: e.target.value })} placeholder="column name" />
            </Field>
          ))}
        </div>

        <div className="space-y-2">
          {(actions.data ?? []).map((a) => (
            <div key={a.id} className="rounded-md border border-border px-3 py-2 text-sm">
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-medium">{a.description}</span> <Badge variant="neutral">{a.status}</Badge>
                </div>
                <div className="flex gap-2">
                  {a.status === "open" && (
                    <Button size="sm" variant="outline" onClick={() => markDone.mutate(a.id)}>
                      Mark done
                    </Button>
                  )}
                  {a.status === "done" && (
                    <Button size="sm" variant="secondary" loading={verify.isPending} onClick={() => verify.mutate(a.id)}>
                      Verify effectiveness
                    </Button>
                  )}
                </div>
              </div>
              {a.verification && (
                <div className="mt-1 text-xs text-muted-foreground">
                  OEE {a.verification.before_oee}% → {a.verification.after_oee}% ({a.verification.delta_oee > 0 ? "+" : ""}
                  {a.verification.delta_oee}pts)
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function Analysis({ caseId, columns }: { caseId: string; columns: string[] }) {
  const [oeeCols, setOeeCols] = useState({ planned_time_col: "", operating_time_col: "", total_units_col: "", good_units_col: "" });
  const [dimensionCols, setDimensionCols] = useState<string>("");
  const [dateCol, setDateCol] = useState("");
  const [categoryCol, setCategoryCol] = useState("");
  const [valueCol, setValueCol] = useState("");

  const evidence = useMutation({
    mutationFn: () =>
      j<{ overall_oee: number; candidates: RcaCandidate[]; candidates_total: number }>(
        apiFetch(`/rca-cases/cases/${caseId}/evidence`, {
          method: "POST",
          body: JSON.stringify({ ...oeeCols, dimension_cols: dimensionCols.split(",").map((s) => s.trim()).filter(Boolean), min_support_rows: 5 }),
        }),
      ),
  });

  const pareto = useMutation({
    mutationFn: () =>
      j<{ total: number; buckets: RcaParetoBucket[] }>(
        apiFetch(`/rca-cases/cases/${caseId}/pareto`, {
          method: "POST",
          body: JSON.stringify({ category_col: categoryCol, value_col: valueCol, top_n: 15 }),
        }),
      ),
  });

  const timeline = useMutation({
    mutationFn: () =>
      j<{ overall_oee: number; events: RcaTimelineEvent[] }>(
        apiFetch(`/rca-cases/cases/${caseId}/timeline`, {
          method: "POST",
          body: JSON.stringify({ ...oeeCols, date_col: dateCol, window_days: 3 }),
        }),
      ),
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Evidence engine</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(["planned_time_col", "operating_time_col", "total_units_col", "good_units_col"] as const).map((k) => (
              <Field key={k} label={k.replace(/_/g, " ")}>
                <Select value={oeeCols[k]} onChange={(e) => setOeeCols({ ...oeeCols, [k]: e.target.value })}>
                  <option value="">Select…</option>
                  {columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
            ))}
          </div>
          <Field label="Dimension columns (comma-separated)">
            <Input value={dimensionCols} onChange={(e) => setDimensionCols(e.target.value)} placeholder="machine, shift, operator" />
          </Field>
          <Button
            disabled={!oeeCols.planned_time_col || !dimensionCols}
            loading={evidence.isPending}
            onClick={() => evidence.mutate()}
          >
            Rank candidate causes
          </Button>
          {evidence.isError && <Alert variant="destructive">{(evidence.error as Error).message}</Alert>}
          {evidence.isSuccess && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Dimension</TH>
                    <TH>Value</TH>
                    <TH>OEE</TH>
                    <TH>Δ OEE</TH>
                    <TH>Support</TH>
                    <TH>Confidence</TH>
                  </TR>
                </THead>
                <TBody>
                  {evidence.data.candidates.map((c, i) => (
                    <TR key={i}>
                      <TD>{c.dimension}</TD>
                      <TD className="font-medium">{c.label}</TD>
                      <TD>{c.oee}%</TD>
                      <TD className="text-rose-400">{c.delta_oee}</TD>
                      <TD>{c.support_pct}%</TD>
                      <TD>{c.confidence}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pareto</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Category column">
              <Select value={categoryCol} onChange={(e) => setCategoryCol(e.target.value)}>
                <option value="">Select…</option>
                {columns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Value column">
              <Select value={valueCol} onChange={(e) => setValueCol(e.target.value)}>
                <option value="">Select…</option>
                {columns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Button disabled={!categoryCol || !valueCol} loading={pareto.isPending} onClick={() => pareto.mutate()}>
            Run Pareto
          </Button>
          {pareto.isSuccess && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Category</TH>
                    <TH>Value</TH>
                    <TH>% of total</TH>
                    <TH>Cumulative %</TH>
                  </TR>
                </THead>
                <TBody>
                  {pareto.data.buckets.map((b, i) => (
                    <TR key={i}>
                      <TD>{b.label}</TD>
                      <TD>{b.value.toLocaleString()}</TD>
                      <TD>{b.pct_of_total}%</TD>
                      <TD>{b.cumulative_pct}%</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Timeline</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Date column" className="max-w-sm">
            <Select value={dateCol} onChange={(e) => setDateCol(e.target.value)}>
              <option value="">Select…</option>
              {columns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </Field>
          <Button disabled={!dateCol || !oeeCols.planned_time_col} loading={timeline.isPending} onClick={() => timeline.mutate()}>
            Show timeline around worst day
          </Button>
          {timeline.isSuccess && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Date</TH>
                    <TH>OEE</TH>
                    <TH>Δ vs overall</TH>
                    <TH>Rows</TH>
                  </TR>
                </THead>
                <TBody>
                  {timeline.data.events.map((e) => (
                    <TR key={e.date} className={e.is_center ? "bg-primary/5" : ""}>
                      <TD>{e.date}</TD>
                      <TD>{e.oee}%</TD>
                      <TD>{e.delta_oee}</TD>
                      <TD>{e.row_count}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function EventsLog({ caseId }: { caseId: string }) {
  const events = useQuery<RcaEvent[]>({
    queryKey: ["rca-events", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}/events`)),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>History</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {(events.data ?? []).map((e) => (
          <div key={e.id} className="text-sm text-muted-foreground">
            <span className="text-foreground">{e.summary}</span> — {e.actor ?? "system"}, {new Date(e.created_at).toLocaleString()}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

const TABS = ["cause tree", "actions", "analysis", "history"] as const;

function CaseDetail({ caseId, onBack }: { caseId: string; onBack: () => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("cause tree");
  const datasets = useDatasets();

  const caseQuery = useQuery<RcaCase>({
    queryKey: ["rca-case", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}`)),
  });

  const columns =
    datasets.data?.find((d) => d.id === caseQuery.data?.dataset_id)?.columns.map((c) => c.name) ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title={caseQuery.data?.title ?? "Case"}
        description={caseQuery.data?.problem_statement}
        actions={
          <Button variant="secondary" onClick={onBack}>
            Back to cases
          </Button>
        }
      />

      <div className="flex gap-2 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn("px-3 py-2 text-sm capitalize", tab === t ? "border-b-2 border-primary font-medium text-foreground" : "text-muted-foreground")}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "cause tree" && <CauseTree caseId={caseId} />}
      {tab === "actions" && <Actions caseId={caseId} />}
      {tab === "analysis" && <Analysis caseId={caseId} columns={columns} />}
      {tab === "history" && <EventsLog caseId={caseId} />}
    </div>
  );
}

export function RcaCases() {
  const [openCaseId, setOpenCaseId] = useState<string | null>(null);
  return openCaseId ? (
    <CaseDetail caseId={openCaseId} onBack={() => setOpenCaseId(null)} />
  ) : (
    <CaseList onOpen={setOpenCaseId} />
  );
}
