# Layouts

Shared layout components that wrap pages. `AppShell` is the authenticated app frame
(sidebar + topbar); `AuthLayout` wraps the login/register/MFA screens; `PageHeader` and
`PageTabs` are per-page layout building blocks used inside both. The nav data model
(`frontend/src/lib/nav.ts`) is the single source of truth consumed by `AppShell`'s
sidebar, its mobile tab strip, its topbar breadcrumb, and `CommandPalette`.

---

## AppShell

- File: `frontend/src/components/AppShell.tsx`
- Renders: sticky left sidebar (desktop, `md:`+) with brand, collapsible nav sections, workspace/org footer with logout; sticky topbar with breadcrumb + search-palette trigger (Ctrl/Cmd-K) + alerts bell; horizontal scrollable tab strip nav for mobile (`md:hidden`); `<Outlet />` for the routed page content, capped at `max-w-6xl`.
- Used by: the authenticated route subtree in `App.tsx` (wraps every route except `/login`, `/register`, `/mfa`).
- Depends on: `@/api/client` (`apiFetch`), `@/store/authStore` (`useAuthStore`), `@/components/ui/button` (`Button`), `@/components/BrandMark`, `@/components/CommandPalette`, `@/lib/nav` (`ALL_NAV_ITEMS`, `NAV_SECTIONS`, `findNavMatch`, `NavItem`), `@/lib/utils` (`cn`).

```tsx
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bell, ChevronDown, ChevronRight, LogOut, Search } from "lucide-react";
import { apiFetch } from "@/api/client";
import { useAuthStore } from "@/store/authStore";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/BrandMark";
import { CommandPalette } from "@/components/CommandPalette";
import { ALL_NAV_ITEMS, NAV_SECTIONS, findNavMatch, type NavItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

interface OrgResponse {
  id: string;
  name: string;
}

const COLLAPSE_KEY = "evowerk.nav.collapsed";

function loadCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark className="size-8 shrink-0 text-foreground" title="Evowerk" />
      <div className="leading-tight">
        <div className="text-[15px] font-bold uppercase tracking-[0.16em] text-foreground">
          Evowerk
        </div>
        <div className="mt-0.5 text-[9px] font-medium uppercase tracking-[0.24em] text-muted-foreground">
          Cloud MRP
        </div>
      </div>
    </div>
  );
}

function SideNavLink({ item }: { item: NavItem }) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      className={({ isActive }) =>
        cn(
          "group relative flex items-center gap-2.5 rounded-md px-3 py-[7px] text-[13px] font-medium transition-colors duration-150",
          isActive
            ? "bg-primary/10 text-primary before:absolute before:left-0 before:top-1/2 before:h-4 before:w-0.5 before:-translate-y-1/2 before:rounded-full before:bg-primary"
            : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground",
        )
      }
    >
      <item.icon className="size-4 shrink-0 opacity-80" />
      <span className="truncate">{item.label}</span>
    </NavLink>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const clear = useAuthStore((s) => s.clear);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadCollapsed);

  const org = useQuery<OrgResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load org");
      return res.json();
    },
  });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function toggleSection(label: string) {
    setCollapsed((prev) => {
      const next = { ...prev, [label]: !prev[label] };
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify(next));
      return next;
    });
  }

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

  const match = findNavMatch(location.pathname);
  const orgInitials = (org.data?.name ?? "??")
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div className="min-h-screen">
      <div className="app-backdrop" />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <div className="mx-auto flex min-h-screen max-w-[1680px]">
        <aside className="sticky top-0 hidden h-screen w-[248px] shrink-0 flex-col border-r border-border/70 bg-card/40 md:flex">
          <div className="flex h-14 shrink-0 items-center border-b border-border/60 px-5">
            <Brand />
          </div>
          <nav className="flex flex-1 flex-col overflow-y-auto px-3 py-3">
            {NAV_SECTIONS.map((section, idx) => {
              const isCollapsed = collapsed[section.label];
              // The single-item Overview section renders without a group header.
              if (section.items.length === 1 && idx === 0) {
                return (
                  <div key={section.label} className="flex flex-col gap-0.5">
                    {section.items.map((item) => (
                      <SideNavLink key={item.to} item={item} />
                    ))}
                  </div>
                );
              }
              return (
                <div key={section.label} className="mt-5 first:mt-0">
                  <button
                    type="button"
                    onClick={() => toggleSection(section.label)}
                    className="flex w-full items-center justify-between rounded px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70 transition-colors hover:text-muted-foreground"
                  >
                    {section.label}
                    {isCollapsed ? (
                      <ChevronRight className="size-3" />
                    ) : (
                      <ChevronDown className="size-3" />
                    )}
                  </button>
                  {!isCollapsed && (
                    <div className="flex flex-col gap-0.5">
                      {section.items.map((item) => (
                        <SideNavLink key={item.to} item={item} />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </nav>
          <div className="flex shrink-0 items-center gap-3 border-t border-border/60 px-4 py-3">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/15 text-[11px] font-bold text-primary ring-1 ring-inset ring-primary/25">
              {orgInitials}
            </div>
            <div className="min-w-0 flex-1 leading-tight">
              <div className="truncate text-[13px] font-medium text-foreground">
                {org.data?.name ?? "—"}
              </div>
              <div className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                Workspace
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              onClick={logout}
              aria-label="Log out"
              title="Log out"
            >
              <LogOut />
            </Button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b border-border/70 bg-background/70 px-5 backdrop-blur-md">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              {match ? (
                <>
                  <span className="hidden text-muted-foreground sm:inline">{match.section}</span>
                  <span className="hidden text-muted-foreground/50 sm:inline">/</span>
                  <span className="truncate font-medium text-foreground">{match.label}</span>
                </>
              ) : (
                <span className="truncate font-medium text-foreground">
                  {org.data?.name ?? ""}
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={() => setPaletteOpen(true)}
                className="hidden h-9 w-60 items-center gap-2 rounded-md border border-input bg-secondary/40 px-3 text-sm text-muted-foreground shadow-card transition-colors hover:border-muted-foreground/40 hover:text-foreground sm:flex"
              >
                <Search className="size-3.5" />
                <span className="flex-1 text-left text-[13px]">Search…</span>
                <kbd className="rounded border border-border bg-background/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                  Ctrl K
                </kbd>
              </button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => navigate("/alerts")}
                aria-label="Alerts"
                title="Alerts"
              >
                <Bell />
              </Button>
            </div>
          </header>

          <div className="flex gap-1 overflow-x-auto border-b border-border bg-card/30 px-3 py-2 md:hidden">
            {ALL_NAV_ITEMS.map((item) => (
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

          <main className="mx-auto w-full max-w-6xl flex-1 animate-fade-in px-6 py-8">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
```

