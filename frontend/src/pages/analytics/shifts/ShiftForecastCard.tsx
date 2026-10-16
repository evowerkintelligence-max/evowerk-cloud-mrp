import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AXIS_LABEL, AXIS_LINE, C, EChart, SPLIT_LINE, TOOLTIP } from "../shared";
import {
  useAutoMLRun,
  useAutoMLRuns,
  useCreateAutoMLRun,
  useForecastTrajectory,
} from "../../automl/api";

interface ForecastSource {
  dataset_id: string;
  target_column: string;
  time_column: string;
  metric_label: string;
  rows: number;
}

// N_LAGS + 1 in backend/app/services/automl_engine.py's recursive trajectory
// walk - the minimum history a forecast run needs before it can train at all.
const MIN_FORECAST_ROWS = 15;

function useShiftForecastSource(shift: string, requestBody: Record<string, unknown> | null) {
  return useQuery<ForecastSource>({
    queryKey: ["shift-forecast-source", shift, requestBody],
    queryFn: async () => {
      const res = await apiFetch("/analytics/shifts/forecast-source", {
        method: "POST",
        body: JSON.stringify({ ...requestBody, shift }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to prepare forecast data");
      return res.json();
    },
    enabled: !!requestBody && !!shift,
  });
}

// Single-shift forecast card - materializes the shift's trend into a Dataset
// via forecast-source, then trains/polls/renders through the *existing*
// AutoML endpoints and hooks (useCreateAutoMLRun/useAutoMLRun/
// useForecastTrajectory), same as the AutoML page itself uses.
export function ShiftForecastCard({ shift, requestBody }: { shift: string; requestBody: Record<string, unknown> | null }) {
  const source = useShiftForecastSource(shift, requestBody);
  const runs = useAutoMLRuns();
  // A run started here belongs to the shift it was started for; switching shift drops it.
  const [startedRun, setStartedRun] = useState<{ shift: string; id: string } | null>(null);
  const runId = startedRun?.shift === shift ? startedRun.id : null;

  const existingRun = source.data
    ? [...(runs.data ?? [])]
        .filter((r) => r.dataset_id === source.data!.dataset_id && r.task === "forecasting")
        .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
    : undefined;
  const activeRunId = runId ?? existingRun?.id;

  const run = useAutoMLRun(activeRunId);
  const trainMut = useCreateAutoMLRun();
  const trajectory = useForecastTrajectory(activeRunId, run.data?.status === "success");

  function train() {
    if (!source.data) return;
    trainMut.mutate(
      {
        name: `Shift forecast: ${shift}`,
        dataset_id: source.data.dataset_id,
        task: "forecasting",
        target_column: source.data.target_column,
        feature_columns: [],
        thoroughness: "fast",
        algorithms: null,
        handle_class_imbalance: true,
        time_column: source.data.time_column,
        horizon: 14,
        forecast_strategy: "recursive",
        time_budget_seconds: null,
        search_method: "random",
        auto_retrain: false,
      },
      { onSuccess: (r) => setStartedRun({ shift, id: r.id }) },
    );
  }

  const isTraining = trainMut.isPending || run.data?.status === "queued" || run.data?.status === "running";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Forecast — {shift}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {source.isLoading && <p className="text-sm text-muted-foreground">Preparing forecast data…</p>}
        {source.isError && <p className="text-sm text-destructive">{(source.error as Error).message}</p>}
        {source.data && (
          <>
            <p className="text-xs text-muted-foreground">
              {source.data.metric_label} · {source.data.rows} day(s) of history
              {source.data.rows < MIN_FORECAST_ROWS &&
                ` — forecasting needs at least ${MIN_FORECAST_ROWS} days of history for this shift.`}
            </p>
            <Button size="sm" loading={isTraining} onClick={train}>
              {activeRunId ? "Retrain forecast" : "Train forecast"}
            </Button>
            {run.data?.status === "failed" && <p className="text-sm text-destructive">{run.data.error}</p>}
            {trajectory.data && (
              <>
                <EChart
                  height={220}
                  option={{
                    tooltip: TOOLTIP,
                    grid: { top: 20, bottom: 40, left: 48, right: 20 },
                    xAxis: {
                      type: "category",
                      data: [
                        ...trajectory.data.history.map((h) => h.time),
                        ...trajectory.data.points.map((p) => p.time),
                      ],
                      axisLabel: { ...AXIS_LABEL, rotate: 30 },
                      axisLine: AXIS_LINE,
                    },
                    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                    series: [
                      {
                        name: "Actual",
                        type: "line" as const,
                        symbol: "none" as const,
                        data: [
                          ...trajectory.data.history.map((h) => h.actual),
                          ...trajectory.data.points.map(() => null),
                        ],
                        lineStyle: { width: 2, color: C.blue },
                      },
                      {
                        name: "Forecast",
                        type: "line" as const,
                        symbol: "none" as const,
                        data: [
                          ...trajectory.data.history.map(() => null),
                          ...trajectory.data.points.map((p) => p.prediction),
                        ],
                        lineStyle: { width: 2, color: C.amber, type: "dashed" as const },
                      },
                    ],
                  }}
                />
                {trajectory.data.notes.map((n, i) => (
                  <p key={i} className="text-xs text-muted-foreground">
                    {n}
                  </p>
                ))}
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
