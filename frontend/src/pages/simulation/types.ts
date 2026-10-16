export interface Baseline {
  shifts_per_day: number;
  shift_hours: number;
  workers_per_shift: number;
  avg_wage_per_hour: number;
  throughput_per_shift: number;
  oee_pct: number;
  demand_units_per_day: number;
  defect_rate_pct: number;
  scrap_cost_per_unit: number;
  raw_material_cost_per_unit: number;
  selling_price_per_unit: number;
}

export type Changes = Record<string, number>;

export interface Band {
  p10: number;
  p50: number;
  p90: number;
}

export interface SensitivityEntry {
  parameter: string;
  baseline_value: number;
  low_value: number;
  high_value: number;
  impact: number;
}

export interface SimResult {
  scenario_name: string;
  baseline: Record<string, number>;
  projected: Record<string, number>;
  delta: Record<string, { absolute: number; pct: number; direction: string }>;
  insights: string[];
  bands?: Record<string, Band>;
  sensitivity?: { target_metric: string; ranked: SensitivityEntry[] };
  variance_source?: "dataset" | "default";
}

export interface BaselineDerivation {
  baseline: Partial<Baseline>;
  provenance: { rows: number; period_days: number; oee_pct: number };
  assumptions: string[];
}

export interface SavedSimulation {
  id: string;
  dataset_id: string | null;
  name: string;
  description: string;
  baseline: Partial<Baseline>;
  changes: Changes;
  created_at: string;
  updated_at: string;
}

export interface SimulationRun {
  id: string;
  changes: Changes;
  result: SimResult;
  created_at: string;
}

export interface CompareResult {
  run_a_id: string;
  run_b_id: string;
  delta: Record<string, { absolute: number; pct: number; direction: string }>;
}
