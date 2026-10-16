import type * as echarts from "echarts";
import { gradientAreaSeries, C, TOOLTIP, AXIS_LABEL, SPLIT_LINE, AXIS_LINE } from "../analytics/shared";

export function confusionMatrixOption(labels: string[], matrix: number[][]): echarts.EChartsOption {
  const data: [number, number, number][] = [];
  let max = 0;
  matrix.forEach((row, i) =>
    row.forEach((v, j) => {
      data.push([j, i, v]);
      max = Math.max(max, v);
    }),
  );
  return {
    tooltip: {
      backgroundColor: "#1e293b",
      borderColor: "#334155",
      textStyle: { color: "#e2e8f0", fontSize: 12 },
      // echarts' formatter param type (CallbackDataParams) doesn't narrow per series
      // type; heatmap always calls this with `data: [number, number, number]` (see
      // the `data.push([j, i, v])` above).
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const d = (p.data ?? [0, 0, 0]) as [number, number, number];
        return `actual ${labels[d[1]]} → predicted ${labels[d[0]]}: ${d[2]}`;
      },
    },
    grid: { top: 24, bottom: 64, left: 96, right: 24 },
    xAxis: { type: "category", data: labels, name: "Predicted", nameLocation: "middle", nameGap: 36, axisLabel: AXIS_LABEL },
    yAxis: { type: "category", data: labels, name: "Actual", nameLocation: "middle", nameGap: 64, axisLabel: AXIS_LABEL },
    visualMap: { min: 0, max: max || 1, calculable: false, show: false, inRange: { color: ["#0f172a", C.blue] } },
    series: [{ type: "heatmap", data, label: { show: true, color: "#e2e8f0", fontSize: 10 } }],
  };
}

export function regressionScatterOption(points: [number, number][]): echarts.EChartsOption {
  const values = points.flat();
  const lo = Math.min(...values, 0);
  const hi = Math.max(...values, 1);
  return {
    tooltip: { trigger: "item", backgroundColor: "#1e293b", borderColor: "#334155", textStyle: { color: "#e2e8f0", fontSize: 12 } },
    grid: { top: 24, bottom: 40, left: 56, right: 24 },
    xAxis: { type: "value", name: "Actual", nameLocation: "middle", nameGap: 28, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, axisLine: AXIS_LINE },
    yAxis: { type: "value", name: "Predicted", nameLocation: "middle", nameGap: 44, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, axisLine: AXIS_LINE },
    series: [
      { type: "scatter", symbolSize: 6, data: points, itemStyle: { color: C.blue, opacity: 0.6 } },
      {
        type: "line",
        data: [[lo, lo], [hi, hi]],
        symbol: "none",
        lineStyle: { color: C.amber, type: "dashed", width: 1.5 },
        tooltip: { show: false },
      },
    ],
  };
}

export function pdOption(grid: (number | string)[], response: number[], isNumeric: boolean): echarts.EChartsOption {
  return {
    tooltip: TOOLTIP,
    grid: { top: 24, bottom: 48, left: 56, right: 24 },
    xAxis: {
      type: isNumeric ? "value" : "category",
      data: isNumeric ? undefined : (grid as string[]),
      axisLabel: { ...AXIS_LABEL, rotate: !isNumeric && grid.length > 6 ? 30 : 0 },
      axisLine: AXIS_LINE,
      splitLine: SPLIT_LINE,
    },
    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [
      isNumeric
        ? { type: "line", smooth: true, symbol: "none", data: grid.map((g, i) => [g, response[i]]), lineStyle: { width: 3, color: C.cyan } }
        : { type: "bar", data: response, itemStyle: { color: C.cyan, borderRadius: [4, 4, 0, 0] } },
    ],
  };
}

export function trajectoryOption(
  history: { time: string; actual: number }[],
  points: { time: string; prediction: number; lower?: number; upper?: number }[],
): echarts.EChartsOption {
  const categories = [...history.map((h) => h.time), ...points.map((p) => p.time)];
  const actualSeries: (number | null)[] = [...history.map((h) => h.actual), ...points.map(() => null)];
  const predSeries: (number | null)[] = [...history.map(() => null), ...points.map((p) => p.prediction)];
  const hasBand = points.length > 0 && points.every((p) => p.lower !== undefined && p.upper !== undefined);
  const lowerSeries: (number | null)[] = [...history.map(() => null), ...points.map((p) => p.lower ?? null)];
  const upperSeries: (number | null)[] = [...history.map(() => null), ...points.map((p) => p.upper ?? null)];

  return {
    tooltip: TOOLTIP,
    legend: { textStyle: { color: "#64748b", fontSize: 11 }, top: 4 },
    grid: { top: 40, bottom: 56, left: 56, right: 24 },
    xAxis: { type: "category", data: categories, axisLabel: { ...AXIS_LABEL, rotate: 30 }, axisLine: AXIS_LINE },
    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [
      gradientAreaSeries("Actual", actualSeries, C.blue),
      gradientAreaSeries("Forecast", predSeries, C.cyan),
      ...(hasBand
        ? [
            { name: "Lower (90%)", type: "line" as const, data: lowerSeries, symbol: "none", lineStyle: { color: C.violet, type: "dashed" as const, width: 1 } },
            { name: "Upper (90%)", type: "line" as const, data: upperSeries, symbol: "none", lineStyle: { color: C.violet, type: "dashed" as const, width: 1 } },
          ]
        : []),
    ],
  };
}
