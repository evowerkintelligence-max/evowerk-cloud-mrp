// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Shifts } from "./Shifts";
import type { ShiftRow, ShiftsResult } from "./shifts/types";

// echarts paints to <canvas>, which jsdom lacks.
vi.mock("echarts", () => ({ init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }) }));

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});

const DS = {
  id: "ds1",
  name: "Shift log",
  original_filename: "s.csv",
  fmt: "csv",
  row_count: 300,
  size_bytes: 1024,
  created_at: "2026-01-01T00:00:00Z",
  columns: ["shift", "units_produced", "machine", "timestamp"].map((name) => ({ name, dtype: "VARCHAR" })),
};

const row = (shift: string, patch: Partial<ShiftRow>): ShiftRow => ({
  shift,
  row_count: 150,
  total_units: null,
  total_scrap: null,
  scrap_rate_pct: null,
  fpq_pct: null,
  availability_pct: null,
  quality_pct: null,
  oee_pct: null,
  total_downtime_min: null,
  downtime_by_category: [],
  ...patch,
});

const RESULT: ShiftsResult = {
  shifts: [
    row("Day", {
      oee_pct: 80,
      total_units: 6000,
      total_downtime_min: 45,
      downtime_by_category: [{ category: "Breakdown", total_minutes: 45, pct: 100 }],
    }),
    row("Night", { oee_pct: 60, total_units: 4000, total_downtime_min: 90 }),
  ],
  best_shift: "Day",
  worst_shift: "Night",
  metrics_available: ["oee", "units", "downtime"],
  row_count: 300,
  invalid_cells: 0,
  rows_with_issues: 0,
  unparsed_date_rows: 0,
  by_machine: [
    { shift: "Day", machine: "Press 1", total_units: 6000, total_scrap: null, scrap_rate_pct: null, fpq_pct: null, availability_pct: null, quality_pct: null, oee_pct: 80, total_downtime_min: 45 },
    { shift: "Night", machine: "Press 1", total_units: 4000, total_scrap: null, scrap_rate_pct: null, fpq_pct: null, availability_pct: null, quality_pct: null, oee_pct: 60, total_downtime_min: 90 },
  ],
  trend: [
    { period: "2026-01-05", shift: "Day", value: 78 },
    { period: "2026-01-06", shift: "Day", value: 82 },
    { period: "2026-01-05", shift: "Night", value: 65 },
    { period: "2026-01-06", shift: "Night", value: 60 },
    { period: "2026-01-06", shift: "Night", value: null },
  ],
  hourly: [
    { shift: "Day", hour: "2026-01-05T08:00:00", units: 999 },
    { shift: "Day", hour: "2026-01-06T09:00:00", units: 130 },
    { shift: "Day", hour: "2026-01-06T08:00:00", units: 100 },
    { shift: "Day", hour: "2026-01-06T10:00:00", units: 100 },
    { shift: "Night", hour: "2026-01-06T22:00:00", units: 90 },
  ],
  target_rate: 2,
};

const SOURCE = { dataset_id: "fc1", target_column: "oee", time_column: "date", metric_label: "OEE %", rows: 10 };

function control(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLElement;
}
const card = (title: string) => screen.getByRole("heading", { name: title }).parentElement!.parentElement as HTMLElement;
const headerSelect = (title: string) => card(title).querySelector("select") as HTMLSelectElement;

async function compare(result: ShiftsResult, extra: Record<string, unknown> = {}) {
  const api = mockApi({
    "/datasets": [DS],
    "/datasets/ds1": { preview: [] },
    "/analytics/shifts/schedule": [],
    "/automl/runs": [],
    "POST /analytics/shifts/forecast-source": SOURCE,
    "POST /analytics/shifts": result,
    ...extra,
  });
  renderPage(<Shifts />);
  await screen.findByRole("option", { name: "Shift log (300 rows)" });
  await userEvent.selectOptions(control("Dataset"), "ds1");
  await userEvent.click(screen.getByRole("button", { name: "Compare shifts" }));
  await screen.findByRole("heading", { name: "All shifts" });
  return api;
}

