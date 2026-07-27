import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Maximize2, Minimize2, Rows3 } from "lucide-react";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sparkline } from "@/components/Sparkline";
import { BreakdownBars } from "@/components/BreakdownBars";

interface KpiDashboardDef {
  id: string;
  name: string;
  tier: "operator" | "supervisor";
  kpi_ids: string[];
  refresh_seconds: number;
}

interface KpiDef {
  id: string;
  name: string;
}

interface DashboardCard {
  kpi_id: string;
  dataset_id: string;
  name: string;
  unit: string | null;
  direction: string;
  value: number | null;
  error: string | null;
  computed_at: string;
  eval: { status: "ok" | "warn" | "breach" | "unknown"; trend: string } | null;
  breakdown: { label: string; value: number }[] | null;
}

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  ok: "success",
  warn: "warning",
  breach: "danger",
  unknown: "neutral",
};

const TREND_ARROW: Record<string, string> = { up: "↑", down: "↓", flat: "→", unknown: "" };

function useKpiHistory(kpiId: string, datasetId: string) {
  return useQuery<{ value: number | null }[]>({
    queryKey: ["kpi-history", kpiId, datasetId],
    queryFn: async () => {
      const res = await apiFetch(`/kpis/${kpiId}/history?dataset_id=${datasetId}&limit=20`);
      if (!res.ok) throw new Error("Failed to load history");
      return res.json();
    },
    enabled: Boolean(kpiId && datasetId),
  });
}

function KpiCard({ card }: { card: DashboardCard }) {
  const history = useKpiHistory(card.kpi_id, card.dataset_id);
  const sparkValues = useMemo(
    () => (history.data ?? []).map((h) => h.value).filter((v): v is number => v !== null).reverse(),
    [history.data],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-sm font-medium text-muted-foreground">
          {card.name}
          {card.eval && <Badge variant={STATUS_VARIANT[card.eval.status]}>{card.eval.status}</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {card.error ? (
          <Badge variant="danger">{card.error}</Badge>
        ) : (
          <>
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-extrabold tracking-tight tabular-nums">{card.value ?? "—"}</span>
              {card.unit && <span className="text-sm text-muted-foreground">{card.unit}</span>}
              {card.eval && <span className="text-lg text-muted-foreground">{TREND_ARROW[card.eval.trend]}</span>}
            </div>
            {sparkValues.length >= 2 && <Sparkline values={sparkValues} />}
            {card.breakdown && <BreakdownBars items={card.breakdown} />}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function KpiDashboards() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ name: "", tier: "supervisor" as "operator" | "supervisor", kpi_ids: [] as string[] });
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);

  const dashboards = useQuery<KpiDashboardDef[]>({
    queryKey: ["kpi-dashboards"],
    queryFn: async () => {
      const res = await apiFetch("/kpi-dashboards");
      if (!res.ok) throw new Error("Failed to load dashboards");
      return res.json();
    },
  });

  const kpis = useQuery<KpiDef[]>({
    queryKey: ["kpis"],
    queryFn: async () => {
      const res = await apiFetch("/kpis");
      if (!res.ok) throw new Error("Failed to load KPIs");
      return res.json();
    },
  });

  const active = dashboards.data?.find((d) => d.id === selectedId) ?? dashboards.data?.[0];

  const cards = useQuery<DashboardCard[]>({
    queryKey: ["kpi-dashboard-cards", active?.id],
    queryFn: async () => {
      const res = await apiFetch(`/kpi-dashboards/${active!.id}/cards`);
      if (!res.ok) throw new Error("Failed to compute dashboard");
      return res.json();
    },
    enabled: Boolean(active?.id),
    // ponytail: this is a GET, so React Query's own refetchInterval covers
    // auto-refresh — no need to hand-roll a setInterval the way the
    // mutation-based EngineScreen "compute" pattern has to.
    refetchInterval: autoRefresh && active ? active.refresh_seconds * 1000 : false,
  });

  const create = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/kpi-dashboards", {
        method: "POST",
        body: JSON.stringify({ name: form.name, tier: form.tier, kpi_ids: form.kpi_ids }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create dashboard");
      return res.json() as Promise<KpiDashboardDef>;
    },
    onSuccess: (d) => {
      setCreateOpen(false);
      setForm({ name: "", tier: "supervisor", kpi_ids: [] });
      qc.invalidateQueries({ queryKey: ["kpi-dashboards"] });
      setSelectedId(d.id);
    },
  });

  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else gridRef.current?.requestFullscreen();
  }

  function toggleKpi(id: string) {
    setForm((f) => ({
      ...f,
      kpi_ids: f.kpi_ids.includes(id) ? f.kpi_ids.filter((k) => k !== id) : [...f.kpi_ids, id],
    }));
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="KPI Dashboards"
        description="Refreshing views of your KPIs that pick up newly-computed values — tiered for operators (fast refresh, fullscreen) or supervisors (slower refresh, full detail)."
        actions={
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => navigate("/kpis")}>Definitions</Button>
            {dashboards.data && dashboards.data.length > 0 && (
              <Select value={active?.id ?? ""} onChange={(e) => setSelectedId(e.target.value)} className="w-56">
                {dashboards.data.map((d) => (
                  <option key={d.id} value={d.id}>{d.name} ({d.tier})</option>
                ))}
              </Select>
            )}
            <Button onClick={() => setCreateOpen(true)}>New dashboard</Button>
          </div>
        }
      />

      {dashboards.data && dashboards.data.length === 0 ? (
        <EmptyState
          icon={<Rows3 />}
          title="No dashboards yet"
          description="Create a named, ordered collection of KPIs — tag it operator for a fullscreen shop-floor tile view, or supervisor for the full card grid."
        />
      ) : active ? (
        <>
          <div className="flex items-center justify-end gap-4">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh ({active.refresh_seconds}s)
            </label>
            <Button variant="ghost" size="sm" onClick={toggleFullscreen}>
              {isFullscreen ? <Minimize2 /> : <Maximize2 />}
              {isFullscreen ? "Exit TV mode" : "TV mode"}
            </Button>
          </div>
          <div
            ref={gridRef}
            className={
              isFullscreen
                ? "grid gap-4 bg-background p-8 sm:grid-cols-2 lg:grid-cols-3"
                : "grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
            }
          >
            {cards.data?.map((card) => (
              <KpiCard key={card.kpi_id} card={card} />
            ))}
          </div>
        </>
      ) : null}

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} title="New dashboard">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
          </Field>
          <Field label="Tier" hint="Operator defaults to a fast 15s refresh; supervisor to 300s.">
            <Select
              value={form.tier}
              onChange={(e) => setForm((f) => ({ ...f, tier: e.target.value as "operator" | "supervisor" }))}
            >
              <option value="supervisor">Supervisor</option>
              <option value="operator">Operator</option>
            </Select>
          </Field>
          <Field label="KPIs">
            <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-input p-2">
              {kpis.data?.map((k) => (
                <label key={k.id} className="flex items-center gap-2 text-sm">
                  <Checkbox checked={form.kpi_ids.includes(k.id)} onChange={() => toggleKpi(k.id)} />
                  {k.name}
                </label>
              ))}
            </div>
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button type="submit" loading={create.isPending} disabled={form.kpi_ids.length === 0}>Create</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
