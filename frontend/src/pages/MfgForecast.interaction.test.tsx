// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { MfgForecast } from "./MfgForecast";

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const DATASETS = [
  {
    id: "d1", name: "Output log", original_filename: "o.csv", fmt: "csv", row_count: 900, size_bytes: 1, created_at: "",
    columns: [{ name: "day", dtype: "date" }, { name: "units", dtype: "int" }, { name: "machine", dtype: "str" }],
  },
];

const RESULT = {
  target_col: "units",
  horizon: 2,
  mape_pct: 8.2,
  r2: 0.91,
  n_training_points: 120,
  series: [
    { date: "2026-10-14", actual: 100, predicted: 98, lower: 90, upper: 106 },
    { date: "2026-10-15", actual: null, predicted: 1050, lower: 900, upper: 1200 },
    { date: "2026-10-16", actual: null, predicted: 1100, lower: 880, upper: 1320 },
  ],
  prescriptive: [{ rank: 1, label: "Day of week", detail: "Mondays run 12% lower", importance_pct: 34, direction: "down", icon: "📅" }],
  summary: "Output is expected to rise slightly.",
  machines: [],
};

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLSelectElement;
}

async function configure() {
  await screen.findByRole("option", { name: "Output log (900 rows)" });
  await userEvent.selectOptions(field("Dataset"), "d1");
  await userEvent.selectOptions(field("Date column"), "day");
}

describe("MfgForecast", () => {
  it("enables Run only once dataset, date and target are chosen, then shows results", async () => {
    const api = mockApi({ "/datasets": DATASETS, "POST /datasets/d1/mfg-forecast/run": RESULT });
    renderPage(<MfgForecast />);
    expect(screen.queryByRole("button", { name: "Run forecast" })).toBeNull();
    await configure();
    const run = screen.getByRole("button", { name: "Run forecast" }) as HTMLButtonElement;
    expect(run.disabled).toBe(true);
    await userEvent.selectOptions(field("Target column"), "units");
    await userEvent.selectOptions(field("Machine column (optional)"), "machine");
    await userEvent.clear(field("Horizon (days)"));
    await userEvent.type(field("Horizon (days)"), "2");
    await userEvent.selectOptions(field("Daily aggregate"), "sum");
    await userEvent.click(run);

    expect(await screen.findByText("Output is expected to rise slightly.")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      date_col: "day", target_col: "units", machine_col: "machine", horizon: 2, aggregate: "sum",
    });
    expect(screen.getByText("8.2%")).toBeTruthy();
    expect(screen.getByText("Next 2 days")).toBeTruthy();
    // only future points are listed; history is summarized
    expect(screen.getByText("2026-10-15")).toBeTruthy();
    expect(screen.queryByText("2026-10-14")).toBeNull();
    expect(screen.getByText("1 historical days shown in the model, not displayed here.")).toBeTruthy();
    expect(screen.getByText(/Mondays run 12% lower/)).toBeTruthy();
  });

  it("omits the machine column and defaults the horizon, and shows API errors", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "POST /datasets/d1/mfg-forecast/run": new Response(JSON.stringify({ detail: "Not enough history" }), { status: 422 }),
    });
    renderPage(<MfgForecast />);
    await configure();
    await userEvent.selectOptions(field("Target column"), "units");
    await userEvent.clear(field("Horizon (days)"));
    await userEvent.click(screen.getByRole("button", { name: "Run forecast" }));

    expect(await screen.findByText("Not enough history")).toBeTruthy();
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ date_col: "day", target_col: "units", horizon: 14, aggregate: "mean" });
  });
});
