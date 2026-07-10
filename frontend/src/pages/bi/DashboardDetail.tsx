import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowLeft, ArrowUp, Trash2 } from "lucide-react";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EChart } from "../analytics/shared";
import { chartOption, toCategoryValueRows, type ChartType } from "./shared";

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

function ChartCard({
  chart,
  onMoveUp,
  onMoveDown,
  onDelete,
}: {
  chart: ChartMeta;
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

  const rows = data.data ? toCategoryValueRows(data.data.columns, data.data.rows, chart.category_field, chart.value_field) : [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">{chart.name}</CardTitle>
        <div className="flex gap-1">
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
      <CardContent>
        {data.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : data.isError ? (
          <Alert variant="destructive">Failed to load chart data.</Alert>
        ) : rows.length === 0 ? (
          <Alert variant="warning">No data.</Alert>
        ) : (
          <EChart option={chartOption(chart.chart_type, rows)} height={280} />
        )}
      </CardContent>
    </Card>
  );
}

export function DashboardDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

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

  const removeChart = useMutation({
    mutationFn: async (chartId: string) => {
      const res = await apiFetch(`/bi/charts/${chartId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete chart");
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

  function swapPositions(i: number, j: number) {
    if (j < 0 || j >= charts.length) return;
    reposition.mutate({ chartId: charts[i].id, position: charts[j].position });
    reposition.mutate({ chartId: charts[j].id, position: charts[i].position });
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
            <Button variant="destructive" size="sm" onClick={() => deleteDashboard.mutate()}>
              <Trash2 /> Delete dashboard
            </Button>
          </div>
        }
      />

      {charts.length === 0 ? (
        <Alert>No charts on this dashboard yet — add one from the chart builder.</Alert>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {charts.map((c, i) => (
            <ChartCard
              key={c.id}
              chart={c}
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
