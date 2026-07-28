import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bell, ChevronDown, ChevronRight, LogOut, PanelLeftClose, PanelLeftOpen, Search } from "lucide-react";
import { apiFetch } from "@/api/client";
import { useAuthStore } from "@/store/authStore";
import { useCurrentUser } from "@/lib/currentUser";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/BrandMark";
import { CommandPalette } from "@/components/CommandPalette";
import { ALL_NAV_ITEMS, NAV_SECTIONS, findNavMatch, isNavItemVisible, type NavItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

const COLLAPSE_KEY = "evowerk.nav.collapsed";
const SIDEBAR_OPEN_KEY = "evowerk.nav.sidebarOpen";

function loadCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function loadSidebarOpen(): boolean {
  return localStorage.getItem(SIDEBAR_OPEN_KEY) !== "false";
}

/** Breadcrumb connector styled like a flowchart edge (line + arrowhead) instead of a plain chevron or slash. */
function FlowConnector() {
  return (
    <svg
      width="20"
      height="12"
      viewBox="0 0 20 12"
      fill="none"
      aria-hidden
      className="hidden shrink-0 text-muted-foreground/50 sm:inline"
    >
      <line x1="0" y1="6" x2="14" y2="6" stroke="currentColor" strokeWidth="1.5" />
      <path d="M11 2L15 6L11 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
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
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground",
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span
              aria-hidden
              className="brand-gradient-animate absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full"
            />
          )}
          <item.icon className="size-4 shrink-0 opacity-80" />
          <span className="truncate">{item.label}</span>
        </>
      )}
    </NavLink>
  );
}

interface AlertRuleSummary {
  id: string;
  name: string;
  severity: "critical" | "warning" | "info";
  enabled: boolean;
}

const SEV_BADGE = { critical: "danger", warning: "warning", info: "info" } as const;

function NotificationsMenu() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const rules = useQuery<AlertRuleSummary[]>({
    queryKey: ["alert-rules"],
    queryFn: async () => {
      const res = await apiFetch("/alerts/rules");
      if (!res.ok) throw new Error("Failed to load alerts");
      return res.json();
    },
    enabled: open,
  });

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const enabled = (rules.data ?? []).filter((r) => r.enabled);
  const criticalCount = enabled.filter((r) => r.severity === "critical").length;

  return (
    <div ref={rootRef} className="relative">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen((o) => !o)}
        aria-label="Notifications"
        title="Notifications"
      >
        <Bell />
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-2 w-80 animate-scale-in overflow-hidden rounded-xl border border-border bg-popover shadow-overlay">
          <div className="border-b border-border px-4 py-3">
            <p className="text-sm font-medium text-foreground">Alert rules</p>
            <p className="text-xs text-muted-foreground">
              {rules.isLoading
                ? "Loading…"
                : enabled.length === 0
                  ? "No enabled alert rules."
                  : `${enabled.length} enabled${criticalCount ? `, ${criticalCount} critical` : ""}`}
            </p>
          </div>
          <div className="max-h-64 overflow-y-auto p-2">
            {enabled.map((r) => (
              <div key={r.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm">
                <Badge variant={SEV_BADGE[r.severity]}>{r.severity}</Badge>
                <span className="truncate text-foreground">{r.name}</span>
              </div>
            ))}
          </div>
          <div className="border-t border-border p-2">
            <Button
              variant="secondary"
              className="w-full"
              onClick={() => {
                setOpen(false);
                navigate("/alerts");
              }}
            >
              View all alerts
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const clear = useAuthStore((s) => s.clear);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadCollapsed);
  const [sidebarOpen, setSidebarOpen] = useState(loadSidebarOpen);

  const org = useCurrentUser();

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

  function toggleSidebar() {
    setSidebarOpen((prev) => {
      const next = !prev;
      localStorage.setItem(SIDEBAR_OPEN_KEY, String(next));
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
  const enabledFeatures = new Set(org.data?.enabled_features ?? []);
  const navGate = { enabledFeatures };
  const visibleSections = NAV_SECTIONS
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => isNavItemVisible(item, navGate)),
    }))
    .filter((section) => section.items.length > 0);
  const visibleNavItems = ALL_NAV_ITEMS.filter((item) => isNavItemVisible(item, navGate));
  const orgInitials = (org.data?.name ?? "??")
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div className="min-h-screen">
      <div className="app-backdrop" />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} items={visibleNavItems} />
      <div className="mx-auto flex min-h-screen max-w-[1680px]">
        <aside
          className={cn(
            "sticky top-0 hidden h-screen w-[248px] shrink-0 flex-col border-r border-border/70 bg-card/40 md:flex",
            !sidebarOpen && "md:hidden",
          )}
        >
          <div className="flex h-14 shrink-0 items-center border-b border-border/60 px-5">
            <Brand />
          </div>
          <nav className="flex flex-1 flex-col overflow-y-auto px-3 py-3">
            {visibleSections.map((section, idx) => {
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
            <div className="brand-gradient-animate flex size-8 shrink-0 items-center justify-center rounded-md text-[11px] font-bold text-white shadow-glow-sm">
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
              <Button
                variant="ghost"
                size="icon"
                className="hidden shrink-0 md:inline-flex"
                onClick={toggleSidebar}
                aria-label={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
                title={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
              >
                {sidebarOpen ? <PanelLeftClose /> : <PanelLeftOpen />}
              </Button>
              {match ? (
                <>
                  <span className="hidden text-muted-foreground sm:inline">{match.section}</span>
                  <FlowConnector />
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
              <NotificationsMenu />
            </div>
          </header>

          <div className="flex gap-1 overflow-x-auto border-b border-border bg-card/30 px-3 py-2 md:hidden">
            {visibleNavItems.map((item) => (
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

          <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
            <div key={location.pathname} className="animate-fade-in">
              <Outlet />
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}
