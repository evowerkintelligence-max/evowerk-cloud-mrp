import * as echarts from "echarts";
import { AXIS_LABEL, AXIS_LINE, C, SPLIT_LINE, TOOLTIP, fmt, gradientAreaSeries, rankedBarOption, token } from "../analytics/shared";

export const CHART_TYPES = ["bar", "horizontal_bar", "line", "area", "pie", "donut"] as const;
export type ChartType = (typeof CHART_TYPES)[number];

export const CHART_TYPE_LABELS: Record<ChartType, string> = {
  bar: "Bar",
  horizontal_bar: "Horizontal bar",
  line: "Line",
  area: "Area",
  pie: "Pie",
  donut: "Donut",
};

// Named color palettes a dashboard can be viewed under. Stored client-side
// only (per-dashboard, in localStorage) - a display preference, not data, so
// it doesn't need a backend field or migration.
//
// index.html ships `<html class="dark">` unconditionally (no light-mode
// toggle wired up), so these are checked against the app's real dark card
// surface only (#171717 - see index.css --card) via the dataviz skill's
// validate_palette.js: adjacent-pair CVD >= 8 and normal-vision floor >= 15.
// Don't reshuffle without re-running the validator.
export const THEMES = {
  default: {
    label: "Default",
    colors: ["#3987e5", "#008300", "#d55181", "#c98500", "#199e70", "#d95926", "#9085e9", "#e66767"],
  },
  vibrant: {
    label: "Vibrant",
    colors: ["#3b82f6", "#16a34a", "#ec4899", "#a16207", "#0891b2", "#ea580c", "#8b5cf6", "#ef4444"],
  },
  // ponytail: monochrome is a deliberate lightness-ramp look a user opts into
  // (not the default), so it's exempt from the categorical-hue CVD checks
  // above - but every step must still clear the dark surface. (The old ramp
  // ran all the way to near-black steps that were nearly invisible on the
  // #171717 card - that's why "ugly" charts included some that looked broken.)
  mono: {
    label: "Monochrome",
    colors: ["#f8fafc", "#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b", "#475569", "#6b7280", "#334155"],
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

// Save-as-image + reset-zoom - free interactivity from echarts' own toolbox,
// no custom UI to build. Icons are styled to sit quietly in the card corner.
const TOOLBOX: echarts.EChartsOption["toolbox"] = {
  right: 4,
  top: 0,
  itemSize: 13,
  iconStyle: { borderColor: token("--muted-foreground") },
  emphasis: { iconStyle: { borderColor: token("--foreground") } },
  feature: {
    saveAsImage: { title: "Save as image", backgroundColor: token("--card") },
    restore: { title: "Reset zoom" },
  },
};

// A slider only earns its keep once there's something to zoom into - a
// handful of bars/points don't need it, and it'd just eat vertical space.
const DATA_ZOOM_THRESHOLD = 12;

function dataZoomFor(length: number, axis: "x" | "y"): echarts.EChartsOption["dataZoom"] | undefined {
  if (length <= DATA_ZOOM_THRESHOLD) return undefined;
  const axisKey = axis === "x" ? "xAxisIndex" : "yAxisIndex";
  return [
    { type: "inside", [axisKey]: 0 } as echarts.DataZoomComponentOption,
    {
      type: "slider",
      [axisKey]: 0,
      ...(axis === "x" ? { bottom: 4, height: 16 } : { right: 4, width: 14 }),
      borderColor: "transparent",
      fillerColor: token("--muted-foreground", 0.2),
      handleStyle: { color: token("--muted-foreground") },
      textStyle: { color: token("--muted-foreground"), fontSize: 10 },
    } as echarts.DataZoomComponentOption,
  ];
}

// Raw numbers in a tooltip read as noise (12345.6789) - format like the rest
// of the app's stat displays.
const VALUE_TOOLTIP = { ...TOOLTIP, valueFormatter: (v: unknown) => fmt(Number(v)) };

export function barChartOption(data: CategoryValueRow[], name = "Value", palette?: string[]): echarts.EChartsOption {
  const zoom = dataZoomFor(data.length, "x");
  return {
    tooltip: VALUE_TOOLTIP,
    color: palette,
    toolbox: TOOLBOX,
    dataZoom: zoom,
    grid: { top: 24, bottom: zoom ? 72 : 48, left: 56, right: 24 },
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
        emphasis: { itemStyle: { color: palette?.[0] ?? C.blue, opacity: 0.8 } },
      },
    ],
  };
}

