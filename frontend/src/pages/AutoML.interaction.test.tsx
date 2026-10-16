// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { AutoML } from "./AutoML";

const summary = (over: Record<string, unknown>) => ({
  id: "r1",
  name: "Late shipments",
  task: "classification",
  status: "success",
  dataset_id: "d1",
  target_column: "late",
  best_model_name: "random_forest",
  metric_name: "f1",
  metric_value: 0.87654,
  error: null,
  created_at: "2026-10-01T10:00:00Z",
  started_at: null,
  finished_at: null,
  auto_retrain: false,
  retrained_from_run_id: null,
  ...over,
});

const detail = (over: Record<string, unknown>) => ({
  ...summary({}),
  feature_columns: ["qty"],
  config: {
    thoroughness: "balanced",
    algorithms: null,
    handle_class_imbalance: true,
    time_column: null,
    horizon: 7,
    forecast_strategy: "direct",
    time_budget_seconds: 60,
    search_method: "bayesian",
  },
  leaderboard: [{}, {}],
  meta: {
    scores: { f1: 0.8, accuracy: 0.9 },
    rows_used: 1200,
    class_balance: { imbalance_ratio: 4, imbalance_detected: true, handling_applied: true },
  },
  ...over,
});

const RUNS = [
  summary({ auto_retrain: true }),
  summary({ id: "r2", name: "Scrap rate", task: "regression", best_model_name: "xgboost", metric_name: "rmse", metric_value: 1.5 }),
  summary({ id: "r3", name: "Pending run", status: "running", best_model_name: null, metric_name: null, metric_value: null }),
];

function renderList() {
  return renderPage(
    <Routes>
      <Route path="/automl/advanced" element={<AutoML />} />
      <Route path="/automl/advanced/:id" element={<p>run page</p>} />
    </Routes>,
    { route: "/automl/advanced" },
  );
}

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

describe("AutoML advanced list", () => {
  it("shows the empty state when there are no runs", async () => {
    mockApi({ "/automl/runs": [] });
    renderList();
    expect(await screen.findByText("No AutoML runs yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Compare" })).toBeNull();
  });

  it("lists runs with metric, status and auto-retrain badge", async () => {
    mockApi({ "/automl/runs": RUNS });
    renderList();
    const row = (await screen.findByText("Late shipments")).closest("tr")!;
    expect(within(row).getByText("auto-retrain")).toBeTruthy();
    expect(within(row).getByText("f1=0.8765")).toBeTruthy();
    const pending = screen.getByText("Pending run").closest("tr")!;
    expect(within(pending).getByText("running")).toBeTruthy();
    expect(within(pending).getAllByText("—").length).toBe(2);
    expect(screen.getByText(/Click a run to see its leaderboard/)).toBeTruthy();
  });

  it("deletes a run via DELETE /automl/runs/:id", async () => {
    const api = mockApi({ "/automl/runs": RUNS, "DELETE /automl/runs/r2": new Response(null, { status: 204 }) });
    renderList();
    const row = (await screen.findByText("Scrap rate")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/automl/runs/r2")).toBe(true));
  });

  it("opens a run from the Open button", async () => {
    mockApi({ "/automl/runs": RUNS });
    renderList();
    const row = (await screen.findByText("Scrap rate")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Open" }));
    expect(await screen.findByText("run page")).toBeTruthy();
  });

  it("compares two successful runs side by side", async () => {
    mockApi({
      "/automl/runs": RUNS,
      "/automl/runs/r1": detail({}),
      "/automl/runs/r2": detail({
        id: "r2", name: "Scrap rate", task: "regression", dataset_id: "d2", best_model_name: "xgboost",
        metric_name: "rmse", metric_value: 1.5, leaderboard: null, meta: null,
        config: { ...detail({}).config, time_budget_seconds: null, search_method: undefined },
      }),
      "/datasets": [{ id: "d1", name: "Shipments", columns: [] }],
    });
    renderList();
    await userEvent.click(await screen.findByRole("button", { name: "Compare" }));
    expect(screen.getByText("Select 2 or more successful runs above to compare.")).toBeTruthy();

    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes[2].disabled).toBe(true); // running run can't be compared
    await userEvent.click(boxes[0]);
    await userEvent.click(boxes[1]);

    expect(await screen.findByText("Run 1: Late shipments")).toBeTruthy();
    const cells = (label: string) =>
      within(screen.getByText(label, { selector: "td" }).closest("tr")!).getAllByRole("cell").slice(1).map((c) => c.textContent);
    await waitFor(() => expect(cells("Dataset")).toEqual(["Shipments", "d2"]));
    expect(cells("F1")).toEqual(["0.8000", "—"]);
    expect(cells("Class balance")).toEqual(["4:1 (handled: yes)", "—"]);
    expect(cells("Search method")).toEqual(["bayesian", "random"]);
    expect(cells("Time budget")).toEqual(["60s", "—"]);
    expect(cells("Models tried")).toEqual(["2", "—"]);
    expect(cells("Rows used")).toEqual(["1200", "—"]);

    await userEvent.click(screen.getByRole("button", { name: "Exit compare" }));
    expect(screen.queryByText("Run comparison")).toBeNull();
  });
});
