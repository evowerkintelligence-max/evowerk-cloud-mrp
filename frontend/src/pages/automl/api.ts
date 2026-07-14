import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";

export const TASKS = ["classification", "regression", "forecasting"] as const;
export type AutoMLTaskType = (typeof TASKS)[number];

export const THOROUGHNESS = ["fast", "balanced", "thorough"] as const;

export interface HoldoutScores {
  accuracy?: number;
  f1?: number;
  rmse?: number;
  mae?: number;
  r2?: number;
}

export interface RegressionDiagnostics {
  type: "regression";
  points: [number, number][];
  residual_std: number;
}

export interface ClassificationDiagnostics {
  type: "classification";
  labels: string[];
  confusion: number[][];
  per_class: { label: string; precision: number; recall: number; support: number }[];
  truncated: boolean;
}

export interface LeaderboardEntry {
  rank: number;
  model_name: string;
  label: string;
  is_winner: boolean;
  selection_score: number;
  primary_metric: string;
  holdout_scores: HoldoutScores;
  train_seconds: number;
  params: Record<string, unknown>;
  diagnostics: RegressionDiagnostics | ClassificationDiagnostics | Record<string, never>;
  full_data_fit: boolean;
}

export interface AutoMLRunSummary {
  id: string;
  name: string;
  task: AutoMLTaskType;
  status: "queued" | "running" | "success" | "failed";
  dataset_id: string;
  target_column: string;
  best_model_name: string | null;
  metric_name: string | null;
  metric_value: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AutoMLRunDetail extends AutoMLRunSummary {
  feature_columns: string[];
  config: {
    thoroughness: string;
    algorithms: string[] | null;
    handle_class_imbalance: boolean;
    time_column: string | null;
    horizon: number;
    forecast_strategy: "direct" | "recursive";
    time_budget_seconds: number | null;
    search_method: "random" | "bayesian";
  };
  leaderboard: LeaderboardEntry[] | null;
  meta: Record<string, unknown> | null;
}

export interface AlgorithmOption {
  id: string;
  label: string;
}

export interface TrainRequest {
  name: string;
  dataset_id: string;
  task: AutoMLTaskType;
  target_column: string;
  feature_columns: string[];
  thoroughness: string;
  algorithms: string[] | null;
  handle_class_imbalance: boolean;
  time_column: string | null;
  horizon: number;
  forecast_strategy: "direct" | "recursive";
  time_budget_seconds: number | null;
  search_method: "random" | "bayesian";
}

const RUN_STATUS_ACTIVE = (status: string) => status === "queued" || status === "running";

export function useAutoMLRuns() {
  return useQuery<AutoMLRunSummary[]>({
    queryKey: ["automl-runs"],
    queryFn: async () => {
      const res = await apiFetch("/automl/runs");
      if (!res.ok) throw new Error("Failed to load AutoML runs");
      return res.json();
    },
    refetchInterval: (query) => (query.state.data?.some((r) => RUN_STATUS_ACTIVE(r.status)) ? 1500 : false),
  });
}

export function useAutoMLRun(id: string | undefined) {
  return useQuery<AutoMLRunDetail>({
    queryKey: ["automl-run", id],
    queryFn: async () => {
      const res = await apiFetch(`/automl/runs/${id}`);
      if (!res.ok) throw new Error("Failed to load run");
      return res.json();
    },
    enabled: !!id,
    refetchInterval: (query) => (RUN_STATUS_ACTIVE(query.state.data?.status ?? "") ? 1500 : false),
  });
}

export function useAutoMLRunsDetail(ids: string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: ["automl-run", id],
      queryFn: async () => {
        const res = await apiFetch(`/automl/runs/${id}`);
        if (!res.ok) throw new Error("Failed to load run");
        return res.json() as Promise<AutoMLRunDetail>;
      },
    })),
  });
}

export function useAlgorithms(task: AutoMLTaskType) {
  return useQuery<AlgorithmOption[]>({
    queryKey: ["automl-algorithms", task],
    queryFn: async () => {
      const res = await apiFetch(`/automl/algorithms?task=${task}`);
      if (!res.ok) throw new Error("Failed to load algorithms");
      return res.json();
    },
  });
}

