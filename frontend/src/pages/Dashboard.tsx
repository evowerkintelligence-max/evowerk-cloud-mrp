import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  BarChart3,
  Boxes,
  CalendarRange,
  Database,
  Layers,
  LayoutGrid,
  Package,
  RefreshCw,
  ShieldCheck,
  ShoppingCart,
  Sparkles,
  Sunrise,
  Truck,
  Warehouse,
} from "lucide-react";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { humanize, inventoryStatusVariant } from "@/lib/status";
import { cn } from "@/lib/utils";

interface OrgResponse {
  id: string;
  name: string;
}

interface BriefLine {
  item_id: string;
  item_sku: string;
  item_name: string;
  status: string;
  days_of_supply: number | null;
  days_of_supply_delta: number | null;
  is_new_this_week: boolean;
}

interface LotAlert {
  lot_id: string;
  item_sku: string;
  lot_number: string;
  location_code: string;
  days_until_expiry: number | null;
  reason: string; // "on_hold" | "expiring_soon"
}

interface MorningBriefResponse {
  lines: BriefLine[];
  lot_alerts: LotAlert[];
}

const STATUS_BORDER: Record<string, string> = {
  stockout: "border-l-rose-500",
  low: "border-l-amber-500",
  excess: "border-l-sky-500",
  slow_moving: "border-l-slate-500",
  dead_stock: "border-l-orange-500",
};

const REASON_BORDER: Record<string, string> = {
  on_hold: "border-l-amber-500",
  expiring_soon: "border-l-rose-500",
};

