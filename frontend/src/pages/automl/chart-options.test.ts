import { describe, expect, it } from "vitest";
import { confusionMatrixOption, pdOption, regressionScatterOption, trajectoryOption } from "./chart-options";

type Opt = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("automl chart options", () => {
  it("confusionMatrixOption scales to the max cell and formats tooltips", () => {
    const opt = confusionMatrixOption(["a", "b"], [[3, 1], [0, 5]]) as Opt;
    expect(opt.visualMap.max).toBe(5);
    expect(opt.tooltip.formatter({ data: [1, 0, 1] })).toBe("actual a → predicted b: 1");
    expect(opt.tooltip.formatter({})).toBe("actual a → predicted a: 0");
    expect((confusionMatrixOption([], []) as Opt).visualMap.max).toBe(1);
  });

  it("regressionScatterOption draws the identity line over the data range", () => {
    const opt = regressionScatterOption([[-2, 4], [3, 1]]) as Opt;
    expect(opt.series[1].data).toEqual([[-2, -2], [4, 4]]);
  });

  it("pdOption switches between numeric line and categorical bar", () => {
    const num = pdOption([1, 2], [5, 6], true) as Opt;
    expect(num.xAxis.type).toBe("value");
    expect(num.series[0].type).toBe("line");
    const cats = pdOption(["a", "b", "c", "d", "e", "f", "g"], [1, 2, 3, 4, 5, 6, 7], false) as Opt;
    expect(cats.xAxis.axisLabel.rotate).toBe(30);
    expect(cats.series[0].type).toBe("bar");
    expect((pdOption(["a"], [1], false) as Opt).xAxis.axisLabel.rotate).toBe(0);
  });

  it("trajectoryOption adds a band only when every point has bounds", () => {
    const history = [{ time: "t0", actual: 1 }];
    const banded = trajectoryOption(history, [{ time: "t1", prediction: 2, lower: 1, upper: 3 }]) as Opt;
    expect(banded.series).toHaveLength(4);
    expect(banded.series[2].data).toEqual([null, 1]);
    const partial = trajectoryOption(history, [{ time: "t1", prediction: 2, lower: 1 }]) as Opt;
    expect(partial.series).toHaveLength(2);
    expect((trajectoryOption(history, []) as Opt).series).toHaveLength(2);
  });
});
