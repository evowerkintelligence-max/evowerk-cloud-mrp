// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Scrap } from "./Scrap";

// echarts paints to <canvas>, which jsdom lacks.
vi.mock("echarts", () => ({ init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }) }));

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});

const DS = {
  id: "ds1",
  name: "Quality log",
  original_filename: "q.csv",
  fmt: "csv",
  row_count: 90,
  size_bytes: 1024,
  created_at: "2026-01-01T00:00:00Z",
  columns: ["total_units", "scrap_qty", "defect_reason", "machine", "date"].map((name) => ({ name, dtype: "VARCHAR" })),
};

const RESULT = {
  total_units: 10000,
  total_scrap: 320,
  scrap_rate_pct: 3.2,
  fpq_pct: 96.8,
  defect_rate_per_1000: 32,
  by_reason: [
    { reason: "Burr", count: 200, pct_of_total: 62.5, cumulative_pct: 62.5 },
    { reason: "Crack", count: 120, pct_of_total: 37.5, cumulative_pct: 100 },
  ],
  by_machine: [
    { machine: "Lathe 1", total_units: 6000, scrap_units: 30, scrap_rate_pct: 0.5 },
    { machine: "Lathe 2", total_units: 4000, scrap_units: 290, scrap_rate_pct: 7.25 },
  ],
  trend: [
    { period: "2026-01-01", total_units: 5000, scrap_units: 100, scrap_rate_pct: 2 },
    { period: "2026-01-02", total_units: 5000, scrap_units: 220, scrap_rate_pct: 4.4 },
  ],
  warnings: ["12 rows had scrap greater than total units and were capped."],
};

function control(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLElement;
}
const card = (title: string) => screen.getByRole("heading", { name: title }).parentElement!.parentElement as HTMLElement;

async function analyze(result: unknown) {
  const api = mockApi({ "/datasets": [DS], "/datasets/ds1": { preview: [] }, "POST /analytics/scrap": result });
  renderPage(<Scrap />);
  await screen.findByRole("option", { name: "Quality log (90 rows)" });
  await userEvent.selectOptions(control("Dataset"), "ds1");
  await userEvent.click(screen.getByRole("button", { name: "Analyze scrap" }));
  await screen.findByText("Defects / 1000");
  return api;
}

describe("Scrap", () => {
  it("posts the auto-mapped scrap columns", async () => {
    const api = await analyze(RESULT);
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      dataset_id: "ds1",
      total_units_col: "total_units",
      scrap_col: "scrap_qty",
      reason_col: "defect_reason",
      machine_col: "machine",
      date_col: "date",
    });
  });

  it("shows the KPIs, backend warnings, and the per-machine scrap table", async () => {
    await analyze(RESULT);
    expect(screen.getByRole("alert").textContent).toBe("12 rows had scrap greater than total units and were capped.");
    expect(screen.getByText("10,000")).toBeTruthy();
    expect(screen.getByText("320")).toBeTruthy();
    expect(screen.getByText("3.2%")).toBeTruthy();
    expect(screen.getByText("96.8%")).toBeTruthy();
    expect(screen.getByText("32")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Pareto by reason" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Scrap rate trend" })).toBeTruthy();

    const rows = within(card("By machine")).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["Lathe 1", "6,000", "30", "0.5%"],
      ["Lathe 2", "4,000", "290", "7.25%"],
    ]);
  });

  it("omits the Pareto, trend, machine table, and warnings when the result has none", async () => {
    await analyze({ ...RESULT, by_reason: [], by_machine: [], trend: [RESULT.trend[0]], warnings: undefined });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Pareto by reason" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Scrap rate trend" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "By machine" })).toBeNull();
  });
});
