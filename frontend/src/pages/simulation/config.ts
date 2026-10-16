import type { Baseline, Changes, SimResult } from "./types";

export const DEFAULT_BASELINE: Baseline = {
  shifts_per_day: 2,
  shift_hours: 8,
  workers_per_shift: 25,
  avg_wage_per_hour: 20,
  throughput_per_shift: 1000,
  oee_pct: 75,
  demand_units_per_day: 1800,
  defect_rate_pct: 3.5,
  scrap_cost_per_unit: 5,
  raw_material_cost_per_unit: 15,
  selling_price_per_unit: 40,
};

export const BASELINE_FIELDS: { key: keyof Baseline; label: string; step?: string }[] = [
  { key: "shifts_per_day", label: "Shifts / day" },
  { key: "shift_hours", label: "Shift hours" },
  { key: "workers_per_shift", label: "Workers / shift" },
  { key: "avg_wage_per_hour", label: "Avg wage / hour ($)", step: "0.01" },
  { key: "throughput_per_shift", label: "Throughput / shift (units)" },
  { key: "oee_pct", label: "OEE (%)", step: "0.1" },
  { key: "demand_units_per_day", label: "Demand (units/day)" },
  { key: "defect_rate_pct", label: "Defect rate (%)", step: "0.1" },
  { key: "scrap_cost_per_unit", label: "Scrap cost / unit ($)", step: "0.01" },
  { key: "raw_material_cost_per_unit", label: "Raw material cost / unit ($)", step: "0.01" },
  { key: "selling_price_per_unit", label: "Selling price / unit ($)", step: "0.01" },
];

export const BASELINE_FIELD_LABELS: Record<string, string> = Object.fromEntries(BASELINE_FIELDS.map((f) => [f.key, f.label]));

export const PRESETS: { key: string; label: string; apply: (b: Baseline) => Changes }[] = [
  { key: "add_shift", label: "Add a Shift", apply: (b) => ({ shifts_per_day: b.shifts_per_day + 1 }) },
  { key: "demand_20", label: "Demand +20%", apply: () => ({ demand_change_pct: 20 }) },
  { key: "oee_5", label: "OEE +5 pp", apply: () => ({ oee_change_pct: 5 }) },
  { key: "defect_half", label: "Halve Defect Rate", apply: (b) => ({ defect_change_pct: -(b.defect_rate_pct / 2) }) },
  { key: "workers_10pct", label: "Workers +10%", apply: (b) => ({ workers_per_shift: Math.round(b.workers_per_shift * 1.1) }) },
];

export const CHANGE_FIELDS: { key: string; label: string; step?: string }[] = [
  { key: "shifts_per_day", label: "Shifts / day" },
  { key: "workers_per_shift", label: "Workers / shift" },
  { key: "throughput_per_shift", label: "Throughput / shift" },
  { key: "oee_change_pct", label: "OEE change (pp)", step: "0.1" },
  { key: "demand_units_per_day", label: "Demand (units/day)" },
  { key: "demand_change_pct", label: "Demand change (%)", step: "0.1" },
  { key: "defect_change_pct", label: "Defect rate change (pp)", step: "0.1" },
];

export const METRIC_LABELS: Record<string, string> = {
  daily_capacity: "Daily Capacity (units)",
  good_units_per_day: "Good Units/Day",
  oee_pct: "OEE",
  demand_units_per_day: "Demand (units/day)",
  defect_rate_pct: "Defect Rate",
  labor_cost_per_day: "Labour Cost/Day",
  scrap_loss_per_day: "Scrap Loss/Day",
  utilisation_pct: "Machine Utilisation",
  daily_revenue: "Daily Revenue",
  daily_margin: "Daily Margin",
};
export const CURRENCY_KEYS = new Set(["labor_cost_per_day", "scrap_loss_per_day", "daily_revenue", "daily_margin"]);
export const PCT_KEYS = new Set(["oee_pct", "defect_rate_pct", "utilisation_pct"]);
export const LOWER_BETTER = new Set(["defect_rate_pct", "labor_cost_per_day", "scrap_loss_per_day"]);

export function fmtVal(key: string, v: number) {
  if (v == null || Number.isNaN(v)) return "—";
  if (CURRENCY_KEYS.has(key)) return "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (PCT_KEYS.has(key)) return v.toFixed(1) + "%";
  return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

export function deltaColor(key: string, direction: string) {
  if (direction === "unchanged") return "text-muted-foreground";
  const positive = LOWER_BETTER.has(key) ? direction === "decrease" : direction === "increase";
  return positive ? "text-success" : "text-destructive";
}

export function resultMetricKeys(result: SimResult): string[] {
  return Object.keys(METRIC_LABELS).filter((k) => k in result.baseline);
}

export function resultToReportText(result: SimResult): string {
  const lines = resultMetricKeys(result).map(
    (k) => `${METRIC_LABELS[k]}: ${fmtVal(k, result.baseline[k])} → ${fmtVal(k, result.projected[k])}`,
  );
  return [...lines, "", ...result.insights].join("\n");
}
