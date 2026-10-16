import { describe, expect, it } from "vitest";
import { C, controlChart, oeeColor, paceStatus, paretoOption, rankedBarOption, token, type SpcPoint } from "./chart-theme";

describe("chart-theme", () => {
  it("token falls back to opaque black without a DOM", () => {
    expect(token("--card")).toBe("hsl(0deg 0% 0%)");
    expect(token("--card", 0.5)).toBe("hsl(0deg 0% 0%)");
  });

  it("paceStatus and oeeColor bucket by threshold", () => {
    expect([100, 90, 70, 10].map((p) => paceStatus(p).label)).toEqual([
      "Ahead of pace",
      "On pace",
      "Behind pace",
      "Well behind pace",
    ]);
    expect([90, 70, 50, 10].map(oeeColor)).toEqual([C.green, C.blue, C.amber, C.red]);
  });

  it("controlChart tooltip lists rule violations and colors out-of-control points", () => {
    const points: SpcPoint[] = [
      { label: "a", value: 1, in_control: true },
      { label: "b", value: 9, in_control: false, violations: [1, 99] },
    ];
    const opt = controlChart(points, (p) => (p.label === "a" ? undefined : p.value), 5, 8, 2, "Rate");
    const fmt = opt.tooltip.formatter;
    expect(fmt([{ dataIndex: 0 }])).toBe("a<br/>Rate: —");
    expect(fmt({ dataIndex: 1 })).toContain("Rule 99: control-chart rule violation");
    expect(fmt({ dataIndex: 1 })).toContain("Rule 1: Point beyond 3σ");
    expect(fmt({ dataIndex: 5 })).toBe("");
    expect(opt.series[0].data[0].itemStyle.color).toBe(C.blue);
    expect(opt.series[0].data[1].itemStyle.color).toBe(C.red);
  });

  it("paretoOption rotates labels only for many categories", () => {
    const rotate = (n: number) => {
      const cats = Array.from({ length: n }, (_, i) => `c${i}`);
      const opt = paretoOption({ categories: cats, values: [], cumulativePct: [], barName: "b", barColor: C.red, yAxisName: "y" });
      return (opt.xAxis as { axisLabel: { rotate: number } }).axisLabel.rotate;
    };
    expect(rotate(3)).toBe(0);
    expect(rotate(7)).toBe(30);
  });

  it("rankedBarOption applies pct axis, mark line and label formatter", () => {
    const plain = rankedBarOption({ categories: ["x"], values: [3], colorOf: () => C.red, formatter: (v) => `${v}` });
    const plainSeries = (plain.series as Record<string, unknown>[])[0];
    expect(plainSeries.markLine).toBeUndefined();
    expect((plain.xAxis as { max?: number }).max).toBeUndefined();
    const label = plainSeries.label as { formatter: (p: { dataIndex?: number }) => string };
    expect(label.formatter({})).toBe("3");
    expect(label.formatter({ dataIndex: 0 })).toBe("3");

    const pct = rankedBarOption({ categories: ["x"], values: [3], colorOf: () => C.red, formatter: String, isPct: true, markLineAt: 85 });
    expect((pct.xAxis as { max?: number }).max).toBe(100);
    expect((pct.series as Record<string, unknown>[])[0].markLine).toBeDefined();
  });
});
