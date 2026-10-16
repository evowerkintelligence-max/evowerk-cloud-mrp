export interface DowntimeCategoryBreakdown {
  category: string;
  total_minutes: number;
  pct: number;
}

export interface ShiftRow {
  shift: string;
  row_count: number;
  total_units: number | null;
  total_scrap: number | null;
  scrap_rate_pct: number | null;
  fpq_pct: number | null;
  availability_pct: number | null;
  quality_pct: number | null;
  oee_pct: number | null;
  total_downtime_min: number | null;
  downtime_by_category: DowntimeCategoryBreakdown[];
}

export interface ShiftMachineRow {
  shift: string;
  machine: string;
  total_units: number | null;
  total_scrap: number | null;
  scrap_rate_pct: number | null;
  fpq_pct: number | null;
  availability_pct: number | null;
  quality_pct: number | null;
  oee_pct: number | null;
  total_downtime_min: number | null;
}

export interface TrendPoint {
  period: string;
  shift: string;
  value: number | null;
}

export interface HourlyPoint {
  shift: string;
  hour: string;
  units: number;
}

export interface ShiftsResult {
  shifts: ShiftRow[];
  best_shift: string | null;
  worst_shift: string | null;
  metrics_available: string[];
  row_count: number;
  invalid_cells: number;
  rows_with_issues: number;
  unparsed_date_rows: number;
  by_machine: ShiftMachineRow[];
  trend: TrendPoint[];
  hourly: HourlyPoint[];
  target_rate: number | null;
}

export const FIELDS = [
  { key: "shift_col", label: "Shift", required: true, hints: [/shift/i, /crew/i, /team/i] },
  { key: "units_col", label: "Units (optional)", hints: [/units_produced/i, /total_unit/i, /output/i, /units/i] },
  { key: "planned_col", label: "Planned time (optional)", hints: [/planned/i, /scheduled/i] },
  { key: "operating_col", label: "Operating time (optional)", hints: [/operating/i, /run_time/i, /uptime/i] },
  { key: "good_units_col", label: "Good units (optional)", hints: [/good_unit/i, /pass/i, /conform/i] },
  { key: "total_units_col", label: "Total units (optional)", hints: [/total_unit/i, /units_produced/i] },
  { key: "scrap_col", label: "Scrap (optional)", hints: [/scrap/i, /defect/i, /reject/i] },
  { key: "downtime_col", label: "Downtime (optional)", hints: [/downtime/i, /down_min/i, /stop/i] },
  { key: "downtime_category_col", label: "Downtime reason (optional)", hints: [/reason/i, /category/i, /downtime_cat/i] },
  { key: "machine_col", label: "Machine (optional)", hints: [/machine/i, /line/i, /asset/i, /equip/i, /station/i] },
  { key: "timestamp_col", label: "Timestamp (optional)", hints: [/timestamp/i, /datetime/i, /date/i, /\bts\b/i, /period/i] },
  { key: "target_rate", label: "Target rate (units/min)", kind: "num" as const },
];