---

## AuthLayout

- File: `frontend/src/components/AuthLayout.tsx`
- Renders: two-pane auth screen. Left pane (`lg:` and up only) is a dark pitch panel with brand, headline, 3 feature bullets (icon + title + text), and a copyright footer. Right pane centers a form card (`glow-card` styled) with title/subtitle/children/footer slots. Mobile shows a compact brand lockup above the form instead of the pitch panel.
- Used by: `Login.tsx`, `Register.tsx`, `MfaChallenge.tsx` (the three routes outside `AppShell`).
- Depends on: `@/components/BrandMark`, `lucide-react` icons (`Boxes`, `Gauge`, `TrendingUp`).
- Key props: `title: string`, `subtitle?: string`, `children: ReactNode`, `footer?: ReactNode`.

```tsx
import type { ReactNode } from "react";
import { Boxes, Gauge, TrendingUp } from "lucide-react";
import { BrandMark } from "@/components/BrandMark";

const PITCH = [
  {
    icon: Boxes,
    title: "Lot-level traceability",
    text: "Every unit of stock tracked from receipt to consumption.",
  },
  {
    icon: Gauge,
    title: "Plant analytics built in",
    text: "OEE, downtime, scrap, and SPC straight from production data.",
  },
  {
    icon: TrendingUp,
    title: "Forecast-driven planning",
    text: "MRP runs that turn demand signals into purchase orders.",
  },
];

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen">
      <div className="app-backdrop" />

      {/* Brand / pitch panel */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden border-r border-border/70 bg-card/30 p-12 lg:flex">
        <div className="flex items-center gap-3">
          <BrandMark className="size-9 text-foreground" title="Evowerk" />
          <div className="leading-tight">
            <div className="text-lg font-bold uppercase tracking-[0.16em] text-foreground">
              Evowerk
            </div>
            <div className="text-[10px] font-medium uppercase tracking-[0.24em] text-muted-foreground">
              Cloud MRP
            </div>
          </div>
        </div>

        <div className="max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight text-foreground">
            The operating layer for modern manufacturing.
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            BOMs, procurement, lot tracking, and plant analytics in one multi-tenant workspace.
          </p>
          <div className="mt-10 space-y-6">
            {PITCH.map((p) => (
              <div key={p.title} className="flex items-start gap-4">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary ring-1 ring-inset ring-primary/20">
                  <p.icon className="size-4" />
                </div>
                <div>
                  <div className="text-sm font-medium text-foreground">{p.title}</div>
                  <div className="mt-0.5 text-sm text-muted-foreground">{p.text}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="text-xs text-muted-foreground">
          © {new Date().getFullYear()} Evowerk · Multi-tenant · Audit-logged · MFA-ready
        </div>
      </div>

      {/* Form panel */}
      <div className="flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm animate-fade-in">
          <div className="mb-8 flex flex-col items-start">
            <div className="mb-8 flex items-center gap-2.5 lg:hidden">
              <BrandMark className="size-9 text-foreground" title="Evowerk" />
              <span className="text-xl font-bold uppercase tracking-[0.14em] text-foreground">
                Evowerk
              </span>
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            {subtitle && <p className="mt-1.5 text-sm text-muted-foreground">{subtitle}</p>}
          </div>
          <div className="glow-card rounded-xl p-6 sm:p-8">{children}</div>
          {footer && <div className="mt-6 text-center text-sm text-muted-foreground">{footer}</div>}
        </div>
      </div>
    </div>
  );
}
```

