import * as echarts from "echarts";
import { AXIS_LABEL, AXIS_LINE, C, SPLIT_LINE, TOOLTIP, gradientAreaSeries } from "../analytics/shared";

export const CHART_TYPES = ["bar", "line", "pie"] as const;
export type ChartType = (typeof CHART_TYPES)[number];

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

export function barChartOption(data: CategoryValueRow[], name = "Value"): echarts.EChartsOption {
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
    series: [
      {
        name,
        type: "bar",
        barMaxWidth: 36,
        data: data.map((d) => d.value),
        itemStyle: { color: C.blue, borderRadius: [4, 4, 0, 0] },
      },
    ],
  };
}

export function lineChartOption(data: CategoryValueRow[], name = "Value"): echarts.EChartsOption {
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
    series: [gradientAreaSeries(name, data.map((d) => d.value), C.green)],
  };
}

export function pieChartOption(data: CategoryValueRow[], name = "Value"): echarts.EChartsOption {
  return {
    tooltip: { trigger: "item", backgroundColor: "#1e293b", borderColor: "#334155", textStyle: { color: "#e2e8f0", fontSize: 12 } },
    legend: { textStyle: { color: "#64748b", fontSize: 11 }, bottom: 0 },
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

export function chartOption(type: ChartType, data: CategoryValueRow[], name?: string): echarts.EChartsOption {
  if (type === "line") return lineChartOption(data, name);
  if (type === "pie") return pieChartOption(data, name);
  return barChartOption(data, name);
}
