import * as echarts from "echarts";
import { AXIS_LABEL, AXIS_LINE, C, SPLIT_LINE, TOOLTIP, gradientAreaSeries } from "../analytics/shared";

export const CHART_TYPES = ["bar", "line", "pie"] as const;
export type ChartType = (typeof CHART_TYPES)[number];

// Named color palettes a dashboard can be viewed under. Stored client-side
// only (per-dashboard, in localStorage) - a display preference, not data, so
// it doesn't need a backend field or migration.
export const THEMES = {
  default: { label: "Default", colors: [C.blue, C.green, C.amber, C.red, C.cyan, C.pink, C.indigo, C.violet] },
  vibrant: {
    label: "Vibrant",
    colors: ["#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#3b82f6", "#8b5cf6", "#ec4899"],
  },
  pastel: {
    label: "Pastel",
    colors: ["#fca5a5", "#fdba74", "#fde047", "#86efac", "#67e8f9", "#93c5fd", "#c4b5fd", "#f9a8d4"],
  },
  mono: {
    label: "Monochrome",
    colors: ["#0f172a", "#1e293b", "#334155", "#475569", "#64748b", "#94a3b8", "#cbd5e1", "#e2e8f0"],
  },
} as const;
export type ThemeKey = keyof typeof THEMES;

export interface CategoryValueRow {
  category: string;
  value: number;
}

/** Extracts {category, value} pairs from a query result, given which output
 * column is the category and which is the value. */
export function toCategoryValueRows(
  columns: string[],
  rows: Record<string, unknown>[],
  categoryField: string,
  valueField: string,
): CategoryValueRow[] {
  if (!columns.includes(categoryField) || !columns.includes(valueField)) return [];
  return rows.map((r) => ({
    category: String(r[categoryField] ?? ""),
    value: Number(r[valueField]) || 0,
  }));
}

export function barChartOption(data: CategoryValueRow[], name = "Value", palette?: string[]): echarts.EChartsOption {
  return {
    tooltip: TOOLTIP,
    color: palette,
    grid: { top: 24, bottom: 48, left: 56, right: 24 },
    xAxis: {
      type: "category",
      data: data.map((d) => d.category),
      axisLabel: { ...AXIS_LABEL, rotate: data.length > 6 ? 30 : 0 },
      axisLine: AXIS_LINE,
    },
    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [
      {
        name,
        type: "bar",
        barMaxWidth: 36,
        data: data.map((d) => d.value),
        itemStyle: { color: palette?.[0] ?? C.blue, borderRadius: [4, 4, 0, 0] },
      },
    ],
  };
}

export function lineChartOption(data: CategoryValueRow[], name = "Value", palette?: string[]): echarts.EChartsOption {
  return {
    tooltip: TOOLTIP,
    grid: { top: 24, bottom: 48, left: 56, right: 24 },
    xAxis: {
      type: "category",
      data: data.map((d) => d.category),
      axisLabel: { ...AXIS_LABEL, rotate: data.length > 6 ? 30 : 0 },
      axisLine: AXIS_LINE,
    },
    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [gradientAreaSeries(name, data.map((d) => d.value), palette?.[0] ?? C.green)],
  };
}

export function pieChartOption(data: CategoryValueRow[], name = "Value", palette?: string[]): echarts.EChartsOption {
  return {
    tooltip: { trigger: "item", backgroundColor: "#1e293b", borderColor: "#334155", textStyle: { color: "#e2e8f0", fontSize: 12 } },
    legend: { textStyle: { color: "#64748b", fontSize: 11 }, bottom: 0 },
    color: palette,
    series: [
      {
        name,
        type: "pie",
        radius: ["40%", "70%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: "#0f172a", borderWidth: 2 },
        label: { color: "#94a3b8", fontSize: 11 },
        data: data.map((d) => ({ name: d.category, value: d.value })),
      },
    ],
  };
}

export function chartOption(
  type: ChartType,
  data: CategoryValueRow[],
  name?: string,
  palette?: string[],
): echarts.EChartsOption {
  if (type === "line") return lineChartOption(data, name, palette);
  if (type === "pie") return pieChartOption(data, name, palette);
  return barChartOption(data, name, palette);
}
