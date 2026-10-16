// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Oee } from "./Oee";
import type { OeeResult } from "./oee/types";

// echarts paints to <canvas>, which jsdom lacks.
vi.mock("echarts", () => ({ init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }) }));

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});

const COLS = ["planned_time", "operating_time", "total_units", "good_units", "machine", "shift_date"];
const DS = {
  id: "ds1",
  name: "Press shop",
  original_filename: "press.csv",
  fmt: "csv",
  row_count: 240,
  size_bytes: 4096,
  created_at: "2026-01-01T00:00:00Z",
  columns: COLS.map((name) => ({ name, dtype: "VARCHAR" })),
};

function control(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLElement;
}

const BASE: OeeResult = {
  oee: 72.5,
  availability: 90,
  performance: 85,
  quality: 94.8,
  performance_estimated: false,
  total_planned: 4800,
  total_operating: 4320,
  total_units: 12000,
  good_units: 11376,
  row_count: 240,
  invalid_cells: 0,
  blank_cells: 0,
  rows_with_issues: 0,
  availability_clamped: false,
  quality_clamped: false,
  date_gap_days: 0,
  duplicate_rows: 0,
  trend: null,
  machines: null,
  revenue_impact: null,
  teep: null,
  six_big_losses: null,
};

const FULL: OeeResult = {
  ...BASE,
  invalid_cells: 3,
  blank_cells: 2,
  rows_with_issues: 2,
  availability_clamped: true,
  quality_clamped: true,
  duplicate_rows: 4,
  date_gap_days: 5,
  trend: Array.from({ length: 61 }, (_, i) => ({
    date: `2026-01-${String((i % 28) + 1).padStart(2, "0")}`,
    oee: 70 + (i % 5),
    availability: 90,
    performance: 85,
    quality: 95,
  })),
  machines: [
    { machine: "Press 1", oee: 61.2, availability: 80, performance: 85, quality: 90, total_units: 5000, good_units: 4500, invalid_cells: 0 },
    { machine: "Press 2", oee: 88.4, availability: 95, performance: 96, quality: 97, total_units: 7000, good_units: 6876, invalid_cells: 3 },
  ],
  revenue_impact: {
    total_lost: 20000,
    availability_lost: 10000,
    performance_lost: 6000,
    quality_lost: 4000,
    daily_rate: 666.6,
    monthly_rate: 20000,
    benchmark_saving: 7500,
    period_days: 30,
    period_days_estimated: false,
    performance_estimated: false,
    per_machine: [
      { machine: "Press 1", total_lost: 15000 },
      { machine: "Press 2", total_lost: 5000 },
    ],
  },
  teep: { teep: 41.3, utilization: 57, calendar_days: 30, machine_count: 2 },
  six_big_losses: {
    total_loss_minutes: 480,
    buckets: [
      { key: "breakdowns", label: "Breakdowns", family: "availability", minutes: 300, units: null, cost: 9000, pct_of_loss: 62.5 },
      { key: "startup", label: "Startup rejects", family: "quality", minutes: 180, units: 120, cost: null, pct_of_loss: 37.5 },
    ],
  },
};

async function compute(result: OeeResult, targetRate?: string) {
  const api = mockApi({ "/datasets": [DS], "/datasets/ds1": { preview: [] }, "POST /analytics/oee": result });
  renderPage(<Oee />);
  await screen.findByRole("option", { name: "Press shop (240 rows)" });
  await userEvent.selectOptions(control("Dataset"), "ds1");
  if (targetRate) await userEvent.type(control("Target rate (units/min)"), targetRate);
  await userEvent.click(screen.getByRole("button", { name: "Compute OEE" }));
  await screen.findByText("Summary");
  return api;
}

// CardTitle is an h3 inside CardHeader inside Card.
const card = (title: string) => screen.getByRole("heading", { name: title }).parentElement!.parentElement as HTMLElement;

