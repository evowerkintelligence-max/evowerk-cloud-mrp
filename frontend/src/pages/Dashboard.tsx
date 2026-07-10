import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  BarChart3,
  Boxes,
  CalendarRange,
  Database,
  FileText,
  Layers,
  MapPin,
  Package,
  ShieldCheck,
  ShoppingCart,
  Sparkles,
  Truck,
  Warehouse,
} from "lucide-react";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardDescription, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface OrgResponse {
  id: string;
  name: string;
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
  { to: "/items", label: "Items", description: "SKUs, types, and preferred suppliers", icon: Package },
  { to: "/boms", label: "Bills of Materials", description: "Component structures and versions", icon: Layers },
  { to: "/suppliers", label: "Suppliers", description: "Vendor directory and contacts", icon: Truck },
  { to: "/purchase-orders", label: "Purchase Orders", description: "Draft, submit, and receive POs", icon: ShoppingCart },
  { to: "/lots", label: "Lots", description: "Lot-tracked stock and holds", icon: Boxes },
  { to: "/inventory", label: "Inventory", description: "On-hand balances by location", icon: Warehouse },
  { to: "/locations", label: "Locations", description: "Warehouses and storage", icon: MapPin },
  { to: "/planning", label: "Planning", description: "Forecasts, MRP, and auto-POs", icon: CalendarRange },
  { to: "/analytics", label: "Analytics", description: "OEE, downtime, throughput, scrap, shifts, SPC", icon: BarChart3 },
  { to: "/datasets", label: "Datasets", description: "Upload, clean, and query production data", icon: Database },
  { to: "/ask", label: "Ask", description: "Natural-language Q&A over your data", icon: Sparkles },
  { to: "/reports", label: "Reports", description: "Export PDF/CSV reports", icon: FileText },
];

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

  const stats = [
    { label: "Items", query: items, icon: <Package /> },
    { label: "BOMs", query: boms, icon: <Layers /> },
    { label: "Suppliers", query: suppliers, icon: <Truck /> },
    { label: "Purchase Orders", query: pos, icon: <ShoppingCart /> },
  ];

  return (
    <div>
      <PageHeader
        title={org.data ? `Welcome, ${org.data.name}` : "Welcome"}
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

      <h2 className="mb-4 mt-10 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Explore
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {LINKS.map((l) => (
          <Link key={l.to} to={l.to} className="group rounded-lg focus-visible:outline-none">
            <Card className="h-full transition-colors hover:border-primary/40 group-focus-visible:ring-2 group-focus-visible:ring-ring">
              <CardContent className="flex items-start gap-4 p-5">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary [&_svg]:size-5">
                  <l.icon />
                </div>
                <div className="space-y-1">
                  <CardTitle className="text-base">{l.label}</CardTitle>
                  <CardDescription>{l.description}</CardDescription>
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
