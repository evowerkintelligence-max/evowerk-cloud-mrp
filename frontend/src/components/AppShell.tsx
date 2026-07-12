import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  Bell,
  Boxes,
  Brain,
  CalendarRange,
  Database,
  FileStack,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LayoutGrid,
  Layers,
  LogOut,
  PackageX,
  Plug,
  Radio,
  Camera,
  Search,
  SplitSquareHorizontal,
  TrendingUp,
  Wrench,
  ShoppingCart,
  Target,
  Terminal,
  TimerOff,
  Users,
  Warehouse,
  Workflow,
} from "lucide-react";
import { apiFetch } from "@/api/client";
import { useAuthStore } from "@/store/authStore";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/BrandMark";
import { cn } from "@/lib/utils";

interface OrgResponse {
  id: string;
  name: string;
}

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/boms", label: "Bills of Materials", icon: Layers, end: false },
  { to: "/procurement", label: "Procurement", icon: ShoppingCart, end: false },
  { to: "/lots", label: "Lots", icon: Boxes, end: false },
  { to: "/warehouse", label: "Warehouse", icon: Warehouse, end: false },
  { to: "/planning", label: "Planning", icon: CalendarRange, end: false },
];

const ANALYTICS_NAV = [
  { to: "/datasets", label: "Datasets", icon: Database, end: false },
  { to: "/sql-editor", label: "SQL Editor", icon: Terminal, end: false },
  { to: "/analytics/oee", label: "OEE", icon: Gauge, end: false },
  { to: "/analytics/downtime", label: "Downtime", icon: TimerOff, end: false },
  { to: "/analytics/throughput", label: "Throughput", icon: BarChart3, end: false },
  { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX, end: false },
  { to: "/analytics/shifts", label: "Shifts", icon: Users, end: false },
  { to: "/analytics/spc", label: "SPC", icon: Activity, end: false },
  { to: "/kpis", label: "KPIs", icon: Target, end: false },
  { to: "/integrations", label: "Integrations", icon: Plug, end: false },
  { to: "/realtime", label: "Realtime", icon: Radio, end: false },
  { to: "/bi", label: "BI Dashboards", icon: LayoutGrid, end: false },
  { to: "/workflows", label: "Workflows", icon: Workflow, end: false },
  { to: "/report-builder", label: "Report Builder", icon: FileStack, end: false },
  { to: "/automl", label: "AutoML", icon: Brain, end: false },
  { to: "/simulation", label: "Simulation", icon: FlaskConical, end: false },
  { to: "/rca-cases", label: "Root Cause Analysis", icon: Search, end: false },
  { to: "/predictive", label: "Predictive Maintenance", icon: Wrench, end: false },
  { to: "/mfg-forecast", label: "Manufacturing Forecast", icon: TrendingUp, end: false },
  { to: "/scenarios", label: "Scenarios", icon: SplitSquareHorizontal, end: false },
  { to: "/computer-vision", label: "Computer Vision", icon: Camera, end: false },
  { to: "/alerts", label: "Alerts", icon: Bell, end: false },
];

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark className="size-9 shrink-0 text-foreground" title="Evowerk" />
      <div className="leading-tight">
        <div className="text-base font-bold uppercase tracking-[0.16em] text-foreground">Evowerk</div>
        <div className="mt-0.5 text-[10px] font-medium uppercase tracking-[0.2em] text-muted-foreground">
          Cloud MRP
        </div>
      </div>
    </div>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  const clear = useAuthStore((s) => s.clear);
  const refreshToken = useAuthStore((s) => s.refreshToken);

  const org = useQuery<OrgResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load org");
      return res.json();
    },
  });

  async function logout() {
    if (refreshToken) {
      await apiFetch("/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken }),
      }).catch(() => undefined);
    }
    clear();
    navigate("/login");
  }

  return (
    <div className="min-h-screen">
      <div className="app-backdrop" />
      <div className="mx-auto flex min-h-screen max-w-[1600px]">
        <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-border bg-card/40 backdrop-blur-sm md:flex">
          <div className="flex h-16 items-center px-5">
            <Brand />
          </div>
          <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-3 py-4">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-primary/10 text-primary shadow-glow-sm"
                      : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                  )
                }
              >
                <item.icon className="size-4 shrink-0" />
                {item.label}
              </NavLink>
            ))}
            <div className="mt-4 px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70">
              Analytics
            </div>
            {ANALYTICS_NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-primary/10 text-primary shadow-glow-sm"
                      : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                  )
                }
              >
                <item.icon className="size-4 shrink-0" />
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="px-5 py-4 text-xs text-muted-foreground">v0.0.1</div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-10 flex h-16 items-center justify-between gap-4 border-b border-border bg-background/70 px-5 backdrop-blur-md">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <span className="hidden sm:inline">Organization</span>
              <span className="font-medium text-foreground">{org.data?.name ?? "—"}</span>
            </div>
            <Button variant="ghost" size="sm" onClick={logout}>
              <LogOut /> Log out
            </Button>
          </header>

          <div className="flex gap-1 overflow-x-auto border-b border-border bg-card/30 px-3 py-2 md:hidden">
            {[...NAV, ...ANALYTICS_NAV].map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium",
                    isActive ? "bg-primary/10 text-primary" : "text-muted-foreground",
                  )
                }
              >
                <item.icon className="size-3.5" /> {item.label}
              </NavLink>
            ))}
          </div>

          <main className="mx-auto w-full max-w-6xl flex-1 animate-fade-in px-5 py-8">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
