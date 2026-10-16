// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { AutoMLRun } from "./AutoMLRun";

// echarts needs canvas, which jsdom lacks.
vi.mock("@/pages/analytics/shared/EChart", () => ({ EChart: () => null }));

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const DATASETS = [
  {
    id: "d1", name: "Shipments", original_filename: "s.csv", fmt: "csv", row_count: 1200, size_bytes: 1, created_at: "",
    columns: [{ name: "date", dtype: "date" }, { name: "qty", dtype: "int" }, { name: "late", dtype: "bool" }],
  },
];

const CONFIG = {
  thoroughness: "balanced", algorithms: null, handle_class_imbalance: true, time_column: null, horizon: 7,
  forecast_strategy: "direct", time_budget_seconds: null, search_method: "random",
};

const RUN = {
  id: "r1", name: "Late shipments", task: "classification", status: "success", dataset_id: "d1", target_column: "late",
  best_model_name: "random_forest", metric_name: "f1", metric_value: 0.9, error: null, created_at: "2026-10-01T10:00:00Z",
  started_at: null, finished_at: null, auto_retrain: false, retrained_from_run_id: null,
  feature_columns: ["qty", "carrier"], config: CONFIG, meta: null,
  leaderboard: [
    {
      rank: 1, model_name: "random_forest", label: "Random forest", is_winner: true, selection_score: 0.9, primary_metric: "f1",
      holdout_scores: { f1: 0.9 }, train_seconds: 2, params: {}, full_data_fit: true,
      diagnostics: { type: "classification", labels: ["no", "yes"], confusion: [[5, 1], [2, 7]], per_class: [], truncated: false },
    },
  ],
};

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLSelectElement;
}

function renderAt(route: string) {
  return renderPage(
    <Routes>
      <Route path="/automl/advanced/:id" element={<AutoMLRun />} />
    </Routes>,
    { route },
  );
}

describe("AutoMLRun new run form", () => {
  it("requires a dataset and target before training", async () => {
    const api = mockApi({ "/datasets": DATASETS, "/automl/algorithms": [] });
    renderAt("/automl/advanced/new");
    await userEvent.click(await screen.findByRole("button", { name: "Train" }));
    expect(screen.getByText("Pick a dataset and a target column.")).toBeTruthy();
    expect(api.calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("posts the configured classification run and opens it", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "/automl/algorithms": [{ id: "xgboost", label: "XGBoost" }, { id: "logreg", label: "Logistic" }],
      "POST /automl/runs": { ...RUN, id: "r9", name: "My run", status: "queued" },
      "/automl/runs/r9": { ...RUN, id: "r9", name: "My run", status: "queued" },
    });
    renderAt("/automl/advanced/new");
    await screen.findByRole("option", { name: "Shipments (1,200 rows)" });

    await userEvent.type(screen.getByPlaceholderText("e.g. Predict late shipments"), "My run");
    await userEvent.selectOptions(field("Dataset"), "d1");
    await userEvent.selectOptions(field("Target column"), "late");
    await userEvent.click(screen.getByRole("checkbox", { name: "qty" }));
    await userEvent.selectOptions(field("Thoroughness"), "thorough");
    await userEvent.type(field("Time budget (seconds)"), "120");
    await userEvent.selectOptions(field("Search method"), "bayesian");
    await userEvent.click(screen.getByRole("checkbox", { name: /Handle class imbalance/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Auto-retrain on drift" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "xgboost" }));
    // target column is not offered as a feature
    expect(screen.queryByRole("checkbox", { name: "late" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Train" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      name: "My run", dataset_id: "d1", task: "classification", target_column: "late", feature_columns: ["qty"],
      thoroughness: "thorough", algorithms: ["xgboost"], handle_class_imbalance: false, time_column: null, horizon: 7,
      forecast_strategy: "direct", time_budget_seconds: 120, search_method: "bayesian", auto_retrain: true,
    });
    expect(await screen.findByText(/Training in progress \(queued\)/)).toBeTruthy();
  });

  it("forecasting needs a time column, then sends horizon and strategy", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "/automl/algorithms": [],
      "POST /automl/runs": new Response(JSON.stringify({ detail: "Dataset too small" }), { status: 400 }),
    });
    renderAt("/automl/advanced/new");
    await screen.findByRole("option", { name: "Shipments (1,200 rows)" });
    await userEvent.selectOptions(field("Dataset"), "d1");
    await userEvent.selectOptions(field("Task"), "forecasting");
    await userEvent.selectOptions(field("Target column"), "qty");
    await waitFor(() => expect(api.calls.some((c) => c.search === "?task=forecasting")).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Train" }));
    expect(screen.getByText("Forecasting needs a time column.")).toBeTruthy();

    // time column choices exclude the target
    expect(within(field("Time column")).queryByRole("option", { name: "qty" })).toBeNull();
    await userEvent.selectOptions(field("Time column"), "date");
    // Clearing snaps the field back to 1 (`Number(v) || 1`), so set the value in one change.
    fireEvent.change(field("Horizon (steps ahead)"), { target: { value: "14" } });
    await userEvent.selectOptions(field("Strategy"), "recursive");
    await userEvent.click(screen.getByRole("button", { name: "Train" }));

    expect(await screen.findByText("Dataset too small")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST")!.body).toMatchObject({
      name: "forecasting run", task: "forecasting", target_column: "qty", time_column: "date", horizon: 14,
      forecast_strategy: "recursive", algorithms: null, feature_columns: [], time_budget_seconds: null,
    });
  });
});

describe("AutoMLRun existing run", () => {
  it("shows the training error for a failed run", async () => {
    mockApi({ "/datasets": [], "/automl/algorithms": [], "/automl/runs/r1": { ...RUN, status: "failed", error: "Target has one class" } });
    renderAt("/automl/advanced/r1");
    expect(await screen.findByText("Target has one class")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  });

  it("links back to the run it was retrained from", async () => {
    mockApi({ "/datasets": [], "/automl/algorithms": [], "/automl/runs/r1": { ...RUN, status: "running", retrained_from_run_id: "r0" } });
    renderAt("/automl/advanced/r1");
    expect(await screen.findByText(/Training in progress \(running\)/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "an earlier run" }).getAttribute("href")).toBe("/automl/advanced/r0");
  });

  it("renders results for a successful run", async () => {
    mockApi({
      "/datasets": [],
      "/automl/algorithms": [],
      "/automl/runs/r1": RUN,
      "/automl/runs/r1/explain": { top_features: [{ feature: "qty", importance: 0.5 }], narrative: "qty drives lateness" },
    });
    renderAt("/automl/advanced/r1");
    expect(await screen.findByRole("heading", { name: "Late shipments" })).toBeTruthy();
    expect(await screen.findByText("qty drives lateness")).toBeTruthy();
    expect(screen.getAllByText("Leaderboard")).toHaveLength(1);
  });
});
