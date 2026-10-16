// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { ResultsView } from "./ResultsView";

// echarts needs canvas, which jsdom lacks.
vi.mock("@/pages/analytics/shared/EChart", () => ({ EChart: () => null }));

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const RUN = {
  id: "r1", name: "Scrap model", task: "classification", status: "success", dataset_id: "d1", target_column: "scrap",
  best_model_name: "random_forest", metric_name: "f1", metric_value: 0.8, error: null, created_at: "2026-10-01T10:00:00Z",
  started_at: null, finished_at: null, auto_retrain: false, retrained_from_run_id: null, feature_columns: ["press"],
  config: {
    thoroughness: "balanced", algorithms: null, handle_class_imbalance: true, time_column: null, horizon: 7,
    forecast_strategy: "direct", time_budget_seconds: null, search_method: "random",
  },
  leaderboard: [],
  meta: {
    explanation_sentence: "Press 4 drives most scrap.",
    recommendation: "Recalibrate press 4.",
    notes: ["ignored here"],
    scrap_summary: { scrap_rate: 0.12345, worst_machine: "Press 4", nested: { a: 1 }, missing: null, rows: 20 },
    top_machines: [{ machine_name: "Press 4", scrap_pct: 12 }, { machine_name: "Press 2", scrap_pct: 3.5 }],
    model_version: "v3",
    unset: null,
  },
};

function renderAt(run: unknown) {
  mockApi({
    "/automl/runs/r1": run,
    "/datasets": [],
    "/automl/runs/r1/explain": { top_features: [], narrative: "explain narrative" },
  });
  return renderPage(
    <Routes>
      <Route path="/automl/runs/:id" element={<ResultsView />} />
      <Route path="/automl" element={<p>guided home</p>} />
    </Routes>,
    { route: "/automl/runs/r1" },
  );
}

describe("ResultsView", () => {
  it("renders the headline and domain-specific meta sections", async () => {
    renderAt(RUN);
    expect(await screen.findByText("Press 4 drives most scrap.")).toBeTruthy();
    expect(screen.getByText("Recalibrate press 4.")).toBeTruthy();
    // flat object section
    expect(screen.getByText("Scrap Summary")).toBeTruthy();
    expect(screen.getByText("0.1235")).toBeTruthy();
    expect(screen.getByText('{"a":1}')).toBeTruthy();
    expect(screen.getByText("20")).toBeTruthy();
    // array-of-objects section renders as a table
    expect(screen.getByRole("columnheader", { name: "Machine Name" })).toBeTruthy();
    expect(screen.getByText("3.5000")).toBeTruthy();
    // scalar section
    expect(screen.getByText("Model Version")).toBeTruthy();
    expect(screen.getByText("v3")).toBeTruthy();
    // known/null keys are not rendered as sections
    expect(screen.queryByText("Notes")).toBeNull();
    expect(screen.queryByText("Unset")).toBeNull();
  });

  it("toggles the advanced panel", async () => {
    renderAt(RUN);
    await userEvent.click(await screen.findByRole("button", { name: /Show advanced/ }));
    expect(await screen.findByText("explain narrative")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Hide advanced/ }));
    expect(screen.queryByText("explain narrative")).toBeNull();
  });

  it("shows progress for a pending run and goes back to the guided home", async () => {
    renderAt({ ...RUN, status: "queued", meta: null });
    expect(await screen.findByText(/Training in progress \(queued\)/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Show advanced/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "New guided run" }));
    expect(await screen.findByText("guided home")).toBeTruthy();
  });

  it("shows the failure message, falling back when none is given", async () => {
    renderAt({ ...RUN, status: "failed", error: null });
    expect(await screen.findByText("Training failed.")).toBeTruthy();
  });

  it("shows not-found when the run can't be loaded", async () => {
    renderAt(new Response("{}", { status: 404 }));
    expect(await screen.findByText("Run not found.")).toBeTruthy();
  });
});
