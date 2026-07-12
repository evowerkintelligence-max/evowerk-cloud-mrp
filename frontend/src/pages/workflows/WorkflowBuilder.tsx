import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { useDatasets } from "../analytics/shared";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";

const STEP_TYPES = [
  "sql", "filter", "aggregate", "load_dataset",
  "export_result", "import_csv", "import_excel", "import_json",
] as const;

const FAILURE_POLICIES = ["fail_fast", "continue", "skip_dependents"] as const;

interface StepDraft {
  key: string; // stable React key - step.id is user-editable, so it can't double as one
  id: string;
  name: string;
  type: (typeof STEP_TYPES)[number];
  depends_on: string[];
  configText: string; // ponytail: raw JSON box instead of a per-step-type form -
  // 8 step types x bespoke fields is a lot of UI for phase 1; add typed forms
  // per step type when a real user asks for one.
}

interface WorkflowDetail {
  id: string;
  name: string;
  description: string | null;
  steps: { id: string; name: string; type: string; depends_on: string[]; config: Record<string, unknown> }[];
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

function newStep(): StepDraft {
  const id = `step_${randomSuffix()}`;
  return { key: id, id, name: "", type: "load_dataset", depends_on: [], configText: "{}" };
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
  const [steps, setSteps] = useState<StepDraft[]>([newStep()]);
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
    setSteps(
      workflow.data.steps.map((s) => ({
        key: `step_${randomSuffix()}`, id: s.id, name: s.name, type: s.type as StepDraft["type"],
        depends_on: s.depends_on, configText: JSON.stringify(s.config, null, 2),
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
    // Keeps each row's status badge live while a run is in flight, even if
    // its timeline isn't expanded (that one polls separately, see runDetail).
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 1500 : false,
  });

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
    const parsedSteps = steps.map((s) => {
      let config: Record<string, unknown>;
      try {
        config = JSON.parse(s.configText || "{}");
      } catch {
        throw new Error(`Step "${s.name || s.id}" has invalid JSON config`);
      }
      return { id: s.id, name: s.name || s.id, type: s.type, depends_on: s.depends_on, config };
    });
    return { name, description: description || null, steps: parsedSteps, failure_policy: failurePolicy };
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

  function onSave() {
    try {
      save.mutate();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const stepIds = steps.map((s) => s.id);

  return (
    <div className="space-y-6">
      <PageHeader
        title={isNew ? "New workflow" : name || "Workflow"}
        description="Add steps, wire dependencies between them, then save and run."
        actions={
          <div className="flex gap-2">
            {!isNew && (
              <Button variant="secondary" loading={run.isPending} onClick={() => run.mutate()}>
                Run workflow
              </Button>
            )}
            <Button loading={save.isPending} onClick={onSave}>
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
          <CardTitle>Steps</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {datasets.data && datasets.data.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Dataset ids for use in step config: {" "}
              {datasets.data.map((d) => `${d.name}=${d.id}`).join(" · ")}
            </p>
          )}

          {steps.map((step, i) => (
            <div key={step.key} className="space-y-3 rounded-md border border-border p-4">
              <div className="flex flex-wrap items-end gap-3">
                <Field label="Step id" className="min-w-[10rem]">
                  <Input
                    value={step.id}
                    onChange={(e) =>
                      setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, id: e.target.value } : s)))
                    }
                  />
                </Field>
                <Field label="Name" className="min-w-[12rem]">
                  <Input
                    value={step.name}
                    onChange={(e) =>
                      setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, name: e.target.value } : s)))
                    }
                  />
                </Field>
                <Field label="Type" className="min-w-[10rem]">
                  <Select
                    value={step.type}
                    onChange={(e) =>
                      setSteps((prev) =>
                        prev.map((s, idx) => (idx === i ? { ...s, type: e.target.value as StepDraft["type"] } : s)),
                      )
                    }
                  >
                    {STEP_TYPES.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </Select>
                </Field>
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={steps.length === 1}
                  onClick={() => setSteps((prev) => prev.filter((_, idx) => idx !== i))}
                >
                  Remove
                </Button>
              </div>

              <div className="space-y-1">
                <div className="text-xs font-medium text-muted-foreground">Depends on</div>
                <div className="flex flex-wrap gap-3">
                  {stepIds.filter((sid) => sid !== step.id).map((sid) => (
                    <label key={sid} className="flex items-center gap-1.5 text-sm">
                      <Checkbox
                        checked={step.depends_on.includes(sid)}
                        onChange={(e) =>
                          setSteps((prev) =>
                            prev.map((s, idx) =>
                              idx === i
                                ? {
                                    ...s,
                                    depends_on: e.target.checked
                                      ? [...s.depends_on, sid]
                                      : s.depends_on.filter((d) => d !== sid),
                                  }
                                : s,
                            ),
                          )
                        }
                      />
                      {sid}
                    </label>
                  ))}
                  {stepIds.filter((sid) => sid !== step.id).length === 0 && (
                    <span className="text-xs text-muted-foreground">No other steps to depend on yet.</span>
                  )}
                </div>
              </div>

              <Field
                label="Config (JSON)"
                hint='e.g. {"dataset_id": "…", "filters": [{"column":"region","op":"eq","value":"west"}]} — filter/aggregate/export_result can instead read "input_step": "<step id>" for chained steps.'
              >
                <textarea
                  className="h-28 w-full rounded-md border border-input bg-background/60 p-2 font-mono text-xs"
                  value={step.configText}
                  onChange={(e) =>
                    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, configText: e.target.value } : s)))
                  }
                />
              </Field>
            </div>
          ))}

          <Button variant="secondary" size="sm" onClick={() => setSteps((prev) => [...prev, newStep()])}>
            + Add step
          </Button>
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
                  className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm"
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
                          <span className="truncate text-rose-300">{e.payload.error}</span>
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
