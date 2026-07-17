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
  FileText,
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
  SplitSquareHorizontal,
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
      { to: "/boms", label: "Bills of Materials", icon: Layers },
      { to: "/procurement", label: "Procurement", icon: ShoppingCart },
      { to: "/lots", label: "Lots", icon: Boxes },
      { to: "/warehouse", label: "Warehouse", icon: Warehouse },
      { to: "/planning", label: "Planning", icon: CalendarRange },
    ],
  },
  {
    label: "Data & Reports",
    items: [
      { to: "/datasets", label: "Datasets", icon: Database },
      { to: "/sql-editor", label: "SQL Editor", icon: Terminal },
      { to: "/bi", label: "BI Dashboards", icon: LayoutGrid },
      { to: "/report-builder", label: "Report Builder", icon: FileStack },
      { to: "/reports", label: "Reports", icon: FileText },
      { to: "/ask", label: "Ask", icon: Sparkles },
    ],
  },
  {
    label: "Plant Analytics",
    items: [
      { to: "/analytics/plant-summary", label: "Plant Summary", icon: Factory },
      { to: "/analytics/oee", label: "OEE", icon: Gauge },
      { to: "/analytics/pace-tracking", label: "Pace Tracking", icon: Timer },
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff },
      { to: "/analytics/downtime-tracking", label: "Downtime Tracking", icon: AlertTriangle },
      { to: "/analytics/throughput", label: "Throughput", icon: BarChart3 },
      { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX },
      { to: "/analytics/shifts", label: "Shifts", icon: Users },
      { to: "/analytics/spc", label: "SPC", icon: Activity },
      { to: "/kpis", label: "KPIs", icon: Target },
      { to: "/kpis/dashboards", label: "KPI Dashboards", icon: Rows3 },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { to: "/automl", label: "AutoML", icon: Brain },
      { to: "/simulation", label: "Simulation", icon: FlaskConical },
      { to: "/rca-cases", label: "Root Cause Analysis", icon: Search },
      { to: "/predictive", label: "Predictive Maintenance", icon: Wrench },
      { to: "/mfg-forecast", label: "Manufacturing Forecast", icon: TrendingUp },
      { to: "/scenarios", label: "Scenarios", icon: SplitSquareHorizontal },
      { to: "/computer-vision", label: "Computer Vision", icon: Camera },
    ],
  },
  {
    label: "System",
    items: [
      { to: "/integrations", label: "Integrations", icon: Plug },
      { to: "/realtime", label: "Realtime", icon: Radio },
      { to: "/workflows", label: "Workflows", icon: Workflow },
      { to: "/runs", label: "Run History", icon: History },
      { to: "/alerts", label: "Alerts", icon: Bell },
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
