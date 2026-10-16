import { describe, expect, it } from "vitest";
import { deltaColor, fmtVal, resultToReportText } from "./config";
import type { SimResult } from "./types";

describe("simulation formatting", () => {
  it("formats currency, percent and plain metrics", () => {
    expect(fmtVal("daily_revenue", 1234.6)).toMatch(/^\$1.?235$/);
    expect(fmtVal("oee_pct", 75)).toBe("75.0%");
    expect(fmtVal("daily_capacity", 10)).toBe("10");
    expect(fmtVal("daily_capacity", NaN)).toBe("—");
  });

  it("colours a delta by whether lower is better", () => {
    expect(deltaColor("daily_revenue", "increase")).toBe("text-success");
    expect(deltaColor("daily_revenue", "decrease")).toBe("text-destructive");
    expect(deltaColor("defect_rate_pct", "decrease")).toBe("text-success");
    expect(deltaColor("defect_rate_pct", "increase")).toBe("text-destructive");
    expect(deltaColor("oee_pct", "unchanged")).toBe("text-muted-foreground");
  });

  it("writes known metrics then insights", () => {
    const result = {
      baseline: { oee_pct: 70, unknown_metric: 1 },
      projected: { oee_pct: 75, unknown_metric: 2 },
      insights: ["OEE up"],
    } as unknown as SimResult;
    expect(resultToReportText(result)).toBe("OEE: 70.0% → 75.0%\n\nOEE up");
  });
});