---

## PageHeader

- File: `frontend/src/components/PageHeader.tsx`
- Renders: page title block used at the top of nearly every routed page inside `AppShell`'s `<main>` — optional eyebrow label, `<h1>` title, optional description, optional right-aligned actions row. Bottom border separates it from page body.
- Key props: `title: string`, `description?: ReactNode`, `actions?: ReactNode`, `eyebrow?: string`.

```tsx
import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: string;
}) {
  return (
    <div className="mb-8 flex flex-col gap-4 border-b border-border/70 pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="space-y-1.5">
        {eyebrow && (
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary/80">
            {eyebrow}
          </div>
        )}
        <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px] sm:leading-9">
          {title}
        </h1>
        {description && (
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
```

---

## PageTabs

- File: `frontend/src/components/PageTabs.tsx`
- Renders: pill-style segmented-control tab switcher (used e.g. in `Warehouse.tsx` to switch between Inventory/Locations panels, and in analytics pages for sub-views). Generic over the tab value's string-literal union.
- Key props: `tabs: { value: T; label: string }[]`, `active: T`, `onChange: (value: T) => void`.

```tsx
import { cn } from "@/lib/utils";

export function PageTabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { value: T; label: string }[];
  active: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-muted/60 p-1 shadow-card">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          onClick={() => onChange(tab.value)}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-150",
            active === tab.value
              ? "bg-card text-foreground shadow-[0_1px_2px_0_hsl(224_42%_2%/0.5),inset_0_0_0_1px_hsl(var(--border))]"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
```

---

## Nav data model

- File: `frontend/src/lib/nav.ts` *(untracked — new)*
- Purpose: single source of truth for the sidebar sections, the mobile tab strip, the topbar breadcrumb match, and the `CommandPalette` search index. 6 sections: Overview, Operations, Data & Reports, Plant Analytics, Intelligence, System — 30 nav items total, each `{ to, label, icon: LucideIcon, end? }`.

```tsx
import {
  Activity,
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
  Search,
  ShoppingCart,
  Sparkles,
  SplitSquareHorizontal,
  Target,
  Terminal,
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
      { to: "/analytics/downtime", label: "Downtime", icon: TimerOff },
      { to: "/analytics/throughput", label: "Throughput", icon: BarChart3 },
      { to: "/analytics/scrap", label: "Scrap & Quality", icon: PackageX },
      { to: "/analytics/shifts", label: "Shifts", icon: Users },
      { to: "/analytics/spc", label: "SPC", icon: Activity },
      { to: "/kpis", label: "KPIs", icon: Target },
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
```
