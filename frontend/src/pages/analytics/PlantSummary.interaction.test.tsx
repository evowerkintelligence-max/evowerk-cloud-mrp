// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { PlantSummary } from "./PlantSummary";

// echarts paints to <canvas>, which jsdom lacks.
vi.mock("echarts", () => ({ init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }) }));

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});

const DS = {
  id: "ds1",
  name: "Plant",
  original_filename: "p.csv",
  fmt: "csv",
  row_count: 500,
  size_bytes: 1024,
  created_at: "2026-01-01T00:00:00Z",
  columns: ["planned_time", "operating_time", "total_units", "good_units", "line", "date", "scrap", "defects"].map((name) => ({
    name,
    dtype: "VARCHAR",
  })),
};

const RESULT = {
  oee: 78.4,
  availability: 91,
  performance: 89,
  quality: 96.8,
  performance_estimated: false,
  total_units: 25000,
  good_units: 24200,
  row_count: 500,
  invalid_cells: 0,
  blank_cells: 0,
  scrap_rate_pct: 3.2,
  fpq_pct: 95.1,
  defect_rate_per_1000: 41,
  trend: [
    { date: "2026-01-01", oee: 77, total_units: 12000, scrap_rate_pct: 3 },
    { date: "2026-01-02", oee: 79.8, total_units: 13000, scrap_rate_pct: 3.4 },
  ],
  machines: [
    { machine: "Line 1", oee: 82, availability: 93, performance: 90, quality: 98, total_units: 15000, good_units: 14700 },
    { machine: "Line 2", oee: 73, availability: 88, performance: 87, quality: 95, total_units: 10000, good_units: 9500 },
  ],
};

function control(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLElement;
}
const card = (title: string) => screen.getByRole("heading", { name: title }).parentElement!.parentElement as HTMLElement;
// StatCard: label <span> inside a header row inside the card.
const stat = (label: string) => screen.getByText(label, { selector: "span" }).parentElement!.parentElement!.textContent;

async function compute(result: unknown) {
  const api = mockApi({ "/datasets": [DS], "/datasets/ds1": { preview: [] }, "POST /analytics/plant-summary": result });
  renderPage(<PlantSummary />);
  await screen.findByRole("option", { name: "Plant (500 rows)" });
  await userEvent.selectOptions(control("Dataset"), "ds1");
  await userEvent.click(screen.getByRole("button", { name: "Compute Summary" }));
  await screen.findByText("Availability", { selector: "span" });
  return api;
}

describe("PlantSummary", () => {
  it("posts the auto-mapped plant columns", async () => {
    const api = await compute(RESULT);
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      dataset_id: "ds1",
      planned_time_col: "planned_time",
      operating_time_col: "operating_time",
      total_units_col: "total_units",
      good_units_col: "good_units",
      machine_col: "line",
      date_col: "date",
      scrap_units_col: "scrap",
      defects_col: "defects",
      target_rate: null,
    });
  });

  it("rolls up OEE, quality KPIs, and a per-line table", async () => {
    await compute(RESULT);
    expect(stat("OEE")).toContain("78.4%");
    expect(stat("Availability")).toContain("91%");
    expect(stat("Total units")).toContain("25,000");
    expect(stat("Scrap rate")).toContain("3.2%");
    expect(stat("First Pass Quality")).toContain("95.1%");
    expect(stat("Defects / 1000")).toContain("41");
    expect(screen.getByRole("heading", { name: "Trend" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();

    const rows = within(card("By machine / line")).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["Line 1", "82%", "93%", "90%", "98%", "14,700 / 15,000"],
      ["Line 2", "73%", "88%", "87%", "95%", "9,500 / 10,000"],
    ]);
  });

  it("warns on estimated performance and bad cells, and hides KPIs the backend couldn't compute", async () => {
    await compute({
      ...RESULT,
      performance_estimated: true,
      invalid_cells: 2,
      blank_cells: 5,
      scrap_rate_pct: null,
      fpq_pct: null,
      defect_rate_per_1000: null,
      trend: [{ ...RESULT.trend[0], scrap_rate_pct: null }, { ...RESULT.trend[1], scrap_rate_pct: null }],
      machines: [],
    });
    expect(screen.getByText("No target rate set — Performance assumed 100%; OEE shown is an upper bound.")).toBeTruthy();
    expect(screen.getByText(/2 non-numeric\/negative value\(s\) were treated as 0\. 5 blank cell\(s\)/)).toBeTruthy();
    expect(stat("OEE")).toContain("78.4%*");
    expect(screen.queryByText("Scrap rate", { selector: "span" })).toBeNull();
    expect(screen.queryByText("First Pass Quality", { selector: "span" })).toBeNull();
    expect(screen.queryByText("Defects / 1000", { selector: "span" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "By machine / line" })).toBeNull();
  });

  it("skips the trend chart with a single period", async () => {
    await compute({ ...RESULT, trend: [RESULT.trend[0]] });
    expect(screen.queryByRole("heading", { name: "Trend" })).toBeNull();
  });
});
