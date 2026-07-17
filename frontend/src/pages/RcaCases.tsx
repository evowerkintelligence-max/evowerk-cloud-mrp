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
import {
  ColumnChecklist,
  SendToReportButton,
  autoMapColumns,
  useDatasets,
  type EngineField,
} from "@/pages/analytics/shared";
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

interface RcaTrendEntry {
  kind: string;
  key: string;
  count: number;
  case_ids: string[];
  case_titles: string[];
}

async function j<T>(resPromise: Promise<Response>): Promise<T> {
  const res = await resPromise;
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Request failed");
  return res.json();
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong";
}

const STATUS_VARIANT: Record<string, "neutral" | "info" | "success" | "warning"> = {
  open: "neutral",
  investigating: "info",
  actions_open: "warning",
  closed: "success",
  verified: "success",
};

// Valid forward/reset transitions per status - drives the manual status
// control in CaseDetail. investigating/actions_open also auto-advance from
// create_node/create_action on the backend, so most cases never need this;
// it exists for closing, verifying, and correcting a case manually.
const STATUS_TRANSITIONS: Record<string, { value: string; label: string }[]> = {
  open: [
    { value: "investigating", label: "Start investigating" },
    { value: "closed", label: "Close" },
  ],
  investigating: [
    { value: "actions_open", label: "Open actions" },
    { value: "closed", label: "Close" },
  ],
  actions_open: [{ value: "closed", label: "Close" }],
  closed: [
    { value: "verified", label: "Mark verified" },
    { value: "open", label: "Reopen" },
  ],
  verified: [{ value: "open", label: "Reopen" }],
};

