import {
  Activity,
  AlertTriangle,
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
  History,
  LayoutDashboard,
  LayoutGrid,
  Layers,
  PackageX,
  Plug,
  Radio,
  Rows3,
  Search,
  ShoppingCart,
  Sparkles,
  Target,
  Terminal,
  Timer,
  TimerOff,
  TrendingUp,
  Users,
  Warehouse,
  Workflow,
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
  /** Gates this item to is_staff users, independent of featureKey. */
  requiresStaff?: boolean;
}

export interface NavSection {
  label: string;
  items: NavItem[];
}

/** Single source of truth for the sidebar, command palette, and topbar breadcrumb. */
export const NAV_SECTIONS: NavSection[] = [
  {
    label: "Overview",
    items: [{ to: "/", label: "Dashboard", icon: LayoutDashboard, end: true }],
  },
  {
    label: "Operations",
    items: [
      { to: "/boms", label: "Bills of Materials", icon: Layers, keywords: ["bom", "recipe", "components"], featureKey: "boms" },
      { to: "/procurement", label: "Procurement", icon: ShoppingCart, keywords: ["suppliers", "purchase orders", "po", "vendors"], featureKey: "procurement" },
      { to: "/lots", label: "Lots", icon: Boxes, keywords: ["batch", "traceability"], featureKey: "lots" },
      { to: "/warehouse", label: "Warehouse", icon: Warehouse, keywords: ["inventory", "stock", "on-hand", "on hand"], featureKey: "warehouse" },
      { to: "/planning", label: "Planning", icon: CalendarRange, keywords: ["mrp", "forecast", "demand"], featureKey: "planning" },
    ],
  },
  {
    label: "Data & Reports",
    items: [
      { to: "/datasets", label: "Datasets", icon: Database, keywords: ["upload", "csv", "data"] },
      { to: "/sql-editor", label: "SQL Editor", icon: Terminal, keywords: ["query", "sql"] },
      { to: "/bi", label: "BI Dashboards", icon: LayoutGrid, keywords: ["charts", "business intelligence"] },
      { to: "/report-builder", label: "Report Builder", icon: FileStack, keywords: ["reports", "export", "pdf"], featureKey: "reports" },
      { to: "/ask", label: "Ask", icon: Sparkles, keywords: ["chat", "natural language", "q&a", "assistant"], featureKey: "data_chat" },
    ],
  },
  {
    label: "Plant Analytics",
    items: [
      { to: "/analytics/plant-summary", label: "Plant Summary", icon: Factory, keywords: ["overview", "rollup"], featureKey: "plant_summary" },
      { to: "/analytics/oee", label: "OEE", icon: Gauge, keywords: ["overall equipment effectiveness", "availability", "performance"], featureKey: "oee" },
      { to: "/analytics/pace-tracking", label: "Pace Tracking", icon: Timer, keywords: ["manual entry", "tap", "operator"], featureKey: "pace_tracking" },
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff, keywords: ["stoppage", "downtime analysis"], featureKey: "downtime" },
      { to: "/analytics/downtime-tracking", label: "Downtime Tracking", icon: AlertTriangle, keywords: ["live downtime", "andon"], featureKey: "downtime_tracking" },
      { to: "/analytics/throughput", label: "Throughput", icon: BarChart3, keywords: ["units per hour", "rate", "production rate"], featureKey: "throughput" },
      { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX, keywords: ["defect", "reject", "quality", "fpq", "first pass"], featureKey: "scrap" },
      { to: "/analytics/shifts", label: "Shifts", icon: Users, keywords: ["shift schedule"], featureKey: "shifts" },
      { to: "/analytics/spc", label: "SPC", icon: Activity, keywords: ["statistical process control", "control chart"], featureKey: "spc" },
      { to: "/kpis", label: "KPIs", icon: Target, keywords: ["metrics", "targets"], featureKey: "kpis" },
      { to: "/kpis/dashboards", label: "KPI Dashboards", icon: Rows3, keywords: ["kpi wall", "tv mode"], featureKey: "kpi_dashboards" },
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
    items: [
      { to: "/integrations", label: "Integrations", icon: Plug, keywords: ["erp", "api", "webhook"] },
      { to: "/realtime", label: "Realtime", icon: Radio, keywords: ["live", "streaming"] },
      { to: "/workflows", label: "Workflows", icon: Workflow, keywords: ["automation", "triggers"] },
      { to: "/runs", label: "Run History", icon: History, keywords: ["logs", "execution history"] },
      { to: "/alerts", label: "Alerts", icon: Bell, keywords: ["notifications"] },
    ],
  },
  {
    label: "Staff",
    items: [
      { to: "/admin/customers", label: "Customers", icon: Users, keywords: ["onboard", "admin", "plan", "features"], requiresStaff: true },
    ],
  },
];

export const ALL_NAV_ITEMS = NAV_SECTIONS.flatMap((s) =>
  s.items.map((item) => ({ ...item, section: s.label })),
);

/** Single filter every nav surface (sidebar, mobile topbar, command palette)
 * runs through, so entitlement/staff gating can't drift between them. */
export function isNavItemVisible(
  item: Pick<NavItem, "featureKey" | "requiresStaff">,
  { enabledFeatures, isStaff }: { enabledFeatures: Set<string>; isStaff: boolean },
): boolean {
  if (item.requiresStaff && !isStaff) return false;
  if (item.featureKey && !enabledFeatures.has(item.featureKey)) return false;
  return true;
}

/** Best (longest-prefix) nav match for a pathname, for the topbar breadcrumb. */
export function findNavMatch(pathname: string) {
  let best: (NavItem & { section: string }) | undefined;
  for (const item of ALL_NAV_ITEMS) {
    const matches =
      item.to === "/" ? pathname === "/" : pathname === item.to || pathname.startsWith(item.to + "/");
    if (matches && (!best || item.to.length > best.to.length)) best = item;
  }
  return best;
}
