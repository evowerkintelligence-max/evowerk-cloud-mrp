import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReactFlow, Background, Controls, type Node, type Edge, type OnConnect, type OnNodesChange, type OnEdgesDelete,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { apiFetch } from "../../api/client";
import { useDatasets, type Dataset } from "../analytics/shared";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { SqlCodeEditor } from "@/components/SqlCodeEditor";
import { FilterRowsEditor, SelectRowsEditor } from "./FilterRowEditor";
import { autoLayout, wouldCreateCycle } from "./stepLayout";
import {
  STEP_TYPES, blankConfigDraft, configToDraft, draftToConfig, type StepConfigDraft, type StepType,
} from "./stepConfig";
import { WorkflowStepNode, type WorkflowNodeData, type RunNodeStatus } from "./WorkflowStepNode";

const nodeTypes = { workflowStep: WorkflowStepNode };

const FAILURE_POLICIES = ["fail_fast", "continue", "skip_dependents"] as const;

interface StepDraft {
  key: string; // stable React/canvas node id - step.id is user-editable, so it can't double as one
  id: string;
  name: string;
  type: StepType;
  depends_on: string[];
  position: { x: number; y: number };
  config: StepConfigDraft;
}

interface WorkflowDetail {
  id: string;
  name: string;
  description: string | null;
  steps: {
    id: string; name: string; type: string; depends_on: string[]; config: Record<string, unknown>;
    position?: { x: number; y: number };
  }[];
  failure_policy: string;
}

