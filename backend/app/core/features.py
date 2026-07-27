"""Per-org feature entitlements. See docs/superpowers/specs/2026-07-23-feature-entitlements-onboarding-design.md
for the full rationale, including why System-section and Data-exploration routers
(datasets, bi, integrations, realtime, workflows, alerts, quality, process_mining,
ai_insights, and the MRP master-data routers items/suppliers/locations/machines/
work_centers/routings) are absent from this registry - they're always-on plumbing,
not sellable features."""

# key -> (label, tier). "predictive" gates two routers (predictive.router AND
# cbm.router) since both back the single Predictive Maintenance page - see
# backend/app/main.py's include_router calls for both.
FEATURES: dict[str, tuple[str, str]] = {
    "boms": ("Bills of Materials", "core"),
    "procurement": ("Procurement", "core"),
    "sales": ("Sales Orders", "core"),
    "production": ("Production", "core"),
    "lots": ("Lots", "core"),
    "warehouse": ("Warehouse", "core"),
    "planning": ("Planning", "core"),
    "plant_summary": ("Plant Summary", "core"),
    "oee": ("OEE", "core"),
    "pace_tracking": ("Pace Tracking", "core"),
    "downtime": ("Downtime", "core"),
    "downtime_tracking": ("Downtime Tracking", "core"),
    "throughput": ("Throughput", "core"),
    "scrap": ("Scrap & Quality", "core"),
    "shifts": ("Shifts", "core"),
    "spc": ("SPC", "core"),
    "kpis": ("KPIs", "core"),
    "kpi_dashboards": ("KPI Dashboards", "core"),
    "reports": ("Report Builder", "core"),
    "data_chat": ("Ask (AI data chat)", "core"),
    "automl": ("AutoML", "premium"),
    "predictive": ("Predictive Maintenance", "premium"),
    "simulation": ("Simulation", "premium"),
    "rca_cases": ("Root Cause Analysis", "premium"),
    "mfg_forecast": ("Manufacturing Forecast", "premium"),
    "computer_vision": ("Computer Vision", "premium"),
}

CORE_FEATURES: list[str] = [k for k, (_, tier) in FEATURES.items() if tier == "core"]
PREMIUM_FEATURES: list[str] = list(FEATURES.keys())  # Core + Premium = everything