function timeAgo(timestampMs: number): string {
  const seconds = Math.max(0, Math.floor((Date.now() - timestampMs) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

function useCount(key: string, path: string) {
  return useQuery<unknown[]>({
    queryKey: [key],
    queryFn: async () => {
      const res = await apiFetch(path);
      if (!res.ok) throw new Error(`Failed to load ${key}`);
      return res.json();
    },
  });
}

const LINKS = [
  { to: "/boms", label: "Bills of Materials", description: "Component structures and versions", icon: Layers },
  { to: "/procurement", label: "Procurement", description: "Suppliers and purchase orders", icon: ShoppingCart },
  { to: "/lots", label: "Lots", description: "Lot-tracked stock and holds", icon: Boxes },
  { to: "/warehouse", label: "Warehouse", description: "On-hand inventory and storage locations", icon: Warehouse },
  { to: "/planning", label: "Planning", description: "What to make and what to order", icon: CalendarRange },
  { to: "/analytics", label: "Analytics", description: "OEE, downtime, throughput, scrap, shifts, SPC", icon: BarChart3 },
  { to: "/datasets", label: "Datasets", description: "Upload, clean, and query production data", icon: Database },
  { to: "/bi", label: "BI Dashboards", description: "Build charts and save them to dashboards", icon: LayoutGrid },
  { to: "/ask", label: "Ask", description: "Natural-language Q&A over your data", icon: Sparkles },
];

/** Cycled per explore-card index so the grid reads as multi-color, not monochrome. */
const EXPLORE_TONES = [
  {
    card: "hover:border-primary/40 hover:shadow-glow-sm",
    icon: "bg-primary/10 text-primary ring-primary/20",
    title: "group-hover:text-primary",
  },
  {
    card: "hover:border-accent/40 hover:shadow-[0_0_18px_-4px_hsl(var(--accent)/0.45)]",
    icon: "bg-accent/10 text-accent ring-accent/20",
    title: "group-hover:text-accent",
  },
  {
    card: "hover:border-success/40 hover:shadow-[0_0_18px_-4px_hsl(var(--success)/0.45)]",
    icon: "bg-success/10 text-success ring-success/20",
    title: "group-hover:text-success",
  },
] as const;

export function Dashboard() {
  const org = useQuery<OrgResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load org");
      return res.json();
    },
  });

  const items = useCount("items", "/items");
  const suppliers = useCount("suppliers", "/suppliers");
  const pos = useCount("purchase-orders", "/purchase-orders");
  const boms = useCount("boms", "/boms");

  const brief = useQuery<MorningBriefResponse>({
    queryKey: ["morning-brief"],
    queryFn: async () => {
      const res = await apiFetch("/inventory/morning-brief");
      if (!res.ok) throw new Error("Failed to load morning brief");
      return res.json();
    },
  });
  const briefEmpty = !brief.data || (brief.data.lines.length === 0 && brief.data.lot_alerts.length === 0);

  // Re-render periodically so the "Updated Xm ago" label keeps advancing on
  // its own instead of freezing at whatever it read on the last data fetch.
  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const stats = [
    { label: "Items", query: items, icon: <Package />, tone: "primary" as const },
    { label: "BOMs", query: boms, icon: <Layers />, tone: "accent" as const },
    { label: "Suppliers", query: suppliers, icon: <Truck />, tone: "success" as const },
    { label: "Purchase Orders", query: pos, icon: <ShoppingCart />, tone: "warning" as const },
  ];

  return (
    <div>
      <PageHeader
        eyebrow={new Date().toLocaleDateString(undefined, {
          weekday: "long",
          month: "long",
          day: "numeric",
        })}
        title={
          org.data ? (
            <>
              Welcome back,{" "}
              <span className="brand-gradient-animate bg-clip-text text-transparent">
                {org.data.name}
              </span>
            </>
          ) : (
            "Welcome back"
          )
        }
        description="Your production-planning workspace — track items, stock, and procurement in one place."
        actions={
          <Link to="/mfa">
            <Button variant="secondary" size="sm">
              <ShieldCheck /> Set up 2FA
            </Button>
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((s) => (
          <StatCard
            key={s.label}
            label={s.label}
            icon={s.icon}
            tone={s.tone}
            value={
              s.query.isLoading ? (
                <Skeleton className="h-7 w-10" />
              ) : s.query.isError ? (
                "—"
              ) : (
                (s.query.data?.length ?? 0)
              )
            }
          />
        ))}
      </div>

      <Card className="relative mt-8 overflow-hidden">
        <div aria-hidden className="absolute inset-x-0 top-0 h-[2px] brand-gradient-animate" />
        <CardHeader className="flex-row items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-warning/10 text-warning ring-1 ring-inset ring-warning/20">
            <Sunrise className="size-4" />
          </div>
          <div>
            <CardTitle>Morning Brief</CardTitle>
            <CardDescription>Items and lots needing attention, vs. 7 days ago</CardDescription>
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {brief.dataUpdatedAt > 0 && (
              <span className="text-xs text-muted-foreground">
                Updated {timeAgo(brief.dataUpdatedAt)}
              </span>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label="Refresh Morning Brief"
              onClick={() => brief.refetch()}
              disabled={brief.isFetching}
            >
              <RefreshCw className={cn("size-4", brief.isFetching && "animate-spin")} />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {brief.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : brief.isError ? (
            <p className="text-sm text-muted-foreground">Couldn't load the Morning Brief.</p>
          ) : briefEmpty ? (
            <p className="text-sm text-muted-foreground">Nothing needs attention today.</p>
          ) : (
            <div className="space-y-2">
              {brief.data!.lines.map((line) => (
                <Link
                  key={line.item_id}
                  to="/planning"
                  className={cn(
                    "flex items-center justify-between gap-3 rounded-md border-l-[6px] bg-secondary/20 px-3 py-2 text-sm transition-colors hover:translate-x-0.5 hover:bg-secondary/40",
                    STATUS_BORDER[line.status] ?? "border-l-border",
                  )}
                >
                  <div className="min-w-0">
                    <p className="truncate font-semibold">
                      {line.item_sku} — {line.item_name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {line.days_of_supply != null
                        ? `${line.days_of_supply.toFixed(1)} days of supply`
                        : "No demand history"}
                      {line.days_of_supply_delta != null &&
                        ` · ${line.days_of_supply_delta >= 0 ? "+" : ""}${line.days_of_supply_delta.toFixed(1)}d vs last week`}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {line.is_new_this_week && <Badge variant="danger">New</Badge>}
                    <Badge variant={inventoryStatusVariant(line.status)}>{humanize(line.status)}</Badge>
                  </div>
                </Link>
              ))}
              {brief.data!.lot_alerts.map((alert) => (
                <Link
                  key={alert.lot_id}
                  to="/lots"
                  className={cn(
                    "flex items-center justify-between gap-3 rounded-md border-l-[6px] bg-secondary/20 px-3 py-2 text-sm transition-colors hover:translate-x-0.5 hover:bg-secondary/40",
                    REASON_BORDER[alert.reason] ?? "border-l-border",
                  )}
                >
                  <div className="min-w-0">
                    <p className="truncate font-semibold">
                      {alert.item_sku} — Lot {alert.lot_number}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {alert.location_code}
                      {alert.reason === "expiring_soon" && alert.days_until_expiry != null
                        ? ` · expires in ${alert.days_until_expiry}d`
                        : " · on QA hold"}
                    </p>
                  </div>
                  <Badge variant={alert.reason === "on_hold" ? "warning" : "danger"}>
                    {alert.reason === "on_hold" ? "On Hold" : "Expiring"}
                  </Badge>
                </Link>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <h2 className="mb-4 mt-10 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Explore
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {LINKS.map((l, i) => {
          const t = EXPLORE_TONES[i % EXPLORE_TONES.length];
          return (
            <Link key={l.to} to={l.to} className="group rounded-lg focus-visible:outline-none">
              <Card
                className={cn(
                  "relative h-full transition-all duration-300 hover:-translate-y-1 group-focus-visible:ring-2 group-focus-visible:ring-ring",
                  t.card,
                )}
              >
                <ArrowUpRight className="absolute right-4 top-4 size-4 text-muted-foreground opacity-0 transition-all duration-300 group-hover:-translate-y-1 group-hover:translate-x-1 group-hover:opacity-100" />
                <CardContent className="flex items-start gap-4 p-5">
                  <div
                    className={cn(
                      "flex size-11 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset transition-transform duration-300 group-hover:scale-110 [&_svg]:size-5",
                      t.icon,
                    )}
                  >
                    <l.icon />
                  </div>
                  <div className="space-y-1">
                    <CardTitle className={cn("text-base transition-colors", t.title)}>
                      {l.label}
                    </CardTitle>
                    <CardDescription>{l.description}</CardDescription>
                  </div>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
