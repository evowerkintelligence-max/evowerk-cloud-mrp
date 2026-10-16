export interface TrendPoint {
  date: string;
  oee: number;
  availability: number;
  performance: number;
  quality: number;
}

export interface Machine {
  machine: string;
  oee: number;
  availability: number;
  performance: number;
  quality: number;
  total_units: number;
  good_units: number;
  invalid_cells: number;
}

export interface RevenueImpact {
  total_lost: number;
  availability_lost: number;
  performance_lost: number;
  quality_lost: number;
  daily_rate: number;
  monthly_rate: number;
  benchmark_saving: number;
  period_days: number;
  period_days_estimated: boolean;
  performance_estimated: boolean;
  per_machine: { machine: string; total_lost: number }[];
}

export interface OeeResult {
  oee: number;
  availability: number;
  performance: number;
  quality: number;
  performance_estimated: boolean;
  total_planned: number;
  total_operating: number;
  total_units: number;
  good_units: number;
  row_count: number;
  invalid_cells: number;
  blank_cells: number;
  rows_with_issues: number;
  availability_clamped: boolean;
  quality_clamped: boolean;
  date_gap_days: number;
  duplicate_rows: number;
  trend: TrendPoint[] | null;
  machines: Machine[] | null;
  revenue_impact: RevenueImpact | null;
  teep: { teep: number; utilization: number; calendar_days: number; machine_count: number } | null;
  six_big_losses: {
    total_loss_minutes: number;
    buckets: { key: string; label: string; family: string; minutes: number; units: number | null; cost: number | null; pct_of_loss: number }[];
  } | null;
}
