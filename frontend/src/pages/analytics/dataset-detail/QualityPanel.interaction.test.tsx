// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { QualityPanel } from "./QualityPanel";

// echarts needs canvas, which jsdom lacks; the stand-in exposes how many points the chart got.
vi.mock("@/pages/analytics/shared/EChart", () => ({
  EChart: ({ option }: { option: { series?: { data?: unknown[] }[] } }) => (
    <div data-testid="echart">{option.series?.[0]?.data?.length ?? 0} points</div>
  ),
}));

const D = {
  id: "d1", name: "Inspections", original_filename: "i.csv", fmt: "csv", row_count: 500, size_bytes: 1, created_at: "",
  columns: [{ name: "defect", dtype: "VARCHAR" }, { name: "lot", dtype: "VARCHAR" }, { name: "day", dtype: "VARCHAR" }],
  preview: [],
};

const RESULT = {
  summary: {
    total_records: 5000, total_defects: 120, metric_label: "defect rate", metric_suffix: "%", defect_metric: 2.4,
    worst_batch: "LOT-7", dpmo: 24000, sigma_level: 3.48, cost_per_defect: 12.5, total_cost_of_quality: 1500,
  },
  batch_stats: [{ batch_id: "LOT-7", total_records: 1000, defect_rate: 0.05 }],
  correlations: [{ feature: "temp", correlation: 0.62, direction: "positive", strength: "strong" }],
  trend: [
    { date: "2026-10-01", defect_rate: 0.02, in_control: true },
    { date: "2026-10-02", defect_rate: 0.09, in_control: false, violations: [1] },
  ],
  trend_control: {
    chart_type: "p", centerline: 0.03, ucl: 0.06, lcl: 0, out_of_control_count: 1, low_sample_warning: true, min_baseline: 20,
  },
  warnings: ["Only 2 periods"],
};

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLInputElement;
}

describe("QualityPanel", () => {
  it("needs a defect column, then posts the mapping and renders the analysis", async () => {
    const api = mockApi({ "POST /datasets/d1/quality/analyze": RESULT });
    renderPage(<QualityPanel d={D} />);
    const analyze = screen.getByRole("button", { name: "Analyze quality" }) as HTMLButtonElement;
    expect(analyze.disabled).toBe(true);

    await userEvent.selectOptions(field("Defect column (0/1 flag or count, required)"), "defect");
    await userEvent.selectOptions(field("Batch/lot column (optional)"), "lot");
    await userEvent.selectOptions(field("Timestamp column (optional, enables trend chart)"), "day");
    await userEvent.clear(field("Defect opportunities per unit (for DPMO)"));
    await userEvent.type(field("Defect opportunities per unit (for DPMO)"), "4");
    await userEvent.type(field("Cost per defect, $ (optional, for Cost of Quality)"), "12.5");
    await userEvent.click(analyze);

    expect(await screen.findByText("Only 2 periods")).toBeTruthy();
    expect(api.calls[0].body).toEqual({
      defect_column: "defect", batch_column: "lot", timestamp_column: "day", opportunities_per_unit: 4, cost_per_defect: 12.5,
    });
    expect(screen.getByText("2.4% defect rate")).toBeTruthy();
    expect(screen.getByText(/120 defects across 5,000\s+records\. Worst batch: LOT-7/)).toBeTruthy();
    expect(screen.getByText("$12.5")).toBeTruthy();
    expect(screen.getByText("$1,500")).toBeTruthy();
    expect(within(screen.getByText("temp").closest("tr") as HTMLElement).getByText("strong")).toBeTruthy();
    expect(within(screen.getByText("LOT-7", { selector: "td" }).closest("tr") as HTMLElement).getByText("1,000")).toBeTruthy();
    // SPC: provisional-limits and out-of-control alerts, chart with both periods, p-chart note.
    expect(screen.getByText(/Only 2 period\(s\) — control limits need at least\s+20/)).toBeTruthy();
    expect(screen.getByText(/1 period\(s\) outside control limits/)).toBeTruthy();
    expect(screen.getByTestId("echart").textContent).toBe("2 points");
    expect(screen.getByText(/Proportion \(p\) chart/)).toBeTruthy();
  });

  it("omits optional fields and hides empty sections", async () => {
    const api = mockApi({
      "POST /datasets/d1/quality/analyze": {
        ...RESULT,
        summary: { ...RESULT.summary, dpmo: null, sigma_level: null, cost_per_defect: null, total_cost_of_quality: null },
        batch_stats: [], correlations: [], trend: [], trend_control: null, warnings: undefined,
      },
    });
    renderPage(<QualityPanel d={D} />);
    await userEvent.selectOptions(field("Defect column (0/1 flag or count, required)"), "defect");
    await userEvent.clear(field("Defect opportunities per unit (for DPMO)"));
    await userEvent.click(screen.getByRole("button", { name: "Analyze quality" }));

    expect(await screen.findByText("2.4% defect rate")).toBeTruthy();
    expect(api.calls[0].body).toEqual({ defect_column: "defect" });
    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(screen.queryByText("Feature correlations with defects")).toBeNull();
    expect(screen.queryByText("Batch breakdown")).toBeNull();
    expect(screen.queryByTestId("echart")).toBeNull();
  });

  it("shows the server error", async () => {
    mockApi({
      "POST /datasets/d1/quality/analyze": new Response(JSON.stringify({ detail: "defect is not numeric" }), { status: 422 }),
    });
    renderPage(<QualityPanel d={D} />);
    await userEvent.selectOptions(field("Defect column (0/1 flag or count, required)"), "lot");
    await userEvent.click(screen.getByRole("button", { name: "Analyze quality" }));
    await waitFor(() => expect(screen.getByText("defect is not numeric")).toBeTruthy());
  });
});
