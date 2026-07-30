/** Mirrors backend/app/core/features.py - keep these two files' keys and tiers
 * identical. backend/tests/test_feature_registry_sync.py fails the build if
 * they drift; there is no automated codegen between the Python and TS files. */

export interface FeatureDef {
  label: string;
  tier: "core" | "premium";
}

export const FEATURES: Record<string, FeatureDef> = {
  boms: { label: "Bills of Materials", tier: "core" },
  procurement: { label: "Procurement", tier: "core" },
  sales: { label: "Sales Orders", tier: "core" },
  production: { label: "Production", tier: "core" },
  lots: { label: "Lots", tier: "core" },
  warehouse: { label: "Warehouse", tier: "core" },
  planning: { label: "Planning", tier: "core" },
  plant_summary: { label: "Plant Summary", tier: "core" },
  oee: { label: "OEE", tier: "core" },
  pace_tracking: { label: "Pace Tracking", tier: "core" },
  downtime: { label: "Downtime", tier: "core" },
  downtime_tracking: { label: "Downtime Tracking", tier: "core" },
  throughput: { label: "Throughput", tier: "core" },
  scrap: { label: "Scrap & Quality", tier: "core" },
  shifts: { label: "Shifts", tier: "core" },
  spc: { label: "SPC", tier: "core" },
  kpis: { label: "KPIs", tier: "core" },
  kpi_dashboards: { label: "KPI Dashboards", tier: "core" },
  reports: { label: "Report Builder", tier: "core" },
  data_chat: { label: "Ask (AI data chat)", tier: "core" },
  automl: { label: "AutoML", tier: "premium" },
  predictive: { label: "Predictive Maintenance", tier: "premium" },
  simulation: { label: "Simulation", tier: "premium" },
  rca_cases: { label: "Root Cause Analysis", tier: "premium" },
  mfg_forecast: { label: "Manufacturing Forecast", tier: "premium" },
};

export const CORE_FEATURES: string[] = Object.entries(FEATURES)
  .filter(([, v]) => v.tier === "core")
  .map(([k]) => k);

export const PREMIUM_FEATURES: string[] = Object.keys(FEATURES);
