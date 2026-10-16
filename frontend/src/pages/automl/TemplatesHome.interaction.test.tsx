// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { TemplatesHome } from "./TemplatesHome";

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const TEMPLATES = [
  {
    id: "scrap_prediction",
    label: "Scrap prediction",
    roles: [
      { key: "target", label: "Scrap flag", required: true },
      { key: "machine", label: "Machine", required: true },
      { key: "shift", label: "Shift", required: false },
    ],
  },
  { id: "custom_x", label: "Custom thing", roles: [{ key: "target", label: "Target", required: true }] },
];

const DATASETS = [
  {
    id: "d1", name: "Line 3", original_filename: "l.csv", fmt: "csv", row_count: 5000, size_bytes: 1, created_at: "",
    columns: [{ name: "scrap", dtype: "bool" }, { name: "press", dtype: "str" }, { name: "shift", dtype: "str" }],
  },
];

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select") as HTMLSelectElement;
}

function renderHome() {
  return renderPage(
    <Routes>
      <Route path="/automl" element={<TemplatesHome />} />
      <Route path="/automl/runs/:id" element={<p>results page</p>} />
      <Route path="/automl/advanced" element={<p>advanced page</p>} />
    </Routes>,
    { route: "/automl" },
  );
}

describe("TemplatesHome", () => {
  it("shows the empty state when no templates are registered", async () => {
    mockApi({ "/automl/templates": [] });
    renderHome();
    expect(await screen.findByText("No AutoML templates available")).toBeTruthy();
  });

  it("shows an error when templates fail to load", async () => {
    mockApi({ "/automl/templates": new Response("{}", { status: 500 }) });
    renderHome();
    expect(await screen.findByText("Failed to load AutoML templates.")).toBeTruthy();
  });

  it("navigates to the advanced page", async () => {
    mockApi({ "/automl/templates": [] });
    renderHome();
    await userEvent.click(screen.getByRole("button", { name: "Advanced / Custom" }));
    expect(await screen.findByText("advanced page")).toBeTruthy();
  });

  it("maps template roles to columns and creates a run", async () => {
    const api = mockApi({
      "/automl/templates": TEMPLATES,
      "/datasets": DATASETS,
      "POST /automl/templates/scrap_prediction/runs": { id: "r42" },
    });
    renderHome();
    expect(await screen.findByText("2 required columns to map, 1 optional")).toBeTruthy();
    expect(screen.getByText("1 required column to map")).toBeTruthy();

    await userEvent.click(screen.getByText("Scrap prediction"));
    expect(screen.getByText("Scrap prediction: dataset & columns")).toBeTruthy();
    const run = screen.getByRole("button", { name: "Run" }) as HTMLButtonElement;
    expect(run.disabled).toBe(true);

    await screen.findByRole("option", { name: "Line 3 (5,000 rows)" });
    await userEvent.selectOptions(field("Dataset"), "d1");
    await userEvent.selectOptions(field("Scrap flag"), "scrap");
    expect(run.disabled).toBe(true); // machine still unmapped
    await userEvent.selectOptions(field("Machine"), "press");
    expect(field("Shift (optional)")).toBeTruthy();
    await userEvent.type(screen.getAllByPlaceholderText("Friendly label (optional)")[1], "Press line");
    await userEvent.click(run);

    expect(await screen.findByText("results page")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      name: "Scrap prediction run",
      dataset_id: "d1",
      column_map: { target: "scrap", machine: "press" },
      config: { friendly_labels: { machine: "Press line" } },
    });
  });

  it("shows the API error and lets the user go back to the picker", async () => {
    mockApi({
      "/automl/templates": TEMPLATES,
      "/datasets": DATASETS,
      "POST /automl/templates/custom_x/runs": new Response(JSON.stringify({ detail: "Target column is constant" }), { status: 422 }),
    });
    renderHome();
    await userEvent.click(await screen.findByText("Custom thing"));
    await userEvent.type(screen.getByPlaceholderText("Custom thing run"), "Mine");
    await screen.findByRole("option", { name: "Line 3 (5,000 rows)" });
    await userEvent.selectOptions(field("Dataset"), "d1");
    await userEvent.selectOptions(field("Target"), "scrap");
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Target column is constant")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(screen.getByText("Scrap prediction")).toBeTruthy());
  });
});
