import {
  BarChart3,
  Bell,
  Boxes,
  Brain,
  CalendarRange,
  Camera,
  Database,
  Factory,
  FileStack,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LayoutGrid,
  Layers,
  PackageX,
  Plug,
  Radio,
  Receipt,
  Rows3,
  Search,
  ShoppingCart,
  Sparkles,
  Target,
  TimerOff,
  TrendingUp,
  Users,
  Warehouse,
  Wrench,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  /** Synonyms/intent terms so the command palette matches more than the literal label (e.g. "inventory" -> Warehouse, "defect" -> Scrap & Quality). */
  keywords?: string[];
  /** Gates this item to orgs with this key in their enabled_features - see frontend/src/lib/features.ts. Absent = always visible (System pages, Datasets, SQL Editor, BI Dashboards). */
  featureKey?: string;
  /** One-line blurb shown on the Explore category hub cards (see pages/CategoryHub.tsx). Absent for items not surfaced there. */
  description?: string;
}

export interface NavSection {
  label: string;
  items: NavItem[];
  /** Explore hub route for this section (see pages/CategoryHub.tsx). Absent = no hub, so the topbar breadcrumb renders the section as plain text. */
  hubTo?: string;
}

/** Single source of truth for the sidebar, command palette, and topbar breadcrumb. */
export const NAV_SECTIONS: NavSection[] = [
  {
    label: "Overview",
    items: [{ to: "/", label: "Dashboard", icon: LayoutDashboard, end: true }],
  },
  {
    label: "Operations",
    hubTo: "/explore/operations",
    items: [
      { to: "/boms", label: "Bills of Materials", icon: Layers, keywords: ["bom", "recipe", "components"], featureKey: "boms", description: "Component structures and versions" },
      { to: "/procurement", label: "Procurement", icon: ShoppingCart, keywords: ["suppliers", "purchase orders", "po", "vendors"], featureKey: "procurement", description: "Suppliers and purchase orders" },
      { to: "/sales", label: "Sales", icon: Receipt, keywords: ["orders", "customers", "sales orders", "so"], featureKey: "sales", description: "Customer orders and fulfillment" },
      { to: "/lots", label: "Lots", icon: Boxes, keywords: ["batch", "traceability"], featureKey: "lots", description: "Lot-tracked stock and holds" },
      { to: "/warehouse", label: "Warehouse", icon: Warehouse, keywords: ["inventory", "stock", "on-hand", "on hand"], featureKey: "warehouse", description: "On-hand inventory and storage locations" },
      { to: "/planning", label: "Planning", icon: CalendarRange, keywords: ["mrp", "forecast", "demand", "what to do", "shortages", "materials"], featureKey: "planning", description: "What to make and what to order" },
      { to: "/production", label: "Production", icon: Factory, keywords: ["work orders", "jobs", "make", "build", "wo", "work centers", "workcenter", "machines", "equipment", "routings", "routing", "cells", "stations"], description: "Work orders, work centers, machines, and routings" },
    ],
  },
  {
    label: "Data & Reports",
    hubTo: "/explore/data-reports",
    items: [
      { to: "/datasets", label: "Datasets", icon: Database, keywords: ["upload", "csv", "data", "query", "sql"], description: "Upload, clean, and query production data" },
      { to: "/bi", label: "BI Dashboards", icon: LayoutGrid, keywords: ["charts", "business intelligence"], description: "Build charts and save them to dashboards" },
      { to: "/report-builder", label: "Report Builder", icon: FileStack, keywords: ["reports", "export", "pdf"], featureKey: "reports", description: "Generate and export formatted reports" },
      { to: "/ask", label: "Ask", icon: Sparkles, keywords: ["chat", "natural language", "q&a", "assistant"], featureKey: "data_chat", description: "Natural-language Q&A over your data" },
    ],
  },
  {
    label: "Plant Analytics",
    hubTo: "/explore/analytics",
    items: [
      { to: "/analytics/plant-summary", label: "Plant Summary", icon: Factory, keywords: ["overview", "rollup"], featureKey: "plant_summary", description: "Rolled-up view across every line" },
      { to: "/analytics/oee", label: "OEE", icon: Gauge, keywords: ["overall equipment effectiveness", "availability", "performance"], featureKey: "oee", description: "Availability, performance, and quality" },
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff, keywords: ["stoppage", "downtime analysis", "live downtime", "andon", "tracking"], featureKey: "downtime", description: "Live tracking and Pareto analysis of downtime" },
      { to: "/analytics/throughput", label: "Throughput", icon: BarChart3, keywords: ["units per hour", "rate", "production rate"], featureKey: "throughput", description: "Units produced per hour" },
      { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX, keywords: ["defect", "reject", "quality", "fpq", "first pass"], featureKey: "scrap", description: "Defects, rejects, and first-pass yield" },
      { to: "/analytics/shifts", label: "Shifts", icon: Users, keywords: ["shift schedule"], featureKey: "shifts", description: "Shift schedules and coverage" },
      { to: "/kpis", label: "KPIs", icon: Target, keywords: ["metrics", "targets"], featureKey: "kpis", description: "Targets and metrics by team" },
      { to: "/kpis/dashboards", label: "KPI Dashboards", icon: Rows3, keywords: ["kpi wall", "tv mode"], featureKey: "kpi_dashboards", description: "TV-mode KPI walls" },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { to: "/automl", label: "AutoML", icon: Brain, keywords: ["machine learning", "model training"], featureKey: "automl" },
      { to: "/simulation", label: "Simulation", icon: FlaskConical, keywords: ["what-if", "scenario"], featureKey: "simulation" },
      { to: "/rca-cases", label: "Root Cause Analysis", icon: Search, keywords: ["rca", "5 why", "fishbone"], featureKey: "rca_cases" },
      { to: "/predictive", label: "Predictive Maintenance", icon: Wrench, keywords: ["failure prediction", "maintenance"], featureKey: "predictive" },
      { to: "/mfg-forecast", label: "Manufacturing Forecast", icon: TrendingUp, keywords: ["production forecast"], featureKey: "mfg_forecast" },
      { to: "/computer-vision", label: "Computer Vision", icon: Camera, keywords: ["image", "defect detection", "camera"], featureKey: "computer_vision" },
    ],
  },
  {
    label: "System",
    hubTo: "/explore/system",
    items: [
      { to: "/integrations", label: "Integrations", icon: Plug, keywords: ["erp", "api", "webhook"], description: "Connect ERPs, APIs, and webhooks" },
      { to: "/realtime", label: "Realtime", icon: Radio, keywords: ["live", "streaming"], description: "Live streaming data sources" },
      { to: "/alerts", label: "Alerts", icon: Bell, keywords: ["notifications"], description: "Threshold rules and scan results" },
    ],
  },
];

export const ALL_NAV_ITEMS = NAV_SECTIONS.flatMap((s) =>
  s.items.map((item) => ({ ...item, section: s.label, sectionHubTo: s.hubTo })),
);

/** Single filter every nav surface (sidebar, mobile topbar, command palette)
 * runs through, so entitlement/staff gating can't drift between them. */
export function isNavItemVisible(
  item: Pick<NavItem, "featureKey">,
  { enabledFeatures }: { enabledFeatures: Set<string> },
): boolean {
  if (item.featureKey && !enabledFeatures.has(item.featureKey)) return false;
  return true;
}

/** Best (longest-prefix) nav match for a pathname, for the topbar breadcrumb. */
export function findNavMatch(pathname: string) {
  let best: (typeof ALL_NAV_ITEMS)[number] | undefined;
  for (const item of ALL_NAV_ITEMS) {
    const matches =
      item.to === "/" ? pathname === "/" : pathname === item.to || pathname.startsWith(item.to + "/");
    if (matches && (!best || item.to.length > best.to.length)) best = item;
  }
  return best;
}