export function useCreateAutoMLRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: TrainRequest) => {
      const res = await apiFetch("/automl/runs", { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to start run");
      return res.json() as Promise<AutoMLRunDetail>;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["automl-runs"] }),
  });
}

export function useDeleteAutoMLRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/automl/runs/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete run");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["automl-runs"] }),
  });
}

export interface ExplainResult {
  top_features: { feature: string; importance: number | null }[];
  narrative: string;
}

export function useExplain(id: string | undefined, enabled: boolean) {
  return useQuery<ExplainResult>({
    queryKey: ["automl-explain", id],
    queryFn: async () => {
      const res = await apiFetch(`/automl/runs/${id}/explain`);
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to load explanation");
      return res.json();
    },
    enabled: enabled && !!id,
  });
}

export interface RowExplainResult {
  prediction: string | number;
  probabilities: Record<string, number> | null;
  base_value: number;
  contributions: { feature: string; value: string | number | null; contribution: number }[];
  confidence:
    | { lower: number; upper: number; level: number; method: string }
    | { confidence_label: "high" | "medium" | "low"; margin: number }
    | null;
}

export function useExplainRow(id: string | undefined) {
  return useMutation({
    mutationFn: async (row: Record<string, string>) => {
      const res = await apiFetch(`/automl/runs/${id}/explain-row`, { method: "POST", body: JSON.stringify({ row }) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to explain row");
      return res.json() as Promise<RowExplainResult>;
    },
  });
}

export interface PartialDependenceResult {
  feature: string;
  is_numeric: boolean;
  grid: (number | string)[];
  response: number[];
  rows_used: number;
}

export function usePartialDependence(id: string | undefined, feature: string | null) {
  return useQuery<PartialDependenceResult>({
    queryKey: ["automl-pd", id, feature],
    queryFn: async () => {
      const res = await apiFetch(`/automl/runs/${id}/partial-dependence?feature=${encodeURIComponent(feature!)}`);
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to load partial dependence");
      return res.json();
    },
    enabled: !!id && !!feature,
  });
}

export interface ForecastTrajectoryResult {
  target_column: string;
  time_column: string;
  steps: number;
  points: { step: number; time: string; prediction: number; lower?: number; upper?: number }[];
  history: { time: string; actual: number }[];
  interval: { level: number; method: string } | null;
  notes: string[];
}

export function useForecastTrajectory(id: string | undefined, enabled: boolean) {
  return useQuery<ForecastTrajectoryResult>({
    queryKey: ["automl-forecast-trajectory", id],
    queryFn: async () => {
      const res = await apiFetch(`/automl/runs/${id}/forecast-trajectory`);
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to load forecast trajectory");
      return res.json();
    },
    enabled: enabled && !!id,
  });
}

export interface PredictResult {
  preview: Record<string, unknown>[];
  columns: string[];
  rows: number;
  notes: string[];
}

export function usePredict(id: string | undefined) {
  return useMutation({
    mutationFn: async (datasetId: string) => {
      const res = await apiFetch(`/automl/runs/${id}/predict`, {
        method: "POST",
        body: JSON.stringify({ dataset_id: datasetId }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Predict failed");
      return res.json() as Promise<PredictResult>;
    },
  });
}

export interface DriftFeature {
  feature: string;
  psi: number;
  severity: "low" | "moderate" | "high";
  type: "numeric" | "categorical";
}

export interface DriftResult {
  overall_severity: "low" | "moderate" | "high";
  features: DriftFeature[];
  skipped_features: string[];
  rows_compared: number;
  train_sample_rows: number;
}

export function useDriftCheck(id: string | undefined) {
  return useMutation({
    mutationFn: async (datasetId: string) => {
      const res = await apiFetch(`/automl/runs/${id}/drift-check`, {
        method: "POST",
        body: JSON.stringify({ dataset_id: datasetId }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Drift check failed");
      return res.json() as Promise<DriftResult>;
    },
  });
}
