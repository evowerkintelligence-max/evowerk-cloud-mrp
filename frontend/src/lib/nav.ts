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
      { to: "/boms", label: "Bills of Materials", icon: Layers, keywords: ["bom", "recipe", "components"] },
      { to: "/procurement", label: "Procurement", icon: ShoppingCart, keywords: ["suppliers", "purchase orders", "po", "vendors"] },
      { to: "/lots", label: "Lots", icon: Boxes, keywords: ["batch", "traceability"] },
      { to: "/warehouse", label: "Warehouse", icon: Warehouse, keywords: ["inventory", "stock", "on-hand", "on hand"] },
      { to: "/planning", label: "Planning", icon: CalendarRange, keywords: ["mrp", "forecast", "demand"] },
    ],
  },
  {
    label: "Data & Reports",
    items: [
      { to: "/datasets", label: "Datasets", icon: Database, keywords: ["upload", "csv", "data"] },
      { to: "/sql-editor", label: "SQL Editor", icon: Terminal, keywords: ["query", "sql"] },
      { to: "/bi", label: "BI Dashboards", icon: LayoutGrid, keywords: ["charts", "business intelligence"] },
      { to: "/report-builder", label: "Report Builder", icon: FileStack, keywords: ["reports", "export", "pdf"] },
      { to: "/ask", label: "Ask", icon: Sparkles, keywords: ["chat", "natural language", "q&a", "assistant"] },
    ],
  },
  {
    label: "Plant Analytics",
    items: [
      { to: "/analytics/plant-summary", label: "Plant Summary", icon: Factory, keywords: ["overview", "rollup"] },
      { to: "/analytics/oee", label: "OEE", icon: Gauge, keywords: ["overall equipment effectiveness", "availability", "performance"] },
      { to: "/analytics/pace-tracking", label: "Pace Tracking", icon: Timer, keywords: ["manual entry", "tap", "operator"] },
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff, keywords: ["stoppage", "downtime analysis"] },
      { to: "/analytics/downtime-tracking", label: "Downtime Tracking", icon: AlertTriangle, keywords: ["live downtime", "andon"] },
      { to: "/analytics/throughput", label: "Throughput", icon: BarChart3, keywords: ["units per hour", "rate", "production rate"] },
      { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX, keywords: ["defect", "reject", "quality", "fpq", "first pass"] },
      { to: "/analytics/shifts", label: "Shifts", icon: Users, keywords: ["shift schedule"] },
      { to: "/analytics/spc", label: "SPC", icon: Activity, keywords: ["statistical process control", "control chart"] },
      { to: "/kpis", label: "KPIs", icon: Target, keywords: ["metrics", "targets"] },
      { to: "/kpis/dashboards", label: "KPI Dashboards", icon: Rows3, keywords: ["kpi wall", "tv mode"] },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { to: "/automl", label: "AutoML", icon: Brain, keywords: ["machine learning", "model training"] },
      { to: "/simulation", label: "Simulation", icon: FlaskConical, keywords: ["what-if", "scenario"] },
      { to: "/rca-cases", label: "Root Cause Analysis", icon: Search, keywords: ["rca", "5 why", "fishbone"] },
      { to: "/predictive", label: "Predictive Maintenance", icon: Wrench, keywords: ["failure prediction", "maintenance"] },
      { to: "/mfg-forecast", label: "Manufacturing Forecast", icon: TrendingUp, keywords: ["production forecast"] },
      { to: "/computer-vision", label: "Computer Vision", icon: Camera, keywords: ["image", "defect detection", "camera"] },
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
];

export const ALL_NAV_ITEMS = NAV_SECTIONS.flatMap((s) =>
  s.items.map((item) => ({ ...item, section: s.label })),
);

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
