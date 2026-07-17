import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowLeft, ArrowUp, GripVertical, Trash2 } from "lucide-react";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EChart, SendToReportButton } from "../analytics/shared";
import { chartOption, THEMES, toCategoryValueRows, type ChartType, type ThemeKey } from "./shared";

interface ChartMeta {
  id: string;
  name: string;
  chart_type: ChartType;
  category_field: string;
  value_field: string;
  position: number;
}

interface DashboardDetail {
  id: string;
  name: string;
  charts: ChartMeta[];
}

interface ChartData {
  columns: string[];
  rows: Record<string, unknown>[];
}

function themeKey(id: string): string {
  return `bi-dashboard-theme-${id}`;
}

function ChartCard({
  chart,
  filterText,
  palette,
  draggable,
  onDragStart,
  onDrop,
  onMoveUp,
  onMoveDown,
  onDelete,
}: {
  chart: ChartMeta;
  filterText: string;
  palette: string[];
  draggable: boolean;
  onDragStart: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onDelete: () => void;
}) {
  const data = useQuery<ChartData>({
    queryKey: ["bi-chart-data", chart.id],
    queryFn: async () => {
      const res = await apiFetch(`/bi/charts/${chart.id}/data`);
      if (!res.ok) throw new Error("Failed to load chart data");
      return res.json();
    },
  });

  const allRows = data.data ? toCategoryValueRows(data.data.columns, data.data.rows, chart.category_field, chart.value_field) : [];
  const rows = filterText.trim()
    ? allRows.filter((r) => r.category.toLowerCase().includes(filterText.trim().toLowerCase()))
    : allRows;

  return (
    <Card onDragOver={(e) => draggable && e.preventDefault()} onDrop={onDrop}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div className="flex items-center gap-1">
          <span
            draggable={draggable}
            onDragStart={onDragStart}
            className="cursor-move text-muted-foreground"
            title="Drag to reorder"
          >
            <GripVertical className="size-4" />
          </span>
          <CardTitle className="text-base">{chart.name}</CardTitle>
        </div>
        <div className="flex gap-1">
          {/* Keyboard/screen-reader alternative to the drag handle above -
              native HTML5 drag-and-drop has no keyboard equivalent. */}
          <Button variant="ghost" size="icon" onClick={onMoveUp} title="Move up">
            <ArrowUp className="size-4" />
          </Button>
          <Button variant="ghost" size="icon" onClick={onMoveDown} title="Move down">
            <ArrowDown className="size-4" />
          </Button>
          <Button variant="ghost" size="icon" onClick={onDelete} title="Remove from dashboard">
            <Trash2 className="size-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        <SendToReportButton spec={{ type: "bi_chart", chart_id: chart.id }} label={chart.name} />
        {data.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : data.isError ? (
          <Alert variant="destructive">Failed to load chart data.</Alert>
        ) : rows.length === 0 ? (
          <Alert variant="warning">{filterText.trim() ? "No data matches the filter." : "No data."}</Alert>
        ) : (
          <EChart option={chartOption(chart.chart_type, rows, undefined, palette)} height={280} />
        )}
      </CardContent>
    </Card>
  );
}

export function DashboardDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [filterText, setFilterText] = useState("");
  const [theme, setTheme] = useState<ThemeKey>(() => {
    const saved = id ? localStorage.getItem(themeKey(id)) : null;
    return saved && saved in THEMES ? (saved as ThemeKey) : "default";
  });
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  function selectTheme(next: ThemeKey) {
    setTheme(next);
    if (id) localStorage.setItem(themeKey(id), next);
  }

  const dashboard = useQuery<DashboardDetail>({
    queryKey: ["bi-dashboard", id],
    queryFn: async () => {
      const res = await apiFetch(`/bi/dashboards/${id}`);
      if (!res.ok) throw new Error("Dashboard not found");
      return res.json();
    },
    enabled: !!id,
  });

  const reposition = useMutation({
    mutationFn: async ({ chartId, position }: { chartId: string; position: number }) => {
      const res = await apiFetch(`/bi/charts/${chartId}`, { method: "PATCH", body: JSON.stringify({ position }) });
      if (!res.ok) throw new Error("Failed to reorder chart");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bi-dashboard", id] }),
  });

  // Unlinks the chart from this dashboard (dashboard_id -> null) rather than
  // deleting it, matching the "Remove from dashboard" label - the chart and
  // its saved query survive and can be re-added to a dashboard later.
  const removeChart = useMutation({
    mutationFn: async (chartId: string) => {
      const res = await apiFetch(`/bi/charts/${chartId}`, {
        method: "PATCH",
        body: JSON.stringify({ dashboard_id: null }),
      });
      if (!res.ok) throw new Error("Failed to remove chart from dashboard");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bi-dashboard", id] }),
  });

  const deleteDashboard = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/bi/dashboards/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete dashboard");
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["bi-dashboards"] });
      navigate("/bi");
    },
  });

  if (dashboard.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (!dashboard.data) return <Alert variant="destructive">Dashboard not found.</Alert>;
  const d = dashboard.data;
  const charts = [...d.charts].sort((a, b) => a.position - b.position);
  const palette = [...THEMES[theme].colors];

  // Reassigns positions 0..n-1 to match `order`, persisting only the charts
  // whose position actually changed - covers both the arrow-button swap
  // (adjacent) and an arbitrary drag-and-drop move in one path.
  function reorderTo(order: ChartMeta[]) {
    order.forEach((c, i) => {
      if (c.position !== i) reposition.mutate({ chartId: c.id, position: i });
    });
  }

  function swapPositions(i: number, j: number) {
    if (j < 0 || j >= charts.length) return;
    const next = [...charts];
    [next[i], next[j]] = [next[j], next[i]];
    reorderTo(next);
  }

  function onDropAt(targetIndex: number) {
    if (dragIndex === null || dragIndex === targetIndex) return;
    const next = [...charts];
    const [moved] = next.splice(dragIndex, 1);
    next.splice(targetIndex, 0, moved);
    reorderTo(next);
    setDragIndex(null);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={d.name}
        description={`${charts.length} chart${charts.length === 1 ? "" : "s"}`}
        actions={
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => navigate("/bi")}>
              <ArrowLeft /> Back to dashboards
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => {
                if (confirm(`Delete dashboard "${d.name}"? This cannot be undone.`)) deleteDashboard.mutate();
              }}
            >
              <Trash2 /> Delete dashboard
            </Button>
          </div>
        }
      />

      {deleteDashboard.isError && (
        <Alert variant="destructive">{(deleteDashboard.error as Error).message}</Alert>
      )}

      {charts.length > 0 && (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Filter charts (by category)" className="min-w-[16rem]">
            <Input
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="e.g. a SKU or label…"
            />
          </Field>
          <Field label="Theme" className="w-48">
            <Select value={theme} onChange={(e) => selectTheme(e.target.value as ThemeKey)}>
              {Object.entries(THEMES).map(([key, t]) => (
                <option key={key} value={key}>
                  {t.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      )}

      {charts.length === 0 ? (
        <Alert>No charts on this dashboard yet — add one from the chart builder.</Alert>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {charts.map((c, i) => (
            <ChartCard
              key={c.id}
              chart={c}
              filterText={filterText}
              palette={palette}
              draggable
              onDragStart={() => setDragIndex(i)}
              onDrop={(e) => {
                e.preventDefault();
                onDropAt(i);
              }}
              onMoveUp={() => swapPositions(i, i - 1)}
              onMoveDown={() => swapPositions(i, i + 1)}
              onDelete={() => removeChart.mutate(c.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
