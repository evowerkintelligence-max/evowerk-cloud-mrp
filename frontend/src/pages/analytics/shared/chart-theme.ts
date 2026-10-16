import type * as echarts from "echarts";

// ─── Chart palette / defaults (copied from the desktop app) ──────────────────

export const C = {
  green: "#34d399",
  blue: "#6c8cff",
  amber: "#f59e0b",
  red: "#f87171",
  cyan: "#22d3ee",
  pink: "#ec4899",
  indigo: "#6366f1",
  violet: "#a78bfa",
};

// echarts paints series/axes/gridlines onto a <canvas>, and a canvas 2D
// context can't resolve CSS custom properties the way the DOM does — so read
// each design-system token's HSL channels off the document root and hand
// echarts a concrete hsl() string. The app is dark-only at runtime
// (index.html hardcodes <html class="dark">, no light/dark toggle), so
// resolving once at module load stays correct.
export function token(name: string, alpha?: number): string {
  // vitest runs with environment: "node" (see vite.config.ts) - this module's
  // module-scope TOOLTIP/AXIS_LABEL/etc. constants call token() at import
  // time, which would otherwise throw "document is not defined" the moment
  // any test imports this file (directly or transitively). Note this
  // fallback drops the `alpha` argument, so a caller like token("--border",
  // 0.4) gets opaque black instead of a translucent color on this path -
  // fine since it only matters in the already-nonexistent "no DOM"
  // production scenario, not worth plumbing through for that.
  if (typeof document === "undefined") return `hsl(0deg 0% 0%)`;
  const ch = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return alpha === undefined ? `hsl(${ch})` : `hsl(${ch} / ${alpha})`;
}

export const TOOLTIP = {
  trigger: "axis" as const,
  backgroundColor: token("--card"),
  borderColor: token("--border"),
  textStyle: { color: token("--foreground"), fontSize: 12 },
};

export const AXIS_LABEL = { color: token("--muted-foreground"), fontSize: 10 };
export const SPLIT_LINE = { lineStyle: { color: token("--muted-foreground", 0.15) } };
export const AXIS_LINE = { lineStyle: { color: token("--muted-foreground", 0.25) } };

// Live-style pace status: on-pace / behind / ahead against whichever target
// is in play (a mapped plan column, or an auto-baseline average).
export function paceStatus(pct: number): { label: string; color: string } {
  if (pct >= 100) return { label: "Ahead of pace", color: C.green };
  if (pct >= 85) return { label: "On pace", color: C.blue };
  if (pct >= 60) return { label: "Behind pace", color: C.amber };
  return { label: "Well behind pace", color: C.red };
}

export function oeeColor(pct: number): string {
  if (pct >= 85) return C.green;
  if (pct >= 65) return C.blue;
  if (pct >= 45) return C.amber;
  return C.red;
}

export interface SpcPoint {
  label: string;
  value: number;
  range_value?: number;
  in_control: boolean;
  violations?: number[];
}

// Mirrors backend/app/analytics/spc_calc.py RULE_DESCRIPTIONS.
export const NELSON_RULE_DESCRIPTIONS: Record<number, string> = {
  1: "Point beyond 3σ (outside control limits)",
  2: "9 points in a row on the same side of the centerline",
  3: "6 points in a row steadily increasing or decreasing",
  4: "14 points in a row alternating up and down",
  5: "2 of 3 points beyond 2σ on the same side",
  6: "4 of 5 points beyond 1σ on the same side",
  7: "15 points in a row within 1σ of the centerline (stratification)",
  8: "8 points in a row beyond 1σ with none within 1σ (mixture)",
};

/** Control chart (with Western Electric/Nelson rule violations) shared by the
 * Dataset Quality tab's defect-rate trend - the standalone SPC analytics page
 * used it too before being removed. */
export function controlChart(points: SpcPoint[], value: (p: SpcPoint) => number | undefined, cl: number, ucl: number, lcl: number, name: string) {
  return {
    tooltip: {
      ...TOOLTIP,
      formatter: (params: unknown) => {
        const p = (Array.isArray(params) ? params[0] : params) as { dataIndex: number };
        const pt = points[p.dataIndex];
        if (!pt) return "";
        const lines = [pt.label, `${name}: ${value(pt) ?? "—"}`];
        for (const rule of pt.violations ?? []) {
          lines.push(`⚠ Rule ${rule}: ${NELSON_RULE_DESCRIPTIONS[rule] ?? "control-chart rule violation"}`);
        }
        return lines.join("<br/>");
      },
    },
    grid: { top: 24, bottom: 28, left: 56, right: 88 },
    xAxis: {
      type: "category" as const,
      data: points.map((p) => p.label),
      axisLabel: AXIS_LABEL,
      axisLine: AXIS_LINE,
      boundaryGap: false,
    },
    yAxis: { type: "value" as const, scale: true, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [
      {
        name,
        type: "line" as const,
        symbol: "circle" as const,
        symbolSize: 6,
        data: points.map((p) => ({
          value: value(p),
          itemStyle: p.in_control ? { color: C.blue } : { color: C.red, borderColor: token("--foreground"), borderWidth: 1 },
        })),
        lineStyle: { width: 1.5, color: C.blue },
        markLine: {
          silent: true,
          symbol: "none" as const,
          data: [
            { yAxis: ucl, lineStyle: { color: C.red, type: "dashed" as const }, label: { formatter: `UCL ${ucl}`, color: C.red, fontSize: 10, position: "end" as const } },
            { yAxis: cl, lineStyle: { color: C.green, type: "solid" as const, opacity: 0.6 }, label: { formatter: `CL ${cl}`, color: C.green, fontSize: 10, position: "end" as const } },
            { yAxis: lcl, lineStyle: { color: C.red, type: "dashed" as const }, label: { formatter: `LCL ${lcl}`, color: C.red, fontSize: 10, position: "end" as const } },
          ],
        },
      },
    ],
  };
}

function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
}

