// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Throughput } from "./Throughput";

// echarts paints to <canvas>, which jsdom lacks; record setOption so a test
// can check what the machine chart was asked to draw.
const setOption = vi.fn();
vi.mock("echarts", () => ({ init: () => ({ setOption, resize: vi.fn(), dispose: vi.fn() }) }));

beforeEach(() => {
  setOption.mockClear();
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});

const DS = {
  id: "ds1",
  name: "Output",
  original_filename: "o.csv",
  fmt: "csv",
  row_count: 30,
  size_bytes: 1024,
  created_at: "2026-01-01T00:00:00Z",
  columns: ["units_produced", "planned_units", "machine", "date"].map((name) => ({ name, dtype: "VARCHAR" })),
};

const RESULT = {
  total_units: 15400,
  period_count: 2,
  avg_units_per_period: 7700,
  peak_units: 8200,
  peak_period: "2026-01-02",
  efficiency_pct: 96,
  row_count: 30,
  invalid_cells: 0,
  rows_with_issues: 0,
  unparsed_date_rows: 0,
  is_target_estimated: false,
  trend: [
    { period: "2026-01-01", units: 7200, planned: 8000 },
    { period: "2026-01-02", units: 8200, planned: 8000 },
  ],
  by_machine: [
    { machine: "Line A", total_units: 10000, pct_of_total: 64.9 },
    { machine: "Line B", total_units: 5400, pct_of_total: 35.1 },
  ],
};

function control(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLElement;
}

// StatCard: label <span> inside a header row inside the card.
const stat = (label: string) => screen.getByText(label, { selector: "span" }).parentElement!.parentElement!.textContent;

async function analyze(result: unknown) {
  const api = mockApi({ "/datasets": [DS], "/datasets/ds1": { preview: [] }, "POST /analytics/throughput": result });
  renderPage(<Throughput />);
  await screen.findByRole("option", { name: "Output (30 rows)" });
  await userEvent.selectOptions(control("Dataset"), "ds1");
  await userEvent.click(screen.getByRole("button", { name: "Analyze throughput" }));
  await screen.findByText("Total units");
  return api;
}

describe("Throughput", () => {
  it("posts the auto-mapped columns and shows output KPIs against plan", async () => {
    const api = await analyze(RESULT);
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      dataset_id: "ds1",
      units_col: "units_produced",
      planned_units_col: "planned_units",
      machine_col: "machine",
      date_col: "date",
    });
    expect(screen.getByText("15,400")).toBeTruthy();
    expect(screen.getByText("7,700")).toBeTruthy();
    expect(screen.getByText("2 periods")).toBeTruthy();
    expect(screen.getByText("8,200")).toBeTruthy();
    expect(screen.getByText("2026-01-02")).toBeTruthy();
    expect(screen.getByText("Pace vs. plan")).toBeTruthy();
    expect(screen.getByText("96% of plan")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Output per period" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("draws machines top-down (reversed) with their share of total", async () => {
    await analyze(RESULT);
    expect(screen.getByRole("heading", { name: "By machine" })).toBeTruthy();
    const opts = setOption.mock.calls.map(([o]) => o);
    const bar = opts.find((o) => JSON.stringify(o.yAxis ?? o.xAxis ?? {}).includes("Line A"));
    expect(bar).toBeTruthy();
    expect(JSON.stringify(bar).indexOf("Line B")).toBeLessThan(JSON.stringify(bar).indexOf("Line A"));
  });

  it("labels an estimated target and warns about bad cells and unparsed dates", async () => {
    await analyze({
      ...RESULT,
      is_target_estimated: true,
      invalid_cells: 4,
      rows_with_issues: 3,
      unparsed_date_rows: 2,
    });
    expect(screen.getByText("Pace vs. estimated target")).toBeTruthy();
    expect(screen.getByText("96% of historical average")).toBeTruthy();
    const alert = screen.getByRole("alert").textContent!;
    expect(alert).toContain("4 non-numeric/negative value(s) in 3 row(s) were treated as 0");
    expect(alert).toContain("2 row(s) had a date that couldn't be parsed");
  });

  it("shows a dash for pace and skips charts when there's no plan, trend, or machines", async () => {
    await analyze({
      ...RESULT,
      efficiency_pct: null,
      peak_period: null,
      trend: [{ period: "2026-01-01", units: 7200, planned: null }],
      by_machine: [],
    });
    expect(stat("Pace vs. plan")).toBe("Pace vs. plan—");
    expect(screen.queryByText(/of plan/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Output per period" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "By machine" })).toBeNull();
  });
});