interface RunSummary {
  id: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

interface RunEvent {
  id: string;
  step_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

interface RunDetail extends RunSummary {
  events: RunEvent[];
}

function randomSuffix(): string {
  return Math.random().toString(36).slice(2, 8);
}

function newStep(existingCount: number): StepDraft {
  const id = `step_${randomSuffix()}`;
  return {
    key: id, id, name: "", type: "load_dataset", depends_on: [],
    position: { x: 0, y: existingCount * 140 },
    config: blankConfigDraft(),
  };
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

const STATUS_VARIANT: Record<string, "neutral" | "success" | "danger" | "warning" | "info"> = {
  queued: "neutral", running: "info", success: "success", failed: "danger", partial: "warning",
};

export function WorkflowBuilder() {
  const { id } = useParams();
  const isNew = !id || id === "new";
  const navigate = useNavigate();
  const qc = useQueryClient();
  const datasets = useDatasets();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [failurePolicy, setFailurePolicy] = useState<(typeof FAILURE_POLICIES)[number]>("fail_fast");
  const [steps, setSteps] = useState<StepDraft[]>([newStep(0)]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  const workflow = useQuery<WorkflowDetail>({
    queryKey: ["workflow", id],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${id}`);
      if (!res.ok) throw new Error("Failed to load workflow");
      return res.json();
    },
    enabled: !isNew,
  });

  useEffect(() => {
    if (!workflow.data) return;
    setName(workflow.data.name);
    setDescription(workflow.data.description ?? "");
    setFailurePolicy(workflow.data.failure_policy as (typeof FAILURE_POLICIES)[number]);
    const needsLayout = workflow.data.steps.some((s) => !s.position);
    const layout = needsLayout ? autoLayout(workflow.data.steps) : {};
    setSteps(
      workflow.data.steps.map((s) => ({
        key: `step_${randomSuffix()}`,
        id: s.id,
        name: s.name,
        type: s.type as StepType,
        depends_on: s.depends_on,
        position: s.position ?? layout[s.id] ?? { x: 0, y: 0 },
        config: configToDraft(s.type as StepType, s.config),
      })),
    );
  }, [workflow.data]);

  const runs = useQuery<RunSummary[]>({
    queryKey: ["workflow-runs", id],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${id}/runs`);
      if (!res.ok) throw new Error("Failed to load runs");
      return res.json();
    },
    enabled: !isNew,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 1500 : false,
  });

  const latestRun = runs.data?.[0];
  const latestRunDetail = useQuery<RunDetail>({
    queryKey: ["workflow-run", id, "latest", latestRun?.id],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${id}/runs/${latestRun!.id}`);
      if (!res.ok) throw new Error("Failed to load run");
      return res.json();
    },
    enabled: !isNew && !!latestRun,
    refetchInterval: (query) =>
      query.state.data?.status === "queued" || query.state.data?.status === "running" ? 1500 : false,
  });

  const statusByStepId = useMemo(() => {
    const map: Record<string, { status: RunNodeStatus; error?: string }> = {};
    for (const e of latestRunDetail.data?.events ?? []) {
      if (!e.step_id) continue;
      if (e.event_type === "node_started") map[e.step_id] = { status: "running" };
      if (e.event_type === "node_skipped") map[e.step_id] = { status: "skipped" };
      if (e.event_type === "node_finished") {
        const status = typeof e.payload.status === "string" ? e.payload.status : "success";
        const failed = status !== "success";
        map[e.step_id] = {
          status: failed ? "failed" : "success",
          error: failed && typeof e.payload.error === "string" ? e.payload.error : undefined,
        };
      }
    }
    return map;
  }, [latestRunDetail.data]);

  const runDetail = useQuery<RunDetail>({
    queryKey: ["workflow-run", id, expandedRun],
    queryFn: async () => {
      const res = await apiFetch(`/workflows/${id}/runs/${expandedRun}`);
      if (!res.ok) throw new Error("Failed to load run");
      return res.json();
    },
    enabled: !isNew && !!expandedRun,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1500 : false;
    },
  });

  function buildPayload() {
    return {
      name,
      description: description || null,
      steps: steps.map((s) => ({
        id: s.id, name: s.name || s.id, type: s.type, depends_on: s.depends_on,
        config: draftToConfig(s.type, s.config), position: s.position,
      })),
      failure_policy: failurePolicy,
    };
  }

  const save = useMutation({
    mutationFn: async () => {
      const body = buildPayload();
      const res = await apiFetch(isNew ? "/workflows" : `/workflows/${id}`, {
        method: isNew ? "POST" : "PUT",
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Save failed");
      return res.json() as Promise<WorkflowDetail>;
    },
    onSuccess: (w) => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["workflows"] });
      if (isNew) navigate(`/workflows/${w.id}`, { replace: true });
      else qc.invalidateQueries({ queryKey: ["workflow", id] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const run = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/workflows/${id}/run`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Run failed to start");
      return res.json() as Promise<RunSummary>;
    },
    onSuccess: (r) => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["workflow-runs", id] });
      setExpandedRun(r.id);
    },
    onError: (e: Error) => setError(e.message),
  });

  function removeStep(key: string) {
    setSteps((prev) => {
      const removed = prev.find((s) => s.key === key);
      if (!removed) return prev;
      return prev.filter((s) => s.key !== key).map((s) => ({ ...s, depends_on: s.depends_on.filter((d) => d !== removed.id) }));
    });
    setSelectedKey((k) => (k === key ? null : k));
  }

  function removeDependency(targetKey: string, dependencyId: string) {
    setSteps((prev) => prev.map((s) => (s.key === targetKey ? { ...s, depends_on: s.depends_on.filter((d) => d !== dependencyId) } : s)));
  }

  const nodes: Node[] = useMemo(
    () =>
      steps.map((s) => ({
        id: s.key,
        type: "workflowStep",
        position: s.position,
        selected: s.key === selectedKey,
        data: {
          name: s.name,
          type: s.type,
          status: statusByStepId[s.id]?.status ?? "idle",
          error: statusByStepId[s.id]?.error,
        } satisfies WorkflowNodeData,
      })),
    [steps, selectedKey, statusByStepId],
  );

  const edges: Edge[] = useMemo(
    () =>
      steps.flatMap((s) =>
        s.depends_on
          .map((depId) => steps.find((d) => d.id === depId))
          .filter((d): d is StepDraft => Boolean(d))
          .map((dep) => ({ id: `${dep.key}->${s.key}`, source: dep.key, target: s.key })),
      ),
    [steps],
  );

  const onNodesChange: OnNodesChange = useCallback((changes) => {
    setSteps((prev) => {
      let next = prev;
      for (const change of changes) {
        if (change.type === "position" && change.position) {
          const position = change.position;
          next = next.map((s) => (s.key === change.id ? { ...s, position } : s));
        }
      }
      return next;
    });
  }, []);

  const onConnect: OnConnect = useCallback((connection) => {
    const { source, target } = connection;
    if (!source || !target || source === target) return;
    setSteps((prev) => {
      const targetStep = prev.find((s) => s.key === target);
      const sourceStep = prev.find((s) => s.key === source);
      if (!targetStep || !sourceStep || targetStep.depends_on.includes(sourceStep.id)) return prev;
      if (wouldCreateCycle(prev, targetStep.id, sourceStep.id)) {
        setError("That connection would create a cycle.");
        return prev;
      }
      setError(null);
      return prev.map((s) => (s.key === target ? { ...s, depends_on: [...s.depends_on, sourceStep.id] } : s));
    });
  }, []);

  const onEdgesDelete: OnEdgesDelete = useCallback((deleted) => {
    setSteps((prev) => {
      let next = prev;
      for (const edge of deleted) {
        const sourceStep = prev.find((s) => s.key === edge.source);
        if (!sourceStep) continue;
        next = next.map((s) => (s.key === edge.target ? { ...s, depends_on: s.depends_on.filter((d) => d !== sourceStep.id) } : s));
      }
      return next;
    });
  }, []);

  const selectedStep = steps.find((s) => s.key === selectedKey) ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title={isNew ? "New workflow" : name || "Workflow"}
        description="Drag to connect steps, click a step to configure it, then save and run."
        actions={
          <div className="flex gap-2">
            {!isNew && (
              <Button variant="secondary" loading={run.isPending} onClick={() => run.mutate()}>
                Run workflow
              </Button>
            )}
            <Button loading={save.isPending} onClick={() => save.mutate()}>
              Save
            </Button>
          </div>
        }
      />

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Weekly stock export" />
          </Field>
          <Field label="Failure policy" hint="fail_fast stops the run; skip_dependents skips only downstream steps; continue tries every step regardless.">
            <Select value={failurePolicy} onChange={(e) => setFailurePolicy(e.target.value as typeof failurePolicy)}>
              {FAILURE_POLICIES.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </Select>
          </Field>
          <Field label="Description" className="sm:col-span-2">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="optional" />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pipeline</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button variant="secondary" size="sm" onClick={() => setSteps((prev) => [...prev, newStep(prev.length)])}>
            + Add step
          </Button>
          <div className="flex gap-4">
            <div className="relative h-[32rem] flex-1 rounded-md border border-border">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={() => {}}
                onConnect={onConnect}
                onEdgesDelete={onEdgesDelete}
                onNodeClick={(_, node) => setSelectedKey(node.id)}
                onPaneClick={() => setSelectedKey(null)}
                deleteKeyCode={null}
                fitView
              >
                <Background />
                <Controls />
              </ReactFlow>
            </div>
            {selectedStep && (
              <div className="w-96 shrink-0 space-y-4 overflow-y-auto rounded-md border border-border p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold">Step config</h3>
                  <Button variant="destructive" size="sm" disabled={steps.length === 1} onClick={() => removeStep(selectedStep.key)}>
                    Remove step
                  </Button>
                </div>
                <Field label="Step id">
                  <Input
                    value={selectedStep.id}
                    onChange={(e) => setSteps((prev) => prev.map((s) => (s.key === selectedStep.key ? { ...s, id: e.target.value } : s)))}
                  />
                </Field>
                <Field label="Name">
                  <Input
                    value={selectedStep.name}
                    onChange={(e) => setSteps((prev) => prev.map((s) => (s.key === selectedStep.key ? { ...s, name: e.target.value } : s)))}
                  />
                </Field>
                <Field label="Type">
                  <Select
                    value={selectedStep.type}
                    onChange={(e) =>
                      setSteps((prev) =>
                        prev.map((s) => (s.key === selectedStep.key ? { ...s, type: e.target.value as StepType, config: blankConfigDraft() } : s)),
                      )
                    }
                  >
                    {STEP_TYPES.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Depends on" hint="Drag a connection between steps on the canvas to add a dependency.">
                  {selectedStep.depends_on.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No dependencies yet.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {selectedStep.depends_on.map((depId) => (
                        <Badge key={depId} variant="neutral" className="gap-1">
                          {depId}
                          <button type="button" aria-label={`Remove dependency on ${depId}`} onClick={() => removeDependency(selectedStep.key, depId)}>
                            ×
                          </button>
                        </Badge>
                      ))}
                    </div>
                  )}
                </Field>
                <StepConfigForm
                  step={selectedStep}
                  allSteps={steps}
                  datasets={datasets.data ?? []}
                  onChange={(config) => setSteps((prev) => prev.map((s) => (s.key === selectedStep.key ? { ...s, config } : s)))}
                />
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {!isNew && (
        <Card>
          <CardHeader>
            <CardTitle>Run history</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {(!runs.data || runs.data.length === 0) && (
              <p className="text-sm text-muted-foreground">No runs yet — click "Run workflow" above.</p>
            )}
            {runs.data?.map((r) => (
              <div key={r.id} className="rounded-md border border-border">
                <button
                  className="flex w-full items-center justify-between gap-3 rounded-md px-4 py-2.5 text-left text-sm transition-colors hover:bg-secondary/20"
                  onClick={() => setExpandedRun(expandedRun === r.id ? null : r.id)}
                >
                  <span className="flex items-center gap-2">
                    <Badge variant={STATUS_VARIANT[r.status] ?? "neutral"}>{r.status}</Badge>
                    <span className="text-muted-foreground">{new Date(r.created_at).toLocaleString()}</span>
                  </span>
                  <span className="text-muted-foreground">{expandedRun === r.id ? "Hide" : "Show timeline"}</span>
                </button>
                {expandedRun === r.id && runDetail.data && (
                  <div className="space-y-1.5 border-t border-border px-4 py-3">
                    {runDetail.data.events.map((e) => (
                      <div key={e.id} className="flex items-center gap-2 text-xs">
                        <span className="w-32 shrink-0 text-muted-foreground">
                          {new Date(e.created_at).toLocaleTimeString()}
                        </span>
                        <Badge variant="neutral">{e.event_type}</Badge>
                        {e.step_id && <span className="font-mono">{e.step_id}</span>}
                        {typeof e.payload.status === "string" && (
                          <Badge variant={e.payload.status === "success" ? "success" : "danger"}>
                            {String(e.payload.status)}
                          </Badge>
                        )}
                        {typeof e.payload.error === "string" && (
                          <span className="truncate text-destructive">{e.payload.error}</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function GroupByEditor({ columns, value, onChange }: { columns: string[]; value: string[]; onChange: (v: string[]) => void }) {
  if (columns.length === 0) return <p className="text-xs text-muted-foreground">No columns available yet.</p>;
  return (
    <div className="flex flex-wrap gap-3">
      {columns.map((c) => (
        <label key={c} className="flex items-center gap-1.5 text-sm">
          <Checkbox
            checked={value.includes(c)}
            onChange={(e) => onChange(e.target.checked ? [...value, c] : value.filter((v) => v !== c))}
          />
          {c}
        </label>
      ))}
    </div>
  );
}

function StepConfigForm({
  step, allSteps, datasets, onChange,
}: {
  step: StepDraft;
  allSteps: StepDraft[];
  datasets: Dataset[];
  onChange: (config: StepConfigDraft) => void;
}) {
  const config = step.config;
  function set<K extends keyof StepConfigDraft>(key: K, value: StepConfigDraft[K]) {
    onChange({ ...config, [key]: value });
  }

  const dataset = datasets.find((d) => d.id === config.datasetId);
  // ponytail: upstream-step output columns aren't known without running the
  // step, so filter/aggregate fall back to a free-text column input when
  // reading from an upstream step instead of a dataset-backed <Select> -
  // real column autocomplete there needs output-schema inference, out of
  // scope for this phase.
  const knownColumns = dataset ? dataset.columns.map((c) => c.name) : [];
  const upstreamOptions = allSteps.filter((s) => step.depends_on.includes(s.id));

  if (step.type === "load_dataset") {
    return (
      <div className="space-y-4">
        <Field label="Dataset">
          <Select value={config.datasetId} onChange={(e) => set("datasetId", e.target.value)}>
            <option value="">Select a dataset…</option>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Row limit">
          <Input type="number" value={config.limit} onChange={(e) => set("limit", e.target.value)} />
        </Field>
      </div>
    );
  }

  if (step.type === "sql") {
    return (
      <div className="space-y-4">
        <Field label="Dataset">
          <Select value={config.datasetId} onChange={(e) => set("datasetId", e.target.value)}>
            <option value="">Select a dataset…</option>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="SQL">
          <SqlCodeEditor
            value={config.sql}
            onChange={(v) => set("sql", v)}
            columns={knownColumns.map((cname) => ({ name: cname, dtype: "text" as const }))}
          />
        </Field>
        <Field label="Row limit">
          <Input type="number" value={config.limit} onChange={(e) => set("limit", e.target.value)} />
        </Field>
      </div>
    );
  }

  if (step.type === "filter" || step.type === "aggregate") {
    return (
      <div className="space-y-4">
        <Field label="Source">
          <Select value={config.source} onChange={(e) => set("source", e.target.value as StepConfigDraft["source"])}>
            <option value="dataset">A dataset</option>
            <option value="upstream">Output of an upstream step</option>
          </Select>
        </Field>
        {config.source === "dataset" ? (
          <Field label="Dataset">
            <Select value={config.datasetId} onChange={(e) => set("datasetId", e.target.value)}>
              <option value="">Select a dataset…</option>
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </Select>
          </Field>
        ) : (
          <Field
            label="Upstream step"
            hint={upstreamOptions.length === 0 ? "Connect this step from another step on the canvas first." : undefined}
          >
            <Select value={config.inputStep} onChange={(e) => set("inputStep", e.target.value)}>
              <option value="">Select a step…</option>
              {upstreamOptions.map((s) => (
                <option key={s.id} value={s.id}>{s.name || s.id}</option>
              ))}
            </Select>
          </Field>
        )}
        {step.type === "filter" ? (
          <Field label="Filters">
            <FilterRowsEditor rows={config.filters} columns={knownColumns} onChange={(rows) => set("filters", rows)} />
          </Field>
        ) : (
          <>
            <Field label="Group by" hint="Leave empty to aggregate the whole table into one row.">
              <GroupByEditor columns={knownColumns} value={config.groupBy} onChange={(v) => set("groupBy", v)} />
            </Field>
            <Field label="Aggregations">
              <SelectRowsEditor rows={config.select} columns={knownColumns} onChange={(rows) => set("select", rows)} />
            </Field>
          </>
        )}
        <Field label="Row limit">
          <Input type="number" value={config.limit} onChange={(e) => set("limit", e.target.value)} />
        </Field>
      </div>
    );
  }

  if (step.type === "export_result") {
    return (
      <div className="space-y-4">
        <Field
          label="Upstream step"
          hint={upstreamOptions.length === 0 ? "Connect this step from another step on the canvas first." : undefined}
        >
          <Select value={config.inputStep} onChange={(e) => set("inputStep", e.target.value)}>
            <option value="">Select a step…</option>
            {upstreamOptions.map((s) => (
              <option key={s.id} value={s.id}>{s.name || s.id}</option>
            ))}
          </Select>
        </Field>
        <Field label="Format">
          <Select value={config.format} onChange={(e) => set("format", e.target.value as "csv" | "json")}>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </Select>
        </Field>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Field label="File">
        <input
          type="file"
          className="text-sm"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            const base64 = await fileToBase64(file);
            onChange({ ...config, contentBase64: base64, filename: file.name });
          }}
        />
      </Field>
      {config.filename && <p className="text-xs text-muted-foreground">Selected: {config.filename}</p>}
      <Field label="Dataset name (optional)">
        <Input value={config.name} onChange={(e) => set("name", e.target.value)} placeholder={config.filename || "defaults to filename"} />
      </Field>
    </div>
  );
}