// Single-series line with a gradient fill fading to transparent — the trend-chart look shared by Downtime, Throughput, and Scrap.
export function gradientAreaSeries(name: string, data: (number | null)[], color: string): echarts.LineSeriesOption {
  const rgb = hexToRgb(color);
  return {
    name,
    type: "line",
    smooth: true,
    symbol: "none",
    data,
    lineStyle: { width: 3, color },
    itemStyle: { color },
    areaStyle: {
      color: {
        type: "linear",
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: `rgba(${rgb},0.20)` },
          { offset: 1, color: `rgba(${rgb},0.01)` },
        ],
      },
    },
  };
}

// Pareto: bar + cumulative-% line on a dual axis with an 80% guide line — shared by Downtime and Scrap.
export function paretoOption({
  categories,
  values,
  cumulativePct,
  barName,
  barColor,
  yAxisName,
}: {
  categories: string[];
  values: number[];
  cumulativePct: number[];
  barName: string;
  barColor: string;
  yAxisName: string;
}): echarts.EChartsOption {
  return {
    tooltip: TOOLTIP,
    legend: { textStyle: { color: token("--muted-foreground"), fontSize: 11 }, top: 4 },
    grid: { top: 40, bottom: 40, left: 56, right: 56 },
    xAxis: {
      type: "category",
      data: categories,
      axisLabel: { ...AXIS_LABEL, rotate: categories.length > 6 ? 30 : 0 },
      axisLine: AXIS_LINE,
    },
    yAxis: [
      { type: "value", name: yAxisName, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
      { type: "value", min: 0, max: 100, axisLabel: { ...AXIS_LABEL, formatter: "{value}%" }, splitLine: { show: false } },
    ],
    series: [
      {
        name: barName,
        type: "bar",
        barMaxWidth: 36,
        data: values,
        itemStyle: { color: barColor, borderRadius: [4, 4, 0, 0], opacity: 0.85 },
      },
      {
        name: "Cumulative %",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        symbol: "circle",
        symbolSize: 6,
        data: cumulativePct,
        lineStyle: { color: C.amber, width: 2 },
        itemStyle: { color: C.amber },
        markLine: {
          silent: true,
          symbol: "none",
          data: [{ yAxis: 80 }],
          lineStyle: { color: C.amber, type: "dashed", opacity: 0.4 },
          label: { position: "insideEndTop", color: C.amber, fontSize: 10, formatter: "80%" },
        },
      },
    ],
  };
}

// Horizontal ranked bar — a category per row, value bar, label on the right —
// shared by OEE's machine breakdown, Throughput's machine breakdown, and Shifts.
export function rankedBarOption({
  categories,
  values,
  colorOf,
  formatter,
  isPct = false,
  barMaxWidth = 22,
  rightMargin = 64,
  markLineAt,
}: {
  categories: string[];
  values: number[];
  colorOf: (value: number, index: number) => string;
  formatter: (value: number, index: number) => string;
  isPct?: boolean;
  barMaxWidth?: number;
  rightMargin?: number;
  markLineAt?: number;
}): echarts.EChartsOption {
  return {
    tooltip: TOOLTIP,
    grid: { left: 88, right: rightMargin, top: 8, bottom: 12 },
    xAxis: {
      type: "value",
      ...(isPct ? { max: 100 } : {}),
      axisLabel: { ...AXIS_LABEL, ...(isPct ? { formatter: "{value}%" } : {}) },
      splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: "category",
      data: categories,
      axisLabel: { color: token("--foreground"), fontSize: 12, fontWeight: 600 },
      axisLine: AXIS_LINE,
      axisTick: { show: false },
    },
    series: [
      {
        type: "bar",
        barMaxWidth,
        data: values.map((v, i) => ({ value: v, itemStyle: { color: colorOf(v, i), borderRadius: [0, 4, 4, 0] } })),
        label: {
          show: true,
          position: "right",
          color: token("--foreground"),
          fontSize: 11,
          fontWeight: 600,
          formatter: (p: { dataIndex?: number }) => formatter(values[p.dataIndex ?? 0], p.dataIndex ?? 0),
        },
        ...(markLineAt !== undefined && {
          markLine: {
            silent: true,
            symbol: "none",
            data: [{ xAxis: markLineAt }],
            lineStyle: { color: C.green, type: "dashed", opacity: 0.4 },
            label: { show: false },
          },
        }),
      },
    ],
  };
}
