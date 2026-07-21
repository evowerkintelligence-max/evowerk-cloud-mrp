/** Per-feature column-role requirements for the "readiness" checklist - see
 * docs/superpowers/specs/2026-07-21-data-readiness-design.md for the full
 * rationale, including why AutoML/KPIs/BI/Pace Tracking/Downtime Tracking
 * are excluded from this registry. */

export interface ColumnRole {
  role: string;
  dtype: "numeric" | "datetime" | "any";
  required: boolean;
}

export interface FeatureRequirement {
  key: string;
  label: string;
  /** A DatasetDetail tab name (if isTab) or a route path (if not). */
  to: string;
  isTab: boolean;
  roles: ColumnRole[];
}

const OEE_CORE_ROLES: ColumnRole[] = [
  { role: "planned time", dtype: "numeric", required: true },
  { role: "operating time", dtype: "numeric", required: true },
  { role: "total units", dtype: "numeric", required: true },
  { role: "good units", dtype: "numeric", required: true },
];

export const FEATURE_REQUIREMENTS: FeatureRequirement[] = [
  {
    key: "quality",
    label: "Quality Analyze",
    to: "quality",
    isTab: true,
    roles: [
      { role: "defect column", dtype: "numeric", required: true },
      { role: "batch/lot column", dtype: "any", required: false },
      { role: "timestamp column", dtype: "datetime", required: false },
    ],
  },
  {
    key: "process",
    label: "Process Mining",
    to: "process",
    isTab: true,
    roles: [
      { role: "case column", dtype: "any", required: true },
      { role: "activity column", dtype: "any", required: true },
      { role: "timestamp column", dtype: "datetime", required: true },
    ],
  },
  {
    key: "oee",
    label: "OEE",
    to: "/analytics/oee",
    isTab: false,
    roles: OEE_CORE_ROLES,
  },
  {
    key: "spc",
    label: "SPC",
    to: "/analytics/spc",
    isTab: false,
    roles: [
      { role: "value column", dtype: "numeric", required: true },
      { role: "subgroup column", dtype: "any", required: false },
      { role: "order column", dtype: "any", required: false },
    ],
  },
  {
    key: "throughput",
    label: "Throughput",
    to: "/analytics/throughput",
    isTab: false,
    roles: [
      { role: "units column", dtype: "numeric", required: true },
      { role: "date column", dtype: "datetime", required: false },
      { role: "machine column", dtype: "any", required: false },
    ],
  },
  {
    key: "scrap",
    label: "Scrap & Quality",
    to: "/analytics/scrap",
    isTab: false,
    roles: [
      { role: "total units column", dtype: "numeric", required: true },
      { role: "scrap column", dtype: "numeric", required: true },
      { role: "reason column", dtype: "any", required: false },
      { role: "machine column", dtype: "any", required: false },
      { role: "date column", dtype: "datetime", required: false },
    ],
  },
  {
    key: "downtime",
    label: "Downtime",
    to: "/analytics/downtime",
    isTab: false,
    roles: [
      { role: "duration column", dtype: "numeric", required: true },
      { role: "category column", dtype: "any", required: false },
      { role: "machine column", dtype: "any", required: false },
      { role: "timestamp column", dtype: "datetime", required: false },
    ],
  },
  {
    key: "shifts",
    label: "Shifts",
    to: "/analytics/shifts",
    isTab: false,
    roles: [
      // Approximation: the real backend rule is "at least one metric family is
      // mappable" (an OR across several optional numeric-column groups), not a
      // simple required/optional split - see design doc. Modeled as "needs one
      // numeric column" since every real metric family is numeric.
      { role: "a metric column", dtype: "numeric", required: true },
    ],
  },
  {
    key: "rca",
    label: "Root Cause Analysis",
    to: "/rca-cases",
    isTab: false,
    roles: OEE_CORE_ROLES,
  },
  {
    key: "plant_summary",
    label: "Plant Summary",
    to: "/analytics/plant-summary",
    isTab: false,
    roles: OEE_CORE_ROLES,
  },
  {
    key: "mfg_forecast",
    label: "Manufacturing Forecast",
    to: "/mfg-forecast",
    isTab: false,
    roles: [
      { role: "date column", dtype: "datetime", required: true },
      { role: "target column", dtype: "numeric", required: true },
    ],
  },
];

export interface HealthColumnLike {
  inferred_dtype: string;
}

export interface ReadinessResult {
  key: string;
  label: string;
  to: string;
  isTab: boolean;
  ready: boolean;
  missing: string[];
}

export function computeReadiness(columns: HealthColumnLike[]): ReadinessResult[] {
  const numericCount = columns.filter((c) => c.inferred_dtype === "numeric").length;
  const datetimeCount = columns.filter((c) => c.inferred_dtype === "datetime").length;
  const totalCount = columns.length;

  return FEATURE_REQUIREMENTS.map((req) => {
    const neededNumeric = req.roles.filter((r) => r.required && r.dtype === "numeric").length;
    const neededDatetime = req.roles.filter((r) => r.required && r.dtype === "datetime").length;
    const neededAny = req.roles.filter((r) => r.required && r.dtype === "any").length;

    const missing: string[] = [];
    if (neededNumeric > numericCount) {
      const short = neededNumeric - numericCount;
      missing.push(
        neededNumeric === 1
          ? "needs a numeric column"
          : `needs ${short} more numeric column${short === 1 ? "" : "s"} (have ${numericCount}, need ${neededNumeric})`,
      );
    }
    if (neededDatetime > datetimeCount) {
      const short = neededDatetime - datetimeCount;
      missing.push(
        neededDatetime === 1
          ? "needs a datetime column"
          : `needs ${short} more datetime column${short === 1 ? "" : "s"} (have ${datetimeCount}, need ${neededDatetime})`,
      );
    }
    if (neededAny > totalCount) {
      missing.push(`needs at least ${neededAny} columns (have ${totalCount})`);
    }

    return { key: req.key, label: req.label, to: req.to, isTab: req.isTab, ready: missing.length === 0, missing };
  });
}
