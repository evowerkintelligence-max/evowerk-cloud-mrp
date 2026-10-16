// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { RunResultsPanel } from "./RunResultsPanel";
import type { AutoMLRunDetail, LeaderboardEntry } from "./api";

// echarts needs canvas, which jsdom lacks.
vi.mock("@/pages/analytics/shared/EChart", () => ({ EChart: () => null }));

const DATASETS = [
  { id: "d1", name: "Shipments", original_filename: "s.csv", fmt: "csv", row_count: 10, size_bytes: 1, created_at: "", columns: [] },
  { id: "d2", name: "October shipments", original_filename: "o.csv", fmt: "csv", row_count: 10, size_bytes: 1, created_at: "", columns: [] },
];

const entry = (over: Partial<LeaderboardEntry>): LeaderboardEntry => ({
  rank: 1, model_name: "random_forest", label: "Random forest", is_winner: true, selection_score: 0.9, primary_metric: "f1",
  holdout_scores: { f1: 0.9 }, train_seconds: 2, params: {}, full_data_fit: true,
  diagnostics: { type: "classification", labels: ["no", "yes"], confusion: [[5, 1], [2, 7]], per_class: [], truncated: false },
  ...over,
});

const RUN: AutoMLRunDetail = {
  id: "r1", name: "Late shipments", task: "classification", status: "success", dataset_id: "d1", target_column: "late",
  best_model_name: "random_forest", metric_name: "f1", metric_value: 0.9, error: null, created_at: "2026-10-01T10:00:00Z",
  started_at: null, finished_at: null, auto_retrain: false, retrained_from_run_id: null,
  feature_columns: ["qty", "carrier"],
  config: {
    thoroughness: "balanced", algorithms: null, handle_class_imbalance: true, time_column: null, horizon: 7,
    forecast_strategy: "direct", time_budget_seconds: null, search_method: "random",
  },
  leaderboard: [
    entry({}),
    entry({
      rank: 2, model_name: "linear", label: "Linear model", is_winner: false, holdout_scores: { f1: 0.81234 }, train_seconds: 1,
      diagnostics: { type: "regression", points: [[1, 1.1]], residual_std: 0.1 },
    }),
  ],
  meta: {
    notes: ["Dropped 3 rows with missing target"],
    feature_columns: ["qty", "carrier"],
    schema: { numeric_features: ["qty"], categorical_features: ["carrier"], categorical_known_values: { carrier: ["DHL", "UPS"] } },
  },
};

const EXPLAIN = { top_features: [{ feature: "qty", importance: 0.6 }, { feature: "carrier", importance: null }], narrative: "qty matters most" };

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLSelectElement;
}

