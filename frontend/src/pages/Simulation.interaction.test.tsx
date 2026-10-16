// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Simulation } from "./Simulation";
import { DEFAULT_BASELINE } from "./simulation/config";

/** Inputs sit next to an un-associated <Label>; index 0 is the Baseline card,
 * 1 the Scenario card for labels that appear in both. */
function field(label: string, index = 0) {
  return screen.getAllByText(label, { selector: "label" })[index].parentElement!.querySelector<HTMLInputElement>(
    "input, select",
  )!;
}

const DATASETS = [{ id: "d1", name: "Line 3 export", original_filename: "l3.csv", fmt: "csv", row_count: 10, size_bytes: 1, created_at: "2026-01-01", columns: [] }];

const RESULT = {
  scenario_name: "Night shift",
  baseline: { daily_capacity: 1500, daily_margin: 20000, defect_rate_pct: 3.5 },
  projected: { daily_capacity: 2250, daily_margin: 28000, defect_rate_pct: 3.5 },
  delta: {
    daily_capacity: { absolute: 750, pct: 50, direction: "increase" },
    daily_margin: { absolute: 8000, pct: 40, direction: "increase" },
  },
  insights: ["Capacity now exceeds demand."],
};

const SIM = {
  id: "sim1",
  dataset_id: "d1",
  name: "Saved plan",
  description: "",
  baseline: { shifts_per_day: 3 },
  changes: { demand_change_pct: 15 },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const RUNS = [
  { id: "r1", changes: { a: 1 }, result: RESULT, created_at: "2026-02-01T10:00:00Z" },
  { id: "r2", changes: {}, result: RESULT, created_at: "2026-02-02T10:00:00Z" },
];

function base(extra: Record<string, unknown> = {}) {
  return mockApi({ "/datasets": DATASETS, "/simulation": [], ...extra });
}

beforeEach(() => {
  // PageHeader's Typewriter reads prefers-reduced-motion; jsdom has no matchMedia.
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
});
afterEach(() => vi.restoreAllMocks());

describe("Simulation", () => {
  it("disables Run until a change exists and shows the empty saved list", async () => {
    base();
    renderPage(<Simulation />);
    expect(await screen.findByText("No saved simulations yet.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run Simulation" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Enter at least one change above, or apply a preset.")).toBeTruthy();
  });

  it("derives a baseline from a dataset and shows provenance", async () => {
    const api = base({
      "POST /simulation/baseline": {
        baseline: { shifts_per_day: 3, oee_pct: 61.2 },
        provenance: { rows: 480, period_days: 20, oee_pct: 61.2 },
        assumptions: ["wage", "price"],
      },
    });
    renderPage(<Simulation />);
    await screen.findByRole("option", { name: "Line 3 export" });
    expect(screen.getByRole("button", { name: "Derive baseline" }).hasAttribute("disabled")).toBe(true);
    await userEvent.selectOptions(field("Load baseline from dataset"), "d1");
    await userEvent.click(screen.getByRole("button", { name: "Derive baseline" }));

    expect(await screen.findByText(/Derived from 480 rows over 20 day\(s\), OEE 61.2%. Assumed: wage, price./)).toBeTruthy();
    expect(field("Shifts / day").value).toBe("3");
    expect(field("OEE (%)").value).toBe("61.2");
    expect(api.calls.find((c) => c.path === "/simulation/baseline")!.body).toEqual({ dataset_id: "d1" });
  });

  it("shows the baseline derivation error", async () => {
    base({ "POST /simulation/baseline": new Response(JSON.stringify({ detail: "Dataset has no OEE columns" }), { status: 422 }) });
    renderPage(<Simulation />);
    await screen.findByRole("option", { name: "Line 3 export" });
    await userEvent.selectOptions(field("Load baseline from dataset"), "d1");
    await userEvent.click(screen.getByRole("button", { name: "Derive baseline" }));
    expect(await screen.findByText("Dataset has no OEE columns")).toBeTruthy();
  });

  it("runs an ad-hoc scenario with a preset plus edited fields and renders the result", async () => {
    const api = base({ "POST /simulation/run": RESULT });
    renderPage(<Simulation />);
    await screen.findByText("No saved simulations yet.");

    // a numeric baseline edit sticks, a non-numeric one is ignored
    fireEvent.change(field("Workers / shift"), { target: { value: "30" } });
    fireEvent.change(field("Workers / shift"), { target: { value: "" } });
    expect(field("Workers / shift").value).toBe("30");
    await userEvent.clear(field("Scenario name"));
    await userEvent.type(field("Scenario name"), "Night shift");
    await userEvent.click(screen.getByRole("button", { name: "Add a Shift" }));
    expect(field("Shifts / day", 1).value).toBe("3");
    await userEvent.type(field("Demand change (%)"), "12.5");
    // blanking a change field drops the key
    await userEvent.type(field("OEE change (pp)"), "4");
    await userEvent.clear(field("OEE change (pp)"));

    await userEvent.click(screen.getByRole("button", { name: "Run Simulation" }));
    expect(await screen.findByText("Capacity now exceeds demand.")).toBeTruthy();
    expect(screen.getByText("Night shift", { selector: ":not(input)" })).toBeTruthy();
    expect(screen.getByText("Daily Capacity (units)")).toBeTruthy();
    expect(screen.getByText("$28,000")).toBeTruthy();
    expect(screen.getByText(/↑ 750 \(\+50.0%\)/)).toBeTruthy();
    // a metric with no delta entry falls back to unchanged
    expect(screen.getByText(/→ 0.0% \(0.0%\)/)).toBeTruthy();

    expect(api.calls.find((c) => c.path === "/simulation/run")!.body).toEqual({
      scenario_name: "Night shift",
      baseline: { ...DEFAULT_BASELINE, workers_per_shift: 30 },
      changes: { shifts_per_day: 3, demand_change_pct: 12.5 },
    });
  });

  it("Clear wipes the changes", async () => {
    base();
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "Demand +20%" }));
    expect(field("Demand change (%)").value).toBe("20");
    expect(screen.getByRole("button", { name: "Run Simulation" }).hasAttribute("disabled")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(field("Demand change (%)").value).toBe("");
    expect(screen.getByRole("button", { name: "Run Simulation" }).hasAttribute("disabled")).toBe(true);
  });

  it("shows the run error from the server", async () => {
    base({ "POST /simulation/run": new Response(JSON.stringify({ detail: "Invalid baseline" }), { status: 400 }) });
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "OEE +5 pp" }));
    await userEvent.click(screen.getByRole("button", { name: "Run Simulation" }));
    expect(await screen.findByText("Invalid baseline")).toBeTruthy();
  });

  it("saves a simulation, then runs it to history and with a confidence range", async () => {
    const mcResult = {
      ...RESULT,
      scenario_name: "MC",
      bands: { daily_capacity: { p10: 2000, p50: 2250, p90: 2400 } },
      variance_source: "default",
      sensitivity: {
        target_metric: "good_units_per_day",
        ranked: [
          { parameter: "oee_pct", baseline_value: 75, low_value: 70, high_value: 80, impact: 120 },
          { parameter: "mystery_param", baseline_value: 1, low_value: 0, high_value: 2, impact: 60 },
        ],
      },
    };
    const api = base({
      "POST /simulation": { ...SIM, id: "new1", name: "What-if Scenario" },
      "PUT /simulation/new1": {},
      "POST /simulation/new1/run": { id: "r9", changes: {}, result: { ...RESULT, scenario_name: "Saved run" }, created_at: "" },
      "POST /simulation/new1/run/monte-carlo": { id: "r10", changes: {}, result: mcResult, created_at: "" },
    });
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "Halve Defect Rate" }));
    await userEvent.click(screen.getByRole("button", { name: "Save simulation" }));
    expect(await screen.findByRole("button", { name: "Save as new" })).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/simulation")!.body).toEqual({
      name: "What-if Scenario",
      description: "",
      baseline: DEFAULT_BASELINE,
      changes: { defect_change_pct: -1.75 },
    });

    await userEvent.click(screen.getByRole("button", { name: "Run & save to history" }));
    expect(await screen.findByText("Saved run")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "PUT")!.body).toEqual({
      name: "What-if Scenario",
      baseline: DEFAULT_BASELINE,
      changes: { defect_change_pct: -1.75 },
    });

    await userEvent.selectOptions(screen.getByDisplayValue("Daily Margin"), "good_units_per_day");
    await userEvent.click(screen.getByRole("button", { name: "Run with confidence range" }));
    expect(await screen.findByText("Confidence range: default estimate (no linked dataset)")).toBeTruthy();
    expect(screen.getByText("Range (P10–P90)")).toBeTruthy();
    expect(screen.getByText("2,000 – 2,400")).toBeTruthy();
    expect(screen.getByText("What moves Good Units/Day the most")).toBeTruthy();
    expect(screen.getByText("OEE (%)", { selector: "span" })).toBeTruthy();
    expect(screen.getByText("mystery_param")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/simulation/new1/run/monte-carlo")!.body).toEqual({
      changes: {},
      target_metric: "good_units_per_day",
    });
  });

  it("surfaces save and run-and-save errors", async () => {
    base({
      "POST /simulation": SIM,
      "PUT /simulation/sim1": () => new Response(JSON.stringify({ detail: "Simulation locked" }), { status: 409 }),
    });
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "Save simulation" }));
    await userEvent.click(await screen.findByRole("button", { name: "Run & save to history" }));
    expect(await screen.findByText("Simulation locked")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Run with confidence range" }));
    await waitFor(() => expect(screen.getAllByText("Simulation locked")).toHaveLength(2));
  });

  it("shows a generic error when save fails without detail", async () => {
    base({ "POST /simulation": new Response("oops", { status: 500 }) });
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "Save simulation" }));
    expect(await screen.findByText("Request failed")).toBeTruthy();
  });

  it("loads a saved simulation into the form", async () => {
    base({ "/simulation": [SIM] });
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "Load" }));
    expect(field("Scenario name").value).toBe("Saved plan");
    expect(field("Shifts / day").value).toBe("3");
    expect(field("Demand change (%)").value).toBe("15");
    expect(field("Load baseline from dataset").value).toBe("d1");
    expect(screen.getByRole("button", { name: "Save as new" })).toBeTruthy();
  });

  it("expands runs, compares two of them, and shows the delta table", async () => {
    const api = base({
      "/simulation": [SIM],
      "/simulation/sim1/runs": RUNS,
      "POST /simulation/compare": {
        run_a_id: "r1",
        run_b_id: "r2",
        delta: {
          daily_margin: { absolute: -500, pct: -2.5, direction: "decrease" },
          defect_rate_pct: { absolute: 0, pct: 0, direction: "unchanged" },
        },
      },
    });
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "View runs" }));
    expect(await screen.findByText(/1 change\(s\)/)).toBeTruthy();
    expect(screen.getByText(/0 change\(s\)/)).toBeTruthy();
    const compareBtn = screen.getByRole("button", { name: "Compare selected runs" });
    expect(compareBtn.hasAttribute("disabled")).toBe(true);

    const [a1, a2] = screen.getAllByRole("radio", { name: "A" });
    const [b1, b2] = screen.getAllByRole("radio", { name: "B" });
    await userEvent.click(a1);
    await userEvent.click(b1);
    expect(screen.getByText("Pick two different runs.")).toBeTruthy();
    expect(compareBtn.hasAttribute("disabled")).toBe(true);
    await userEvent.click(b2);
    expect(a2).toBeTruthy();
    await userEvent.click(compareBtn);

    expect(await screen.findByText("Run comparison")).toBeTruthy();
    expect(screen.getByText(/↓ \$-500 \(-2.5%\)/)).toBeTruthy();
    expect(screen.getByText(/→ 0.0% \(0.0%\)/)).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/simulation/compare")!.body).toEqual({ run_a_id: "r1", run_b_id: "r2" });

    // collapsing clears the selection
    await userEvent.click(screen.getByRole("button", { name: "Hide runs" }));
    await userEvent.click(screen.getByRole("button", { name: "View runs" }));
    await screen.findByText(/1 change\(s\)/);
    expect(screen.getByRole("button", { name: "Compare selected runs" }).hasAttribute("disabled")).toBe(true);
  });

  it("shows a compare error and the no-runs state", async () => {
    base({
      "/simulation": [SIM, { ...SIM, id: "sim2", name: "Empty plan", description: "nothing yet" }],
      "/simulation/sim1/runs": RUNS,
      "/simulation/sim2/runs": [],
      "POST /simulation/compare": new Response(JSON.stringify({ detail: "Runs belong to different simulations" }), { status: 400 }),
    });
    renderPage(<Simulation />);
    const rows = await screen.findAllByRole("button", { name: "View runs" });
    await userEvent.click(rows[0]);
    await screen.findByText(/1 change\(s\)/);
    await userEvent.click(screen.getAllByRole("radio", { name: "A" })[0]);
    await userEvent.click(screen.getAllByRole("radio", { name: "B" })[1]);
    await userEvent.click(screen.getByRole("button", { name: "Compare selected runs" }));
    expect(await screen.findByText("Runs belong to different simulations")).toBeTruthy();

    expect(screen.getByText("nothing yet")).toBeTruthy();
    await userEvent.click(screen.getAllByRole("button", { name: "View runs" })[0]);
    expect(await screen.findByText("No runs yet.")).toBeTruthy();
  });

  it("deletes a simulation, and shows a delete failure", async () => {
    const api = base({
      "/simulation": [SIM, { ...SIM, id: "sim2", name: "Other" }],
      "/simulation/sim1/runs": [],
      "DELETE /simulation/sim1": new Response(null, { status: 204 }),
      "DELETE /simulation/sim2": new Response(JSON.stringify({ detail: "In use by a report" }), { status: 409 }),
    });
    renderPage(<Simulation />);
    await screen.findByText("Saved plan");
    await userEvent.click(screen.getAllByRole("button", { name: "Load" })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: "View runs" })[0]);
    await screen.findByText("No runs yet.");
    await userEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    // deleting the loaded + expanded sim resets both
    await waitFor(() => expect(screen.getByRole("button", { name: "Save simulation" })).toBeTruthy());
    expect(screen.queryByText("No runs yet.")).toBeNull();
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/simulation/sim1")).toBe(true);

    await userEvent.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    expect(await screen.findByText("In use by a report")).toBeTruthy();
  });

  it("downloads a run export as a file", async () => {
    const createObjectURL = vi.fn(() => "blob:run");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const api = base({
      "/simulation": [SIM],
      "/simulation/sim1/runs": RUNS.slice(0, 1),
      "/simulation/sim1/runs/r1/export": () => new Response("a,b\n1,2", { status: 200 }),
    });
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "View runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "XLSX" }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const anchor = click.mock.contexts[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("simulation-run.xlsx");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:run");
    expect(api.calls.find((c) => c.path.endsWith("/export"))!.search).toBe("?fmt=xlsx");
  });

  it("sends a result to the report inbox", async () => {
    const api = base({ "POST /simulation/run": RESULT, "POST /reports/inbox": {} });
    renderPage(<Simulation />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a Shift" }));
    await userEvent.click(screen.getByRole("button", { name: "Run Simulation" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send to report" }));
    expect(await screen.findByText("Sent to report inbox.")).toBeTruthy();
    const body = api.calls.find((c) => c.path === "/reports/inbox")!.body as { type: string; text: string };
    expect(body.type).toBe("text");
    expect(body.text).toContain("Daily Capacity (units): 1,500 → 2,250");
  });
});
