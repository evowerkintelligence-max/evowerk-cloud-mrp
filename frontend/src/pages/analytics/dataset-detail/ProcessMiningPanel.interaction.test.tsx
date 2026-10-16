// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ProcessMiningPanel } from "./ProcessMiningPanel";

const D = {
  id: "d1", name: "Event log", original_filename: "e.csv", fmt: "csv", row_count: 900, size_bytes: 1, created_at: "",
  columns: [{ name: "job", dtype: "VARCHAR" }, { name: "step", dtype: "VARCHAR" }, { name: "at", dtype: "VARCHAR" }],
  preview: [],
};

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select") as HTMLSelectElement;
}

async function mapAll() {
  await userEvent.selectOptions(field("Case/job/order ID (required)"), "job");
  await userEvent.selectOptions(field("Activity/step name (required)"), "step");
  await userEvent.selectOptions(field("Event timestamp (required)"), "at");
}

describe("ProcessMiningPanel", () => {
  it("requires all three columns, then renders summary, conformance and bottlenecks", async () => {
    const api = mockApi({
      "POST /datasets/d1/process-mining/analyze": {
        summary: { total_cases: 1500, total_activities: 4, avg_case_duration_min: 95.5, longest_bottleneck: "Paint" },
        activities: [],
        bottlenecks: [{ name: "Paint", cases: 1200, executions: 1300, avg_duration_min: 20, avg_wait_min: 42, severity: "high" }],
        conformance: { reference_sequence: ["Cut", "Weld", "Paint"], conformance_pct: 81, conforming_cases: 1215, deviating_cases: 285 },
        warnings: ["12 cases have a single event"],
      },
    });
    renderPage(<ProcessMiningPanel d={D} />);
    const run = screen.getByRole("button", { name: "Discover process" }) as HTMLButtonElement;
    await userEvent.selectOptions(field("Case/job/order ID (required)"), "job");
    expect(run.disabled).toBe(true);
    await userEvent.selectOptions(field("Activity/step name (required)"), "step");
    await userEvent.selectOptions(field("Event timestamp (required)"), "at");
    await userEvent.click(run);

    expect(await screen.findByText(/1,500 cases · 4\s+activities · avg case duration 95.5 min/)).toBeTruthy();
    expect(api.calls[0].body).toEqual({ case_column: "job", activity_column: "step", timestamp_column: "at" });
    expect(screen.getByText(/Cut → Weld → Paint\):\s+81% \(1215 conforming,\s+285 deviating\)/)).toBeTruthy();
    expect(screen.getByText("12 cases have a single event")).toBeTruthy();
    const row = screen.getByText("Paint", { selector: "td" }).closest("tr") as HTMLElement;
    expect(within(row).getByText("42")).toBeTruthy();
    expect(within(row).getByText("1,200")).toBeTruthy();
  });

  it("hides the bottleneck table when there are none", async () => {
    mockApi({
      "POST /datasets/d1/process-mining/analyze": {
        summary: { total_cases: 1, total_activities: 1, avg_case_duration_min: 0, longest_bottleneck: "—" },
        activities: [], bottlenecks: [],
        conformance: { reference_sequence: ["Cut"], conformance_pct: 100, conforming_cases: 1, deviating_cases: 0 },
        warnings: [],
      },
    });
    renderPage(<ProcessMiningPanel d={D} />);
    await mapAll();
    await userEvent.click(screen.getByRole("button", { name: "Discover process" }));
    await screen.findByText(/1 cases/);
    expect(screen.queryByText("Bottlenecks (highest avg wait time)")).toBeNull();
  });

  it("shows the server error", async () => {
    mockApi({
      "POST /datasets/d1/process-mining/analyze": new Response(JSON.stringify({ detail: "at is not a timestamp" }), { status: 422 }),
    });
    renderPage(<ProcessMiningPanel d={D} />);
    await mapAll();
    await userEvent.click(screen.getByRole("button", { name: "Discover process" }));
    expect(await screen.findByText("at is not a timestamp")).toBeTruthy();
  });
});