describe("Oee", () => {
  it("posts the auto-mapped columns and target rate", async () => {
    const api = await compute(BASE, "4");
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      dataset_id: "ds1",
      planned_time_col: "planned_time",
      operating_time_col: "operating_time",
      total_units_col: "total_units",
      good_units_col: "good_units",
      machine_col: "machine",
      date_col: "shift_date",
      target_rate: 4,
      unit_price: null,
    });
  });

  it("shows headline metrics, level badge, and the summary totals", async () => {
    await compute(BASE);
    expect(screen.getByText("72.5%")).toBeTruthy();
    expect(screen.getByText("Good")).toBeTruthy();
    expect(screen.getAllByText("90%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("94.8%").length).toBeGreaterThan(0);

    const summary = card("Summary");
    const row = (label: string) => within(summary).getByText(label).nextElementSibling!.textContent;
    expect(row("Total planned time")).toBe("4,800");
    expect(row("Good units")).toBe("11,376");
    expect(row("Rows processed")).toBe("240");
    // No TEEP, trend, machines, revenue, or losses when the backend omits them.
    expect(within(summary).queryByText("TEEP")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Trend" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "By machine" })).toBeNull();
    expect(screen.queryByText("Revenue at risk")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Six Big Losses" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("flags an estimated performance with a warning and an asterisk, and grades low OEE as Poor", async () => {
    await compute({ ...BASE, oee: 30, performance_estimated: true });
    expect(screen.getByText("No target rate set — Performance assumed 100%; OEE shown is an upper bound.")).toBeTruthy();
    expect(screen.getByText("30.0%*")).toBeTruthy();
    expect(screen.getByText("Poor")).toBeTruthy();
  });

  it("surfaces every data-quality warning", async () => {
    await compute(FULL);
    expect(screen.getByText(/Operating time exceeds Planned time/)).toBeTruthy();
    expect(screen.getByText(/Good units exceed Total units/)).toBeTruthy();
    expect(screen.getByText(/3 non-numeric\/negative value\(s\) in 2 row\(s\) were treated as 0/)).toBeTruthy();
    expect(screen.getByText(/2 blank cell\(s\) were also treated as 0/)).toBeTruthy();
    expect(screen.getByText(/Looks like 4 row\(s\) were uploaded twice/)).toBeTruthy();
    expect(screen.getByText(/5 calendar day\(s\) in this date range have no rows at all/)).toBeTruthy();
  });

  it("lists machines best-first and marks ones with invalid cells", async () => {
    await compute(FULL);
    const table = card("By machine").querySelector("table")!;
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual(["Press 2🛈", "Press 1"]);
    expect(within(rows[0]).getByTitle("3 invalid cell(s) treated as 0 — OEE may be understated")).toBeTruthy();
    expect(within(rows[0]).getByText("6,876 / 7,000")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Trend" })).toBeTruthy();
  });

  it("shows TEEP and utilization in the summary when present", async () => {
    await compute(FULL);
    const summary = card("Summary");
    expect(within(summary).getByText("TEEP").nextElementSibling!.textContent).toBe("41.3%");
    expect(within(summary).getByText("Utilization").nextElementSibling!.textContent).toBe("57%");
  });

  it("breaks revenue at risk into pillars and per-machine losses", async () => {
    await compute(FULL);
    expect(screen.getByText("Revenue at risk")).toBeTruthy();
    expect(screen.getByText("$20,000")).toBeTruthy();
    expect(screen.getByText(/lost over 30 days · \$667\/day · \$20,000\/mo projected/)).toBeTruthy();
    expect(screen.getByText("$7,500")).toBeTruthy();
    expect(screen.getByText("50% of total · machines idle")).toBeTruthy();
    expect(screen.getByText("30% of total · running slow")).toBeTruthy();
    expect(screen.getByText("20% of total · scrap & rework")).toBeTruthy();
    // The worst machine is first and badged.
    expect(screen.getByText("worst").parentElement!.textContent).toBe("Press 1worst");
    expect(screen.getByText("$15,000")).toBeTruthy();
  });

  it("explains estimated revenue figures and hides the benchmark when there's nothing to save", async () => {
    await compute({
      ...BASE,
      revenue_impact: {
        ...FULL.revenue_impact!,
        total_lost: 0,
        availability_lost: 0,
        performance_lost: 0,
        quality_lost: 0,
        benchmark_saving: 0,
        period_days: 2.4,
        period_days_estimated: true,
        performance_estimated: true,
        per_machine: [],
      },
    });
    expect(screen.getByText(/lost over 2\* days/)).toBeTruthy();
    expect(screen.getByText(/excludes speed losses and understates the true total/)).toBeTruthy();
    expect(screen.getByText(/No date column mapped — period length guessed/)).toBeTruthy();
    expect(screen.queryByText("Reach 85% OEE target")).toBeNull();
    expect(screen.queryByText("Loss by machine")).toBeNull();
    expect(screen.getByText("0% of total · machines idle")).toBeTruthy();
  });

  it("tabulates the six big losses with dashes for missing units or cost", async () => {
    await compute(FULL);
    const table = card("Six Big Losses").querySelector("table")!;
    const [breakdowns, startup] = within(table).getAllByRole("row").slice(1);
    expect(within(breakdowns).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Breakdowns",
      "availability",
      "300",
      "—",
      "$9,000",
      "62.5%",
    ]);
    expect(within(startup).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Startup rejects",
      "quality",
      "180",
      "120",
      "—",
      "37.5%",
    ]);
  });
});