// Column-mapping hints shared by Evidence/Timeline/Verify - same convention
// (and same regexes) as the OEE engine screen, so a column named e.g.
// "operating_minutes" auto-fills instead of leaving the user to type it.
const OEE_FIELDS: EngineField[] = [
  { key: "planned_time_col", label: "Planned time", hints: [/planned/i, /plan_time/i, /scheduled/i] },
  { key: "operating_time_col", label: "Operating time", hints: [/operating_time/i, /operating/i, /run_time/i, /uptime/i, /actual_time/i] },
  { key: "total_units_col", label: "Total units", hints: [/total_unit/i, /units_produced/i, /output/i, /total_prod/i] },
  { key: "good_units_col", label: "Good units", hints: [/good_unit/i, /pass/i, /conform/i, /ok_unit/i, /first_pass/i] },
];
const DATE_FIELD: EngineField = { key: "date_col", label: "Date", hints: [/date/i, /time/i, /period/i, /shift_date/i] };
const DIMENSION_HINT = /machine|line|asset|equip|station|shift|operator/i;

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

  const trends = useQuery<{ total_root_causes: number; by_category: RcaTrendEntry[] }>({
    queryKey: ["rca-trends"],
    queryFn: () => j(apiFetch("/rca-cases/trends")),
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
            {create.isError && <Alert variant="destructive">{errorMessage(create.error)}</Alert>}
            <Button disabled={!title || !datasetId} loading={create.isPending} onClick={() => create.mutate()}>
              Create case
            </Button>
          </CardContent>
        </Card>
      )}

      {(trends.data?.by_category.length ?? 0) > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Recurring root causes</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Root causes marked in 2+ cases, grouped by 6M category. A repeat category across investigations usually
              means a systemic fix pays off more than another one-off CAPA action.
            </p>
            {trends.data!.by_category.map((entry) => (
              <div key={entry.key} className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
                <Badge variant="warning">{entry.count}×</Badge>
                <span className="font-medium">{entry.key}</span>
                <span className="text-muted-foreground">in</span>
                {entry.case_ids.map((id, i) => (
                  <button
                    key={id}
                    className="text-primary underline underline-offset-2"
                    onClick={() => onOpen(id)}
                  >
                    {entry.case_titles[i] ?? "case"}
                  </button>
                ))}
              </div>
            ))}
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

const NODE_STATUSES = ["hypothesis", "verified", "ruled_out"] as const;

function CauseTree({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [category, setCategory] = useState("");
  const nodes = useQuery<RcaNode[]>({
    queryKey: ["rca-nodes", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}/nodes`)),
  });
  const taxonomy = useQuery<Record<string, string[]>>({
    queryKey: ["rca-taxonomy"],
    queryFn: () => j(apiFetch("/rca-cases/taxonomy")),
  });

  const addNode = useMutation({
    mutationFn: () =>
      j(
        apiFetch(`/rca-cases/cases/${caseId}/nodes`, {
          method: "POST",
          body: JSON.stringify({ text, node_type: "cause", category: category || null }),
        }),
      ),
    onSuccess: async () => {
      setText("");
      setCategory("");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] }),
        qc.invalidateQueries({ queryKey: ["rca-case", caseId] }),
      ]);
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

  const suggestions = category ? taxonomy.data?.[category] ?? [] : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cause tree</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Classify each cause with the 6M framework (Machine, Method, Material, Manpower, Measurement, Environment) —
          picking a category surfaces common causes below and feeds the recurring-cause trends on the case list.
        </p>
        <div className="grid gap-2 sm:grid-cols-[10rem_14rem_1fr_auto]">
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Category (optional)</option>
            {Object.keys(taxonomy.data ?? {}).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
          <Select
            value=""
            disabled={!category}
            onChange={(e) => {
              if (e.target.value) setText(e.target.value);
            }}
          >
            <option value="">{category ? "Suggested cause…" : "Pick a category first"}</option>
            {suggestions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
          <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a contributing cause…" />
          <Button disabled={!text} loading={addNode.isPending} onClick={() => addNode.mutate()}>
            Add
          </Button>
        </div>
        {addNode.isError && <Alert variant="destructive">{errorMessage(addNode.error)}</Alert>}
        {patchNode.isError && <Alert variant="destructive">{errorMessage(patchNode.error)}</Alert>}
        {deleteNode.isError && <Alert variant="destructive">{errorMessage(deleteNode.error)}</Alert>}

        <div className="space-y-2">
          {(nodes.data ?? []).map((n) => (
            <div key={n.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{n.text}</span>
                <Badge variant={n.node_type === "root_cause" ? "danger" : n.node_type === "problem" ? "neutral" : "info"}>
                  {n.node_type}
                </Badge>
                {n.category && <Badge variant="indigo">{n.category}</Badge>}
              </div>
              {n.node_type !== "problem" && (
                <div className="flex items-center gap-2">
                  <Select
                    className="h-8 w-36 py-1 text-xs"
                    value={n.status}
                    onChange={(e) => patchNode.mutate({ id: n.id, patch: { status: e.target.value } })}
                  >
                    {NODE_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s.replace("_", " ")}
                      </option>
                    ))}
                  </Select>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      patchNode.mutate({ id: n.id, patch: { node_type: n.node_type === "root_cause" ? "cause" : "root_cause" } })
                    }
                  >
                    {n.node_type === "root_cause" ? "Unmark root cause" : "Mark root cause"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (confirm(`Delete cause "${n.text}"?`)) deleteNode.mutate(n.id);
                    }}
                  >
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

function Actions({ caseId, columns }: { caseId: string; columns: string[] }) {
  const qc = useQueryClient();
  const [description, setDescription] = useState("");
  const [verifyCols, setVerifyCols] = useState<Record<string, string>>(() => ({
    ...{ planned_time_col: "", operating_time_col: "", total_units_col: "", good_units_col: "", date_col: "" },
    ...autoMapColumns(OEE_FIELDS, columns),
    ...autoMapColumns([DATE_FIELD], columns),
  }));

  const actions = useQuery<RcaAction[]>({
    queryKey: ["rca-actions", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}/actions`)),
  });

  const addAction = useMutation({
    mutationFn: () =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions`, { method: "POST", body: JSON.stringify({ description }) })),
    onSuccess: async () => {
      setDescription("");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
        qc.invalidateQueries({ queryKey: ["rca-case", caseId] }),
      ]);
    },
  });

  const markDone = useMutation({
    mutationFn: (id: string) =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions/${id}`, { method: "PATCH", body: JSON.stringify({ status: "done" }) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
  });

  const deleteAction = useMutation({
    mutationFn: (id: string) => apiFetch(`/rca-cases/cases/${caseId}/actions/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
  });

  const verify = useMutation({
    mutationFn: (id: string) =>
      j(apiFetch(`/rca-cases/cases/${caseId}/actions/${id}/verify`, { method: "POST", body: JSON.stringify(verifyCols) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-actions", caseId] }),
  });

  const verifyReady = Object.values(verifyCols).every(Boolean);

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
        {addAction.isError && <Alert variant="destructive">{errorMessage(addAction.error)}</Alert>}

        <div>
          <p className="mb-2 text-xs text-muted-foreground">
            Column mapping for "Verify effectiveness" below — compares average OEE before vs. after each action's
            completion date. Auto-filled where a column name matches; check them before verifying.
          </p>
          <div className="grid gap-2 sm:grid-cols-5">
            {[...OEE_FIELDS, DATE_FIELD].map((f) => (
              <Field key={f.key} label={f.label}>
                <Select value={verifyCols[f.key]} onChange={(e) => setVerifyCols({ ...verifyCols, [f.key]: e.target.value })}>
                  <option value="">Select column…</option>
                  {columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
            ))}
          </div>
        </div>
        {verify.isError && <Alert variant="destructive">{errorMessage(verify.error)}</Alert>}
        {markDone.isError && <Alert variant="destructive">{errorMessage(markDone.error)}</Alert>}
        {deleteAction.isError && <Alert variant="destructive">{errorMessage(deleteAction.error)}</Alert>}

        <div className="space-y-2">
          {(actions.data ?? []).map((a) => (
            <div key={a.id} className="rounded-md border border-border px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
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
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={!verifyReady}
                      loading={verify.isPending}
                      onClick={() => verify.mutate(a.id)}
                    >
                      Verify effectiveness
                    </Button>
                  )}
                  {a.status !== "verified" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        if (confirm(`Delete action "${a.description}"?`)) deleteAction.mutate(a.id);
                      }}
                    >
                      Delete
                    </Button>
                  )}
                </div>
              </div>
              {a.status === "done" && !verifyReady && (
                <div className="mt-1 text-xs text-amber-400">Map all 5 columns above to verify effectiveness.</div>
              )}
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
  const qc = useQueryClient();
  const [oeeCols, setOeeCols] = useState<Record<string, string>>(() => ({
    ...{ planned_time_col: "", operating_time_col: "", total_units_col: "", good_units_col: "" },
    ...autoMapColumns(OEE_FIELDS, columns),
  }));
  const [dimensionCols, setDimensionCols] = useState<Set<string>>(
    () => new Set(columns.filter((c) => DIMENSION_HINT.test(c))),
  );
  const [dateCol, setDateCol] = useState(() => autoMapColumns([DATE_FIELD], columns).date_col ?? "");
  const [categoryCol, setCategoryCol] = useState("");
  const [valueCol, setValueCol] = useState("");
  const [addedKeys, setAddedKeys] = useState<Set<string>>(new Set());

  const evidence = useMutation({
    mutationFn: () =>
      j<{ overall_oee: number; candidates: RcaCandidate[]; candidates_total: number }>(
        apiFetch(`/rca-cases/cases/${caseId}/evidence`, {
          method: "POST",
          body: JSON.stringify({ ...oeeCols, dimension_cols: Array.from(dimensionCols), min_support_rows: 5 }),
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

  const addCause = useMutation({
    mutationFn: (payload: { key: string; text: string; evidence: Record<string, unknown>[] }) =>
      j(
        apiFetch(`/rca-cases/cases/${caseId}/nodes`, {
          method: "POST",
          body: JSON.stringify({ text: payload.text, node_type: "cause", evidence: payload.evidence }),
        }),
      ),
    onSuccess: async (_data, payload) => {
      setAddedKeys((prev) => new Set(prev).add(payload.key));
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] }),
        qc.invalidateQueries({ queryKey: ["rca-case", caseId] }),
      ]);
    },
  });

  const evidenceReady = Boolean(oeeCols.planned_time_col && oeeCols.operating_time_col && oeeCols.total_units_col && oeeCols.good_units_col) && dimensionCols.size > 0;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Evidence engine</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            Ranks dimension values (machine, shift, operator…) that run worse than the overall OEE. Confidence combines
            how bad the drop is with how much production volume it represents, 0–100 — a huge drop on 2 rows out of
            20,000 scores low; a moderate drop across a third of production scores high.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {OEE_FIELDS.map((f) => (
              <Field key={f.key} label={f.label}>
                <Select value={oeeCols[f.key]} onChange={(e) => setOeeCols({ ...oeeCols, [f.key]: e.target.value })}>
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
          <div className="space-y-2">
            <div className="text-sm font-medium">Dimension columns to check</div>
            <ColumnChecklist columns={columns} selected={dimensionCols} onChange={setDimensionCols} />
          </div>
          <Button disabled={!evidenceReady} loading={evidence.isPending} onClick={() => evidence.mutate()}>
            Rank candidate causes
          </Button>
          {evidence.isError && <Alert variant="destructive">{errorMessage(evidence.error)}</Alert>}
          {addCause.isError && <Alert variant="destructive">{errorMessage(addCause.error)}</Alert>}
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
                    <TH></TH>
                  </TR>
                </THead>
                <TBody>
                  {evidence.data.candidates.map((c, i) => {
                    const key = `evidence-${c.dimension}-${c.label}`;
                    return (
                      <TR key={i}>
                        <TD>{c.dimension}</TD>
                        <TD className="font-medium">{c.label}</TD>
                        <TD>{c.oee}%</TD>
                        <TD className="text-rose-400">{c.delta_oee}</TD>
                        <TD>{c.support_pct}%</TD>
                        <TD>{c.confidence}</TD>
                        <TD>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={addedKeys.has(key)}
                            loading={addCause.isPending}
                            onClick={() =>
                              addCause.mutate({
                                key,
                                text: `${c.dimension}: ${c.label} (OEE ${c.oee}%, Δ${c.delta_oee}pts)`,
                                evidence: [
                                  {
                                    kind: "candidate",
                                    summary: `${c.label} runs ${Math.abs(c.delta_oee)}pts below overall OEE (confidence ${c.confidence})`,
                                    data: c,
                                  },
                                ],
                              })
                            }
                          >
                            {addedKeys.has(key) ? "Added" : "Add as cause"}
                          </Button>
                        </TD>
                      </TR>
                    );
                  })}
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
          <p className="text-xs text-muted-foreground">
            Classic 80/20: sums the value column per category, sorted highest first, with a running cumulative % — the
            categories at the top of the list before it crosses ~80% are usually where to focus first.
          </p>
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
          {pareto.isError && <Alert variant="destructive">{errorMessage(pareto.error)}</Alert>}
          {pareto.isSuccess && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Category</TH>
                    <TH>Value</TH>
                    <TH>% of total</TH>
                    <TH>Cumulative %</TH>
                    <TH></TH>
                  </TR>
                </THead>
                <TBody>
                  {pareto.data.buckets.map((b, i) => {
                    const key = `pareto-${b.label}`;
                    return (
                      <TR key={i}>
                        <TD>{b.label}</TD>
                        <TD>{b.value.toLocaleString()}</TD>
                        <TD>{b.pct_of_total}%</TD>
                        <TD>{b.cumulative_pct}%</TD>
                        <TD>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={addedKeys.has(key) || b.label.startsWith("Other (")}
                            loading={addCause.isPending}
                            onClick={() =>
                              addCause.mutate({
                                key,
                                text: `${categoryCol}: ${b.label} (${b.pct_of_total}% of total)`,
                                evidence: [
                                  {
                                    kind: "pareto",
                                    summary: `${b.label} accounts for ${b.pct_of_total}% of total ${valueCol}`,
                                    data: b,
                                  },
                                ],
                              })
                            }
                          >
                            {addedKeys.has(key) ? "Added" : "Add as cause"}
                          </Button>
                        </TD>
                      </TR>
                    );
                  })}
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
          <p className="text-xs text-muted-foreground">
            Shows daily OEE for a window of days around the single worst day, so you can spot whether the problem was a
            one-off event or a sustained slide.
          </p>
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
          <Button
            disabled={!dateCol || !oeeCols.planned_time_col || !oeeCols.operating_time_col || !oeeCols.total_units_col || !oeeCols.good_units_col}
            loading={timeline.isPending}
            onClick={() => timeline.mutate()}
          >
            Show timeline around worst day
          </Button>
          {timeline.isError && <Alert variant="destructive">{errorMessage(timeline.error)}</Alert>}
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
        {events.data?.length === 0 && <div className="text-sm text-muted-foreground">No history yet.</div>}
      </CardContent>
    </Card>
  );
}

const TABS = ["cause tree", "actions", "analysis", "history"] as const;

function CaseDetail({ caseId, onBack }: { caseId: string; onBack: () => void }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]>("cause tree");
  const datasets = useDatasets();

  const caseQuery = useQuery<RcaCase>({
    queryKey: ["rca-case", caseId],
    queryFn: () => j(apiFetch(`/rca-cases/cases/${caseId}`)),
  });

  const columns =
    datasets.data?.find((d) => d.id === caseQuery.data?.dataset_id)?.columns.map((c) => c.name) ?? [];

  const updateCase = useMutation({
    mutationFn: (patch: Partial<Pick<RcaCase, "status">>) =>
      j<RcaCase>(apiFetch(`/rca-cases/cases/${caseId}`, { method: "PATCH", body: JSON.stringify(patch) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rca-case", caseId] }),
  });

  const deleteCase = useMutation({
    mutationFn: () => apiFetch(`/rca-cases/cases/${caseId}`, { method: "DELETE" }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["rca-cases"] });
      onBack();
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={caseQuery.data?.title ?? "Case"}
        description={caseQuery.data?.problem_statement}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {caseQuery.data && (
              <SendToReportButton spec={{ type: "rca_case", case_id: caseId }} label={caseQuery.data.title} />
            )}
            <Button variant="secondary" onClick={onBack}>
              Back to cases
            </Button>
            <Button
              variant="ghost"
              className="text-rose-400 hover:text-rose-300"
              loading={deleteCase.isPending}
              onClick={() => {
                if (confirm(`Delete case "${caseQuery.data?.title}"? This cannot be undone.`)) deleteCase.mutate();
              }}
            >
              Delete case
            </Button>
          </div>
        }
      />

      {caseQuery.data && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={STATUS_VARIANT[caseQuery.data.status] ?? "neutral"}>{caseQuery.data.status}</Badge>
          {STATUS_TRANSITIONS[caseQuery.data.status]?.map((t) => (
            <Button
              key={t.value}
              size="sm"
              variant="outline"
              loading={updateCase.isPending}
              onClick={() => updateCase.mutate({ status: t.value })}
            >
              {t.label}
            </Button>
          ))}
        </div>
      )}
      {updateCase.isError && <Alert variant="destructive">{errorMessage(updateCase.error)}</Alert>}
      {deleteCase.isError && <Alert variant="destructive">{errorMessage(deleteCase.error)}</Alert>}

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
      {tab === "actions" && <Actions caseId={caseId} columns={columns} />}
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