export function horizontalBarChartOption(
  data: CategoryValueRow[],
  _name = "Value",
  palette?: string[],
): echarts.EChartsOption {
  const color = palette?.[0] ?? C.blue;
  const zoom = dataZoomFor(data.length, "y");
  return {
    ...rankedBarOption({
      categories: data.map((d) => d.category),
      values: data.map((d) => d.value),
      colorOf: () => color,
      formatter: (v) => fmt(v),
      rightMargin: zoom ? 88 : 64,
    }),
    tooltip: VALUE_TOOLTIP,
    toolbox: TOOLBOX,
    dataZoom: zoom,
  };
}

export function lineChartOption(data: CategoryValueRow[], name = "Value", palette?: string[]): echarts.EChartsOption {
  const color = palette?.[0] ?? C.blue;
  const zoom = dataZoomFor(data.length, "x");
  return {
    tooltip: VALUE_TOOLTIP,
    toolbox: TOOLBOX,
    dataZoom: zoom,
    grid: { top: 24, bottom: zoom ? 72 : 48, left: 56, right: 24 },
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
        type: "line" as const,
        symbol: "circle",
        symbolSize: 7,
        showSymbol: data.length <= 30,
        lineStyle: { width: 2.5, color },
        itemStyle: { color },
        data: data.map((d) => d.value),
      },
    ],
  };
}

export function areaChartOption(data: CategoryValueRow[], name = "Value", palette?: string[]): echarts.EChartsOption {
  const zoom = dataZoomFor(data.length, "x");
  return {
    tooltip: VALUE_TOOLTIP,
    toolbox: TOOLBOX,
    dataZoom: zoom,
    grid: { top: 24, bottom: zoom ? 72 : 48, left: 56, right: 24 },
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

function pieLikeOption(
  data: CategoryValueRow[],
  name: string,
  palette: string[] | undefined,
  radius: [string, string],
): echarts.EChartsOption {
  return {
    tooltip: { ...TOOLTIP, trigger: "item" as const, valueFormatter: (v) => fmt(Number(v)) },
    legend: { textStyle: { color: token("--muted-foreground"), fontSize: 11 }, bottom: 0 },
    color: palette,
    toolbox: TOOLBOX,
    series: [
      {
        name,
        type: "pie",
        radius,
        avoidLabelOverlap: true,
        itemStyle: { borderColor: token("--card"), borderWidth: 2 },
        label: { color: token("--foreground"), fontSize: 11 },
        data: data.map((d) => ({ name: d.category, value: d.value })),
      },
    ],
  };
}

export function pieChartOption(data: CategoryValueRow[], name = "Value", palette?: string[]): echarts.EChartsOption {
  return pieLikeOption(data, name, palette, ["40%", "70%"]);
}

export function donutChartOption(data: CategoryValueRow[], name = "Value", palette?: string[]): echarts.EChartsOption {
  return pieLikeOption(data, name, palette, ["58%", "78%"]);
}

export function chartOption(
  type: ChartType,
  data: CategoryValueRow[],
  name?: string,
  palette?: string[],
): echarts.EChartsOption {
  switch (type) {
    case "line":
      return lineChartOption(data, name, palette);
    case "area":
      return areaChartOption(data, name, palette);
    case "pie":
      return pieChartOption(data, name, palette);
    case "donut":
      return donutChartOption(data, name, palette);
    case "horizontal_bar":
      return horizontalBarChartOption(data, name, palette);
    default:
      return barChartOption(data, name, palette);
  }
}