describe("RunResultsPanel", () => {
  it("shows headline cards, leaderboard, narrative and notes", async () => {
    mockApi({ "/datasets": DATASETS, "/automl/runs/r1/explain": EXPLAIN });
    renderPage(<RunResultsPanel runData={RUN} />);
    expect(screen.getAllByText("f1=0.9000")).toHaveLength(2); // headline card + winner row
    const linear = screen.getByRole("button", { name: "Linear model" }).closest("tr")!;
    expect(within(linear).getByText("f1=0.8123")).toBeTruthy();
    expect(screen.getByText("winner")).toBeTruthy();
    expect(screen.getByText("Random forest diagnostics")).toBeTruthy();
    expect(await screen.findByText("qty matters most")).toBeTruthy();
    expect(screen.getByText("Dropped 3 rows with missing target")).toBeTruthy();

    await userEvent.click(within(linear).getByRole("button", { name: "Diagnostics" }));
    expect(screen.getByText("Linear model diagnostics")).toBeTruthy();
  });

  it("explains a single row with the entered feature values", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "/automl/runs/r1/explain": EXPLAIN,
      "POST /automl/runs/r1/explain-row": {
        prediction: "yes",
        probabilities: { no: 0.2, yes: 0.8 },
        base_value: 0.5,
        contributions: [{ feature: "qty", value: 40, contribution: 0.25 }, { feature: "carrier", value: "UPS", contribution: -0.1 }],
        confidence: { confidence_label: "high", margin: 0.6 },
      },
    });
    renderPage(<RunResultsPanel runData={RUN} />);
    expect(field("qty").type).toBe("number");
    await userEvent.type(field("qty"), "40");
    await userEvent.selectOptions(field("carrier"), "UPS");
    await userEvent.click(screen.getByRole("button", { name: "Predict & explain" }));

    expect(await screen.findByText("yes: 80.0%")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/automl/runs/r1/explain-row")!.body).toEqual({ row: { qty: "40", carrier: "UPS" } });
    expect(screen.getByText("high confidence")).toBeTruthy();
    expect(screen.getByText("+0.2500")).toBeTruthy();
    expect(screen.getByText("-0.1000")).toBeTruthy();
  });

  it("shows the explain-row error from the API", async () => {
    mockApi({
      "/datasets": DATASETS,
      "/automl/runs/r1/explain": EXPLAIN,
      "POST /automl/runs/r1/explain-row": new Response(JSON.stringify({ detail: "Model artifact missing" }), { status: 409 }),
    });
    renderPage(<RunResultsPanel runData={RUN} />);
    await userEvent.click(screen.getByRole("button", { name: "Predict & explain" }));
    expect(await screen.findByText("Model artifact missing")).toBeTruthy();
  });

  it("loads partial dependence for the chosen feature", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "/automl/runs/r1/explain": EXPLAIN,
      "/automl/runs/r1/partial-dependence": { feature: "qty", is_numeric: true, grid: [1, 2], response: [0.1, 0.2], rows_used: 10 },
    });
    renderPage(<RunResultsPanel runData={RUN} />);
    await userEvent.selectOptions(field("Feature"), "qty");
    await waitFor(() => expect(api.calls.some((c) => c.path === "/automl/runs/r1/partial-dependence" && c.search === "?feature=qty")).toBe(true));
  });

  it("runs a prediction and a drift check against a chosen dataset", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "/automl/runs/r1/explain": EXPLAIN,
      "POST /automl/runs/r1/predict": { preview: [{ qty: 3, prediction: "no" }], columns: ["qty", "prediction"], rows: 1, notes: [] },
      "POST /automl/runs/r1/drift-check": {
        overall_severity: "high",
        features: [{ feature: "carrier", psi: 0.31, severity: "high", type: "categorical" }],
        skipped_features: [], rows_compared: 10, train_sample_rows: 10,
      },
    });
    renderPage(<RunResultsPanel runData={RUN} />);
    const runPrediction = screen.getByRole("button", { name: "Run prediction" }) as HTMLButtonElement;
    expect(runPrediction.disabled).toBe(true);
    await screen.findAllByRole("option", { name: "October shipments" });
    await userEvent.selectOptions(field("Dataset"), "d2");
    await userEvent.click(runPrediction);
    expect(await screen.findByText("prediction")).toBeTruthy();
    expect(screen.getByText("no")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/automl/runs/r1/predict")!.body).toEqual({ dataset_id: "d2" });

    await userEvent.selectOptions(field("Compare against dataset"), "d2");
    await userEvent.click(screen.getByRole("button", { name: "Check drift" }));
    expect(await screen.findByText("overall: high")).toBeTruthy();
    expect(screen.getByText("0.31")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/automl/runs/r1/drift-check")!.body).toEqual({ dataset_id: "d2" });
  });

  it("recursive forecasts fetch a trajectory and skip row explanations", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "/automl/runs/r1/explain": EXPLAIN,
      "/automl/runs/r1/forecast-trajectory": {
        target_column: "qty", time_column: "date", steps: 1,
        points: [{ step: 1, time: "2026-10-02", prediction: 5, lower: 4, upper: 6 }],
        history: [{ time: "2026-10-01", actual: 4 }], interval: null, notes: [],
      },
    });
    const forecastRun: AutoMLRunDetail = {
      ...RUN, task: "forecasting", meta: null,
      config: { ...RUN.config, forecast_strategy: "recursive" },
      leaderboard: [entry({ diagnostics: { type: "regression", points: [[1, 1]], residual_std: 0 } })],
    };
    renderPage(<RunResultsPanel runData={forecastRun} />);
    expect(screen.getByText("Forecast trajectory")).toBeTruthy();
    expect(screen.queryByText("Why this prediction")).toBeNull();
    await waitFor(() => expect(api.calls.some((c) => c.path === "/automl/runs/r1/forecast-trajectory")).toBe(true));
    await waitFor(() => expect(screen.queryAllByText("Loading…")).toHaveLength(0));
  });

  it("shows a regression confidence range", async () => {
    mockApi({
      "/datasets": DATASETS,
      "/automl/runs/r1/explain": EXPLAIN,
      "POST /automl/runs/r1/explain-row": {
        prediction: 12.5, probabilities: null, base_value: 10, contributions: [],
        confidence: { lower: 10, upper: 15, level: 0.9, method: "conformal" },
      },
    });
    renderPage(<RunResultsPanel runData={{ ...RUN, task: "regression", meta: null }} />);
    await userEvent.click(screen.getByRole("button", { name: "Predict & explain" }));
    expect(await screen.findByText("12.5")).toBeTruthy();
    expect(screen.getByText(/90% range: 10\.0000 – 15\.0000/)).toBeTruthy();
  });
});