describe("Shifts", () => {
  it("posts the mapped columns and ranks shifts with only the metrics that have data", async () => {
    const api = await compare(RESULT);
    expect(api.calls.find((c) => c.path === "/analytics/shifts")!.body).toEqual({
      dataset_id: "ds1",
      shift_col: "shift",
      units_col: "units_produced",
      total_units_col: "units_produced",
      machine_col: "machine",
      timestamp_col: "timestamp",
      target_rate: null,
    });

    expect(screen.getByText("Best shift", { selector: "span" }).parentElement!.parentElement!.textContent).toBe("Best shiftDay");
    expect(screen.getByText("Worst shift", { selector: "span" }).parentElement!.parentElement!.textContent).toBe("Worst shiftNight");
    expect(screen.getByRole("heading", { name: "OEE by shift" })).toBeTruthy();
    expect(screen.queryByText("Lower is better for downtime.")).toBeNull();

    const table = card("All shifts").querySelector("table")!;
    const header = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(header).toEqual(["Shift", "Rows", "OEE", "Units", "Downtime (min)"]);
    const [day, night] = within(table).getAllByRole("row").slice(1);
    expect(within(day).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Daybest▲ 4 vs last", "150", "80%", "6,000", "45"]);
    expect(within(night).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Nightworst▼ 5 vs last", "150", "60%", "4,000", "90"]);
    expect(screen.getByText("Metrics available: oee, units, downtime")).toBeTruthy();

    expect(screen.getByRole("heading", { name: "Shift trend" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Average by day of week" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "By machine" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("warns about bad cells and unparsed timestamps", async () => {
    await compare({ ...RESULT, invalid_cells: 7, rows_with_issues: 4, unparsed_date_rows: 3 });
    const alert = screen.getByRole("alert").textContent!;
    expect(alert).toContain("7 non-numeric/negative value(s) in 4 row(s) were treated as 0");
    expect(alert).toContain("3 row(s) had a timestamp that couldn't be parsed");
  });

  it("ranks on downtime when it's the only metric, noting lower is better", async () => {
    await compare({
      ...RESULT,
      shifts: [row("Day", { total_downtime_min: 30 }), row("Night", { total_downtime_min: 90 })],
      by_machine: [],
      trend: [
        { period: "2026-01-05", shift: "Day", value: 40 },
        { period: "2026-01-06", shift: "Day", value: 30 },
      ],
      hourly: [],
    });
    expect(screen.getByRole("heading", { name: "Downtime (min) by shift" })).toBeTruthy();
    expect(screen.getByText("Lower is better for downtime.")).toBeTruthy();
    // A drop in downtime is shown as a fall.
    expect(screen.getByText("▼ 10 vs last")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "By machine" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Handover — loss Pareto" })).toBeNull();
  });

  it("resolves the live shift from the schedule for the handover and hourly pace views", async () => {
    // A 00:00-00:00 window wraps the whole day, so "now" is always Day.
    await compare(RESULT, { "/analytics/shifts/schedule": [{ shift_label: "Day", start_time: "00:00", end_time: "00:00" }] });
    expect(await screen.findByText("Current shift, resolved from the schedule below.")).toBeTruthy();
    expect(headerSelect("Handover — loss Pareto").value).toBe("Day");
    // Latest day only; the last hour (100 units) is 20 short of 2/min * 60.
    expect(screen.getByRole("heading", { name: "Hourly pace — Day" })).toBeTruthy();
    expect(screen.getByText(/^20 units behind pace this hour, \d+ min left in shift\.$/)).toBeTruthy();

    // Picking another shift by hand is no longer "live".
    await userEvent.selectOptions(headerSelect("Handover — loss Pareto"), "Night");
    expect(screen.queryByText("Current shift, resolved from the schedule below.")).toBeNull();
    expect(screen.getByText("No downtime-by-category data for Night.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Hourly pace — Night" })).toBeTruthy();
    expect(screen.getByText("Set a shift schedule below to see minutes-left-in-shift.")).toBeTruthy();
  });

  it("says on pace when the last hour met target", async () => {
    await compare({ ...RESULT, hourly: [{ shift: "Day", hour: "2026-01-06T08:00:00", units: 150 }] }, {
      "/analytics/shifts/schedule": [{ shift_label: "Day", start_time: "00:00", end_time: "00:00" }],
    });
    expect(await screen.findByText(/^On pace this hour, \d+ min left in shift\.$/)).toBeTruthy();
  });

  it("switches the by-machine view between shifts", async () => {
    await compare(RESULT);
    const select = headerSelect("By machine");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["Day", "Night"]);
    expect(select.value).toBe("Day");
    await userEvent.selectOptions(select, "Night");
    expect(headerSelect("By machine").value).toBe("Night");
  });

  it("explains missing machine data when no metric can rank", async () => {
    await compare({
      ...RESULT,
      shifts: [row("Day", {}), row("Night", {})],
      best_shift: null,
      worst_shift: null,
      metrics_available: [],
      // A single period draws no trend line.
      trend: [{ period: "not-a-date", shift: "Day", value: 5 }],
      hourly: [],
    });
    expect(screen.getByText("No machine data for Day.")).toBeTruthy();
    expect(screen.getByText("Best shift", { selector: "span" }).parentElement!.parentElement!.textContent).toBe("Best shift—");
    expect(screen.queryByText(/Metrics available/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Shift trend" })).toBeNull();
    // An unparseable period can't be bucketed by weekday.
    expect(screen.queryByRole("heading", { name: "Average by day of week" })).toBeNull();
  });

  describe("schedule editor", () => {
    const timeInputs = (label: string) => {
      const line = screen.getByText(label, { selector: "div.w-24" }).parentElement!;
      return line.querySelectorAll("input") as NodeListOf<HTMLInputElement>;
    };

    it("prefills saved times and saves only shifts with both start and end", async () => {
      const api = await compare(RESULT, {
        "/analytics/shifts/schedule": [{ shift_label: "Day", start_time: "06:00", end_time: "18:00" }],
        "PUT /analytics/shifts/schedule": [],
      });
      await waitFor(() => expect(timeInputs("Day")[0].value).toBe("06:00"));
      expect(timeInputs("Day")[1].value).toBe("18:00");
      expect(timeInputs("Night")[0].value).toBe("");

      // Night gets a start only, so it's left out; Day's end is edited.
      await userEvent.type(timeInputs("Night")[0], "18:00");
      await userEvent.clear(timeInputs("Day")[1]);
      await userEvent.type(timeInputs("Day")[1], "17:30");
      await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));

      await waitFor(() => expect(api.calls.some((c) => c.method === "PUT")).toBe(true));
      expect(api.calls.find((c) => c.method === "PUT")!.body).toEqual([
        { shift_label: "Day", start_time: "06:00", end_time: "17:30" },
      ]);
    });

    it("shows the server error when saving fails", async () => {
      await compare(RESULT, {
        "PUT /analytics/shifts/schedule": new Response(JSON.stringify({ detail: "Overlapping shifts" }), { status: 422 }),
      });
      await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
      expect(await screen.findByText("Overlapping shifts")).toBeTruthy();
    });
  });

  describe("forecast card", () => {
    it("prepares forecast data for the handover shift and flags too-short history", async () => {
      const api = await compare(RESULT);
      expect(await screen.findByText(/OEE % · 10 day\(s\) of history — forecasting needs at least 15 days/)).toBeTruthy();
      expect(screen.getByRole("heading", { name: "Forecast — Day" })).toBeTruthy();
      const src = api.calls.find((c) => c.path === "/analytics/shifts/forecast-source")!;
      expect(src.body).toMatchObject({ dataset_id: "ds1", shift_col: "shift", shift: "Day" });
    });

    it("trains a recursive 14-step forecast and draws it once the run succeeds", async () => {
      const api = await compare(
        { ...RESULT, hourly: [] },
        {
          "POST /analytics/shifts/forecast-source": { ...SOURCE, rows: 30 },
          "POST /automl/runs": { id: "run9", status: "queued" },
          "/automl/runs/run9": { id: "run9", status: "success", error: null },
          "/automl/runs/run9/forecast-trajectory": {
            target_column: "oee",
            time_column: "date",
            steps: 2,
            points: [{ step: 1, time: "2026-01-07", prediction: 81 }],
            history: [{ time: "2026-01-06", actual: 82 }],
            interval: null,
            notes: ["Trained on 30 days."],
          },
        },
      );
      const train = await screen.findByRole("button", { name: "Train forecast" });
      expect(screen.queryByText(/forecasting needs at least/)).toBeNull();
      await userEvent.click(train);

      expect(await screen.findByText("Trained on 30 days.")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Retrain forecast" })).toBeTruthy();
      expect(api.calls.find((c) => c.method === "POST" && c.path === "/automl/runs")!.body).toEqual({
        name: "Shift forecast: Day",
        dataset_id: "fc1",
        task: "forecasting",
        target_column: "oee",
        feature_columns: [],
        thoroughness: "fast",
        algorithms: null,
        handle_class_imbalance: true,
        time_column: "date",
        horizon: 14,
        forecast_strategy: "recursive",
        time_budget_seconds: null,
        search_method: "random",
        auto_retrain: false,
      });
    });

    it("picks up the newest existing forecasting run for the dataset and shows its failure", async () => {
      await compare(RESULT, {
        "/automl/runs": [
          { id: "old", dataset_id: "fc1", task: "forecasting", created_at: "2026-01-01T00:00:00Z", status: "success" },
          { id: "new", dataset_id: "fc1", task: "forecasting", created_at: "2026-01-03T00:00:00Z", status: "failed" },
          { id: "other", dataset_id: "zzz", task: "forecasting", created_at: "2026-01-09T00:00:00Z", status: "success" },
        ],
        "/automl/runs/new": { id: "new", status: "failed", error: "Not enough rows after lagging" },
      });
      expect(await screen.findByText("Not enough rows after lagging")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Retrain forecast" })).toBeTruthy();
    });

    it("shows the error when forecast data can't be prepared", async () => {
      await compare(RESULT, {
        "POST /analytics/shifts/forecast-source": new Response(JSON.stringify({ detail: "No timestamp column mapped" }), { status: 400 }),
      });
      expect(await screen.findByText("No timestamp column mapped")).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Train forecast" })).toBeNull();
    });
  });
});
